from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class AssetRequest(models.Model):
    _name = "asset.request"
    _description = "Asset Purchase Request"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"
    _rec_name = "name"

    # ----------------------------------------------------------------
    # Identification
    # ----------------------------------------------------------------
    name = fields.Char(
        string="Reference",
        required=True,
        readonly=True,
        copy=False,
        default=lambda self: _("New"),
        tracking=True,
    )
    requested_by = fields.Many2one(
        "res.users",
        string="Requested By",
        required=True,
        default=lambda self: self.env.user,
        tracking=True,
    )
    request_date = fields.Date(
        string="Request Date",
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    notes = fields.Text(string="Notes")

    # ----------------------------------------------------------------
    # Workflow
    # ----------------------------------------------------------------
    # NEW STATE FLOW:
    #   draft → submitted → approved → po_created → po_done → done
    #
    # po_created → po_done is AUTOMATIC: triggered when every non-cancelled
    #   PO linked to this request reaches state 'done' (Locked). At that
    #   moment asset.list rows are auto-generated, one per ordered unit.
    #
    # po_done → done is MANUAL: user fills serial numbers on the visible
    #   "Asset List" notebook page, then clicks "Done". Validation requires
    #   every row to have a non-empty serial_no.
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("submitted", "Submitted"),
            ("approved", "Approved"),
            ("rfq", "RFQ Sent"),
            ("po_created", "PO Created"),
            ("po_done", "PO Done"),
            ("done", "Done"),
            ("cancel", "Cancelled"),
        ],
        string="Status",
        default="draft",
        tracking=True,
        readonly=True,
        copy=False,
    )

    # ----------------------------------------------------------------
    # Relations
    # ----------------------------------------------------------------
    line_ids = fields.One2many(
        "asset.request.line",
        "request_id",
        string="Request Lines",
        copy=True,
    )
    purchase_order_ids = fields.One2many(
        "purchase.order",
        "asset_request_id",
        string="Purchase Orders",
        copy=False,
    )
    asset_list_ids = fields.One2many(
        "asset.list",
        "request_id",
        string="Asset Inventory",
        copy=False,
    )
    rfq_ids = fields.One2many(
        "purchase.order",
        "asset_request_id",
        string="RFQs",
        domain=[("state", "in", ("draft", "sent"))],
        copy=False,
    )
    rfq_count = fields.Integer(
        string="RFQs",
        compute="_compute_rfq_count",
    )

    @api.depends("purchase_order_ids.state")
    def _compute_rfq_count(self):
        for rec in self:
            rec.rfq_count = len(rec.purchase_order_ids.filtered(
                lambda p: p.state in ("draft", "sent")))

    # ----------------------------------------------------------------
    # Computed counters / smart-button helpers
    # ----------------------------------------------------------------
    po_count = fields.Integer(
        string="POs",
        compute="_compute_po_count",
    )
    asset_list_count = fields.Integer(
        string="Asset List Count",
        compute="_compute_asset_list_count",
    )
    asset_list_total = fields.Integer(
        string="Asset List Expected",
        compute="_compute_asset_list_count",
        help="Total units expected across all POs (sum of PO line qty).",
    )
    asset_list_filled = fields.Integer(
        string="Asset List Filled",
        compute="_compute_asset_list_count",
        help="How many asset.list rows already have a serial number.",
    )
    all_pos_done = fields.Boolean(
        string="All POs Done",
        compute="_compute_all_pos_done",
        help="True when at least one non-cancelled PO exists and "
             "every non-cancelled PO has state == 'done'.",
    )

    @api.depends("purchase_order_ids")
    def _compute_po_count(self):
        for rec in self:
            rec.po_count = len(rec.purchase_order_ids)

    @api.depends("asset_list_ids", "asset_list_ids.serial_no",
                 "purchase_order_ids.order_line.product_qty")
    def _compute_asset_list_count(self):
        for rec in self:
            rec.asset_list_count = len(rec.asset_list_ids)
            rec.asset_list_total = int(sum(
                pol.product_qty for po in rec.purchase_order_ids for pol in po.order_line
            ))
            rec.asset_list_filled = len(rec.asset_list_ids.filtered(lambda a: a.serial_no))

    @api.depends("purchase_order_ids.state")
    def _compute_all_pos_done(self):
        for rec in self:
            # Cancelled POs are ignored entirely — they don't block the flow.
            relevant = rec.purchase_order_ids.filtered(lambda p: p.state != "cancel")
            rec.all_pos_done = bool(relevant) and all(po.state == "done" for po in relevant)

    # ----------------------------------------------------------------
    # CRUD
    # ----------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("asset.request") or _("New")
        return super().create(vals_list)

    # ----------------------------------------------------------------
    # Constraints
    # ----------------------------------------------------------------
    @api.constrains("line_ids")
    def _check_line_ids(self):
        for rec in self:
            if rec.state != "draft" and not rec.line_ids:
                raise ValidationError(_("Asset Request must have at least one line."))

    # ----------------------------------------------------------------
    # Workflow actions
    # ----------------------------------------------------------------
    def action_submit(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only Draft requests can be submitted."))
            if not rec.line_ids:
                raise UserError(_("Add at least one request line before submitting."))
            rec.state = "submitted"

    def action_approve(self):
        for rec in self:
            if rec.state != "submitted":
                raise UserError(_("Only Submitted requests can be approved."))
            rec.state = "approved"

    def action_reject(self):
        for rec in self:
            if rec.state not in ("submitted", "approved"):
                raise UserError(_("Only Submitted or Approved requests can be rejected."))
            rec.state = "cancel"

    def action_cancel(self):
        for rec in self:
            if rec.state in ("done", "cancel"):
                raise UserError(_("Cannot cancel a request that is already Done or Cancelled."))
            rec.state = "cancel"

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state != "cancel":
                raise UserError(_("Only Cancelled requests can be reset to Draft."))
            rec.state = "draft"

    def action_create_rfq(self):
        """Raise ONE RFQ from this request, ready to go out to several vendors.

        Replaces the old vendor-comparison route, which created a separate
        purchase.order per vendor before anyone had quoted. That made every
        competing quote look like a real order - it inflated committed
        quantity, and each one had to be cancelled by hand afterwards.

        Here a single RFQ is created instead. vendor_management's vendor_ids
        holds the vendors to approach, and its action_send_to_vendors()
        creates one vendor.quote per vendor against this one RFQ. Only when a
        vendor is finally selected does the RFQ become a real order.

        Request lines carry an asset.category, not a product - the request is
        raised before anyone knows which exact model will be bought - so the
        buyer still has to set the products on the RFQ before sending it out.
        """
        self.ensure_one()
        if self.state not in ("approved", "rfq"):
            raise UserError(_(
                "An RFQ can only be raised from an Approved request."))
        if not self.line_ids:
            raise UserError(_("This request has no lines to quote."))

        if self.state == "approved":
            self.state = "rfq"

        self.message_post(body=_(
            "Raising an RFQ for %s line(s). Add the products and the vendors "
            "to approach, then use Send to Vendors."
        ) % len(self.line_ids))

        action = {
            "type": "ir.actions.act_window",
            "name": _("New RFQ"),
            "res_model": "purchase.order",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_asset_request_id": self.id,
                "default_origin": self.name,
                "form_view_initial_mode": "edit",
            },
        }
        form_view = self.env.ref("asset_management.view_asset_po_form",
                                 raise_if_not_found=False)
        if form_view:
            action["views"] = [(form_view.id, "form")]
        return action

    def action_open_rfqs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("RFQs"),
            "res_model": "purchase.order",
            "view_mode": "list,form",
            "domain": [("asset_request_id", "=", self.id),
                       ("state", "in", ("draft", "sent"))],
            "context": {"default_asset_request_id": self.id},
        }

    def action_create_po(self):
        """Create a PO by hand, bypassing the RFQ/quotation round.

        Kept for the cases quoting cannot serve - a single-source item, an
        urgent replacement, a contracted rate that needs no quoting. The
        state transition still happens in purchase_order.create().
        """
        self.ensure_one()
        if self.state not in ("approved", "rfq", "po_created"):
            raise UserError(_(
                "You can only create POs from an Approved request."))

        action = {
            "type": "ir.actions.act_window",
            "name": _("New Purchase Order"),
            "res_model": "purchase.order",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_asset_request_id": self.id,
                "default_origin": self.name,
                # Force form to open in create mode
                "form_view_initial_mode": "edit",
            },
        }
        # Same defensive lookup as action_open_pos: never let a missing
        # external ID take down the button. The old code also passed
        # form_view_ref="asset_request.view_asset_po_form" - the wrong module
        # name entirely, since these views live in asset_management. That
        # silently resolved to nothing.
        form_view = self.env.ref("asset_management.view_asset_po_form",
                                 raise_if_not_found=False)
        if form_view:
            action["views"] = [(form_view.id, "form")]
        return action

    def action_done(self):
        """Manual transition from 'po_done' to 'done'.

        Validates every asset.list row has a non-empty serial_no.
        If any are blank → block with a clear error.
        """
        for rec in self:
            if rec.state != "po_done":
                raise UserError(_(
                    "The request must be in 'PO Done' state before it can be marked as Done. "
                    "Current state: %s"
                ) % dict(rec._fields["state"].selection).get(rec.state, rec.state))

            if not rec.asset_list_ids:
                raise UserError(_(
                    "No asset inventory rows exist. This shouldn't normally happen — "
                    "please contact your administrator."
                ))

            missing = rec.asset_list_ids.filtered(lambda a: not a.serial_no or not a.serial_no.strip())
            if missing:
                raise UserError(_(
                    "Cannot mark Done — %d of %d asset inventory rows are missing a Serial Number. "
                    "Please fill in all serial numbers on the Asset List page first."
                ) % (len(missing), len(rec.asset_list_ids)))

            rec.state = "done"
            rec.message_post(body=_(
                "All %d serial numbers filled. Request marked as Done."
            ) % len(rec.asset_list_ids))
            for line in rec.asset_list_ids:
                line.action_confirm()
                # rec. was missing here - a bare `asset_list_ids` is an
                # undefined name, so this raised NameError every time Done was
                # pressed. The request state had already been written by then,
                # so the failure surfaced later as "Cursor already closed"
                # rather than as the NameError itself.
                if line.product_id:
                    line.product_id.sudo().is_asset = True
                # lot_id points at stock.quant, whose write() is restricted -
                # Odoo only allows it in inventory mode. Without that context
                # this raises, and an empty lot_id would silently write to an
                # empty recordset anyway.
                if line.lot_id:
                    line.lot_id.sudo().with_context(
                        inventory_mode=True).is_asset = True
    # ----------------------------------------------------------------
    # Automatic transition: po_created -> po_done
    # ----------------------------------------------------------------
    def _check_and_advance_to_po_done(self):
        """Called by purchase.order.write() when a PO changes state.

        Advances 'po_created' -> 'po_done' once the goods have actually
        arrived, and generates the blank asset.list rows.

        Two things this deliberately does NOT do:

        1. It does not require PO state == 'done'. That state means "Locked",
           a manual action many teams never perform - so keying off it left
           requests stuck in po_created forever even after everything was
           received. Receipt is judged from the incoming pickings instead,
           which is what "we have the goods" actually means and matches what
           the all_pos_done compute field already reports.

        2. It does not wait for draft/sent RFQs. An RFQ that never turned
           into an order - because a different vendor won, or it was simply
           abandoned - would otherwise block the request forever. Only
           committed POs (confirmed or locked) are considered.
        """
        for rec in self:
            if rec.state != "po_created":
                continue

            committed_pos = rec.purchase_order_ids.filtered(
                lambda p: p.state in ("purchase", "done"))
            if not committed_pos:
                continue

            all_received = True
            for po in committed_pos:
                incoming = po.picking_ids.filtered(
                    lambda pick: pick.picking_type_code == "incoming"
                    and pick.state != "cancel")
                # A confirmed PO with no receipt yet means the goods are
                # still coming - not that there is nothing to wait for.
                if not incoming or any(p.state != "done" for p in incoming):
                    all_received = False
                    break
            if not all_received:
                continue

            # Move state and generate rows in one shot
            rec.state = "po_done"
            rec._generate_asset_list_rows()
            rec.message_post(body=_(
                "All Purchase Orders are now Done. "
                "%d asset inventory row(s) auto-created. "
                "Please open the Asset List page, fill in the serial numbers, "
                "and then click 'Done' to close this request."
            ) % len(rec.asset_list_ids))

    def _generate_asset_list_rows(self):
        """Create asset.list rows based on request_line.qty.

        Total rows == sum(request_line.quantity).
        """
        AssetList = self.env["asset.list"].sudo()

        for request in self:
            if request.asset_list_ids:
                # Don't regenerate if rows already exist
                continue

            new_rows = []

            # Build PO unit slots
            po_slots = []

            for po in request.purchase_order_ids.filtered(
                    lambda p: p.state in ("purchase", "done")
            ):
                for line in po.order_line.filtered(
                        lambda l: not l.display_type
                ):
                    qty = int(line.product_qty or 0)

                    for _i in range(qty):
                        po_slots.append({
                            "po_id": po.id,
                            "po_line_id": line.id,
                            "product_id": line.product_id.id
                            if line.product_id else False,
                        })

            slot_index = 0

            # Create rows based on request line quantity
            for rl in request.line_ids:

                quantity = int(rl.quantity or 0)

                for _i in range(quantity):
                    slot = (
                        po_slots[slot_index]
                        if slot_index < len(po_slots)
                        else {}
                    )

                    new_rows.append({
                        "request_id": request.id,
                        "po_id": slot.get("po_id"),
                        "po_line_id": slot.get("po_line_id"),
                        "product_id": slot.get("product_id"),
                        "category_id": rl.asset_category_id.id
                        if rl.asset_category_id else False,
                        "serial_no": False,
                    })

                    slot_index += 1

            if new_rows:
                AssetList.create(new_rows)

    # ----------------------------------------------------------------
    # Smart-button targets
    # ----------------------------------------------------------------
    def action_open_pos(self):
        self.ensure_one()
        # env.ref() raises ValueError when the external ID is missing, which
        # takes down the whole smart button rather than degrading. That
        # happens whenever these custom views have not loaded - a partial
        # module upgrade, or purchase_order_custom_views.xml erroring earlier
        # in the same load. Fall back to Odoo's standard purchase views so
        # the button keeps working instead of erroring out.
        views = []
        list_view = self.env.ref("asset_management.view_asset_po_tree",
                                 raise_if_not_found=False)
        form_view = self.env.ref("asset_management.view_asset_po_form",
                                 raise_if_not_found=False)
        if list_view and form_view:
            views = [(list_view.id, "list"), (form_view.id, "form")]

        action = {
            "type": "ir.actions.act_window",
            "name": _("Purchase Orders"),
            "res_model": "purchase.order",
            "view_mode": "list,form",
            "domain": [("id", "in", self.purchase_order_ids.ids)],
            "context": {
                "default_asset_request_id": self.id,
            },
        }
        if views:
            action["views"] = views
        return action
    def action_open_asset_list(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Asset Inventory"),
            "res_model": "asset.list",
            "view_mode": "list,form",
            "domain": [("request_id", "=", self.id)],
            "context": {"default_request_id": self.id},
        }


class AssetRequestLine(models.Model):
    _name = "asset.request.line"
    _description = "Asset Request Line"

    request_id = fields.Many2one(
        "asset.request",
        string="Request",
        required=True,
        ondelete="cascade",
        index=True,
    )
    asset_category_id = fields.Many2one(
        "asset.category",
        string="Asset Category",
        required=True,
    )
    description = fields.Char(string="Description")
    quantity = fields.Integer(string="Quantity", required=True, default=1)

    @api.constrains("quantity")
    def _check_quantity(self):
        for rec in self:
            if rec.quantity <= 0:
                raise ValidationError(_("Quantity must be greater than zero."))


class AssetAssetWindowsUpdate(models.Model):
    _inherit = 'asset.asset'

    asset_list_id = fields.Many2one('asset.list')

    @api.depends('device_name', 'asset_name', 'asset_code',
                 'asset_list_id.serial_no')
    def _compute_display_name(self):
        """Label an asset for dropdowns.

        device_name is reported by the AGENT, so it is empty for anything
        without one - general assets, peripherals, anything created from a
        receipt. Using it unguarded produced labels like "False - FN-BT-001",
        which is what a Many2one to a general asset showed everywhere.

        Falls back through asset_name (the descriptive label) to asset_code
        (always generated), so there is never a blank or "False" label.
        """
        for rec in self:
            label = rec.device_name or rec.asset_name or rec.asset_code or _("New Asset")
            serial = rec.asset_list_id.serial_no if rec.asset_list_id else False
            rec.display_name = f"{label} - {serial}" if serial else label