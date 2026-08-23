from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    asset_request_id = fields.Many2one(
        "asset.request",
        string="Asset Request",
        ondelete="set null",
        copy=False,
        index=True,
        help="The Asset Request that originated this Purchase Order.",
    )
    asset_list_ids = fields.One2many(
        "asset.list",
        "po_id",
        string="Asset Inventory Rows",
    )
    asset_list_count = fields.Integer(
        string="Asset Rows",
        compute="_compute_asset_list_count",
    )

    @api.depends("asset_list_ids")
    def _compute_asset_list_count(self):
        for rec in self:
            rec.asset_list_count = len(rec.asset_list_ids)

    # ----------------------------------------------------------------
    # Constraint: total ordered quantity across all (non-cancelled) POs
    # of the same Asset Request must not exceed the requested quantity.
    #
    # Examples:
    #   - Request: Laptop x 1  →  POs may have total qty = 1 (not 2, not 0.5)
    #   - Request: Laptop x 10 →  POs may have total qty up to 10 (e.g.
    #                              one PO with 10, or two POs with 6 + 4)
    #
    # The check runs at the PO level (not the line level) because we need
    # to consider all sibling POs, not just self. Section/note lines
    # (display_type set) are excluded.
    # ----------------------------------------------------------------
    @api.constrains("order_line", "order_line.product_qty", "state", "asset_request_id")
    def _check_qty_against_asset_request(self):
        for po in self:
            request = po.asset_request_id
            if not request:
                continue
            # Cancelled POs are not enforced (they're being thrown away)
            if po.state == "cancel":
                continue

            # Total requested = sum of request.line_ids.quantity
            requested_qty = sum(int(rl.quantity or 0) for rl in request.line_ids)
            if requested_qty <= 0:
                continue  # nothing to enforce yet

            # Total ordered = sum across all COMMITTED POs of the same request.
            #
            # Only 'purchase' (confirmed) and 'done' (locked) count. Draft and
            # sent POs are RFQs - competing quotes, not commitments. The vendor
            # comparison flow deliberately raises one RFQ per vendor for the
            # SAME quantity (3 vendors x 7 units = 21 across RFQs), expecting
            # to award only one and cancel the rest. Counting RFQs here made
            # any multi-vendor tender impossible to save, which is exactly the
            # scenario the comparison step exists to support.
            #
            # Over-ordering is still caught: it is blocked at the moment a
            # second PO is CONFIRMED, which is the point where real money is
            # actually committed.
            ordered_qty = 0.0
            for sibling_po in request.purchase_order_ids:
                if sibling_po.state not in ("purchase", "done"):
                    continue
                for line in sibling_po.order_line:
                    if line.display_type:  # skip section / note rows
                        continue
                    ordered_qty += line.product_qty or 0.0

            # Convert to int for clean comparison (avoid float-equality issues
            # when user types whole numbers).
            ordered_qty_int = int(round(ordered_qty))

            if ordered_qty_int > requested_qty:
                raise ValidationError(_(
                    "Cannot confirm this Purchase Order.\n\n"
                    "The Asset Request %(req_name)s is for %(requested)d unit(s), "
                    "but the total on CONFIRMED Purchase Orders would become "
                    "%(ordered)d.\n\n"
                    "Draft RFQs are not counted - only confirmed orders. "
                    "Either reduce the quantity on this PO, or cancel one of "
                    "the other confirmed Purchase Orders linked to this request."
                ) % {
                    "req_name": request.name,
                    "requested": requested_qty,
                    "ordered": ordered_qty_int,
                })

    # ----------------------------------------------------------------
    # When a PO linked to an asset.request is saved for the first time,
    # advance the request's state from 'approved' to 'po_created'.
    # This used to happen server-side in asset_request.action_create_po(),
    # but we now open the form in create mode (because partner_id is
    # mandatory in Odoo 17) so we advance state on the actual save instead.
    # ----------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # A newly created purchase.order is a DRAFT RFQ, not an order. Under
        # the vendor_management flow it may sit in draft for days while
        # vendors quote, so advancing the request to 'po_created' here would
        # be wrong - it is not a PO yet. The request moves to 'rfq' when the
        # user raises it, and to 'po_created' only once the order is actually
        # confirmed (see write() below).
        #
        # Direct-PO creation still needs covering: if a PO is created already
        # confirmed, treat it as committed straight away.
        confirmed = records.filtered(lambda p: p.state in ("purchase", "done"))
        requests_to_advance = confirmed.mapped("asset_request_id").filtered(
            lambda r: r.state in ("approved", "rfq")
        )
        if requests_to_advance:
            requests_to_advance.write({"state": "po_created"})
        return records

    # ----------------------------------------------------------------
    # Trigger the asset.request auto-transition when a PO state changes.
    #
    # The previous design only triggered on state='done'. We now trigger
    # on any state change to also handle cancellations correctly — a
    # cancelled PO should let the request advance if it was the only
    # PO still holding it back.
    # ----------------------------------------------------------------
    def write(self, vals):
        res = super().write(vals)
        if "state" in vals:
            requests = self.mapped("asset_request_id")
            if requests:
                # An RFQ becoming a real order is what moves the request on.
                # Under vendor_management an RFQ can sit in draft/sent for
                # days while vendors quote, so confirmation - not creation -
                # is the honest signal that something was actually ordered.
                confirmed_requests = self.filtered(
                    lambda p: p.state in ("purchase", "done")
                ).mapped("asset_request_id").filtered(
                    lambda r: r.state in ("approved", "rfq"))
                if confirmed_requests:
                    confirmed_requests.write({"state": "po_created"})
                requests._check_and_advance_to_po_done()
        return res

    # ----------------------------------------------------------------
    # Smart button -> back to the originating request
    # ----------------------------------------------------------------
    def action_open_asset_request(self):
        self.ensure_one()
        if not self.asset_request_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Asset Request"),
            "res_model": "asset.request",
            "view_mode": "form",
            "res_id": self.asset_request_id.id,
            "target": "current",
        }

class StockPickingAssetRequest(models.Model):
    """Advance the asset request when goods physically arrive.

    _check_and_advance_to_po_done() was only ever called from
    purchase_order.write() when the PO's own state changed. But validating a
    receipt changes the stock.picking state, NOT the purchase.order state -
    so the check never ran at the one moment that actually matters, and
    requests sat in 'po_created' after everything had been delivered.

    Hooking the picking is what makes both of the intended cases work:
      - one vendor supplies everything: that PO's receipt is validated, the
        request advances.
      - two vendors supply different products: each receipt validation
        re-runs the check, and it only passes once every committed PO has
        all of its incoming pickings done.
    """
    _inherit = "stock.picking"

    def write(self, vals):
        res = super().write(vals)
        if "state" in vals:
            self._advance_linked_asset_requests()
        return res

    def button_validate(self):
        # write() above covers the normal path, but button_validate() can
        # complete through backorder wizards and other routes where the
        # final state write does not pass through this recordset. Calling it
        # here as well is harmless - the advance method is idempotent, since
        # it returns immediately unless the request is still in po_created.
        res = super().button_validate()
        self._advance_linked_asset_requests()
        return res

    def _advance_linked_asset_requests(self):
        requests = self.env["asset.request"]
        for picking in self:
            # purchase_id is set by purchase_stock on receipts generated
            # from a PO. Pickings unrelated to purchasing are skipped.
            po = picking.purchase_id
            if po and po.asset_request_id:
                requests |= po.asset_request_id
        if requests:
            requests._check_and_advance_to_po_done()