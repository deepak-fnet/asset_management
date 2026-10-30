from odoo import api, fields, models, _


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
    # No cap on the ordered quantity: purchase may buy more than the request
    # asked for (pack sizes, minimum order, spares). Extra units stay in stock.

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
                requests._check_and_advance_to_done()
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

    _check_and_advance_to_done() was only ever called from
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
            requests._check_and_advance_to_done()