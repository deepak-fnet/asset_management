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
    # STATE FLOW:
    #   draft → submitted → approved → rfq → po_created → done
    #
    # po_created → done is AUTOMATIC: once every committed PO of the request
    #   has been fully received. The receipt itself already created one
    #   asset.asset per unit (asset_purchase), carrying its serial number,
    #   so there is nothing left for anyone to fill in.
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("submitted", "Submitted"),
            ("approved", "Approved"),
            ("rfq", "RFQ Sent"),
            ("po_created", "PO Created"),
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
    asset_ids = fields.One2many(
        "asset.asset",
        compute="_compute_asset_ids",
        string="Received Assets",
        help="Assets created on receipt of this request's purchase orders.",
    )
    asset_count = fields.Integer(
        string="Assets",
        compute="_compute_asset_ids",
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

    @api.depends("purchase_order_ids")
    def _compute_asset_ids(self):
        Asset = self.env["asset.asset"]
        has_link = "purchase_order_id" in Asset._fields
        for rec in self:
            assets = Asset.search([
                ("purchase_order_id", "in", rec.purchase_order_ids.ids),
            ]) if has_link and rec.purchase_order_ids else Asset
            rec.asset_ids = assets
            rec.asset_count = len(assets)

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

        Request lines carry an asset.category, not necessarily a product -
        for lines where the buyer already set a Product, that line is
        pre-filled on the RFQ; lines left as category-only still need the
        buyer to pick a product before sending it out.
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
                "default_order_line": self._prefill_po_lines(),
                "form_view_initial_mode": "edit",
            },
        }
        form_view = self.env.ref("asset_management.view_asset_po_form",
                                 raise_if_not_found=False)
        if form_view:
            action["views"] = [(form_view.id, "form")]
        return action

    def _prefill_po_lines(self):
        """One (0, 0, {...}) order-line dict per request line that already
        has a Product set. Lines left as category-only produce nothing here -
        the buyer still adds those by hand once the exact model is known."""
        self.ensure_one()
        lines = []
        for rl in self.line_ids.filtered("product_id"):
            product = rl.product_id
            lines.append((0, 0, {
                "product_id": product.id,
                "product_qty": rl.quantity,
                "product_uom_id": product.uom_id.id,
                # name/price_unit/date_planned are computed, store=True,
                # readonly=False on purchase.order.line - left unset here so
                # the line's own compute derives them from product_id.
            }))
        return lines

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
                "default_order_line": self._prefill_po_lines(),
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

    # ----------------------------------------------------------------
    # Automatic transition: po_created -> done
    # ----------------------------------------------------------------
    def _check_and_advance_to_done(self):
        """Called by purchase.order.write() / receipt validation.

        Closes the request once the goods have actually arrived: every
        committed PO (confirmed or locked) has all its incoming receipts
        done. The receipt already created the asset records (with their
        serial numbers) in asset_purchase, so the request is complete.

        Two things this deliberately does NOT do:

        1. It does not require PO state == 'done' ("Locked"), a manual
           action many teams never perform.
        2. It does not wait for draft/sent RFQs - an RFQ that never turned
           into an order would otherwise block the request forever.
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

            rec.state = "done"
            rec._finalize_received_assets()
            rec.message_post(body=_(
                "All Purchase Orders are received. %d asset(s) created from "
                "the receipts. Request marked as Done."
            ) % rec.asset_count)

    def _finalize_received_assets(self):
        """What closing the request settles for the received units:

        * the purchased products (and their on-hand quants) are flagged as
          IT assets;
        * a unit bought for a joining-process shortfall is pre-picked onto
          that requirement line, so a person only has to press Assign Asset.
        """
        for rec in self:
            assets = rec.asset_ids
            if not assets:
                continue
            assets.product_id.product_tmpl_id.sudo().is_asset = True
            lots = assets.lot_id if "lot_id" in assets._fields else self.env["stock.lot"]
            if lots:
                quants = self.env["stock.quant"].sudo().search([
                    ("lot_id", "in", lots.ids),
                    ("location_id.usage", "=", "internal"),
                ])
                # stock.quant.write() is only allowed in inventory mode.
                quants.with_context(inventory_mode=True).write({"is_asset": True})
            rec._route_to_joining_requirements(assets)

    def _route_to_joining_requirements(self, assets):
        """Pre-pick received units onto the joining requirement line that
        was short of them. Deliberately does not assign them - a person
        still presses Assign Asset on the joining process."""
        self.ensure_one()
        # Same bar as the joining form's picker: submitted assets only.
        free = assets.filtered(lambda a: a.state == "submit")
        for line in self.line_ids.filtered("joining_requirement_id"):
            requirement = line.joining_requirement_id
            missing = requirement.quantity - requirement.selected_count
            if missing <= 0:
                continue
            candidates = free.filtered(
                lambda a: a.category_id == requirement.category_id
                and (not line.product_id or a.product_id == line.product_id)
                and a not in requirement.asset_ids)[:min(missing, line.quantity)]
            if not candidates:
                continue
            requirement.asset_ids = [(4, a.id) for a in candidates]
            free -= candidates
            joining = requirement.joining_id
            joining.message_post(body=_(
                "%(assets)s received and pre-picked for the %(category)s "
                "requirement for %(employee)s. Open the joining process and "
                "press Assign Asset to complete it."
            ) % {
                "assets": ", ".join(candidates.mapped("display_name")),
                "category": requirement.category_id.name,
                "employee": joining.employee_id.name,
            })

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
    def action_open_assets(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Received Assets"),
            "res_model": "asset.asset",
            "view_mode": "list,form",
            "domain": [("id", "in", self.asset_ids.ids)],
            "context": {"create": False},
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
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        help="Exact product to buy for this line, if already known. When "
             "set, this pre-fills the Purchase Order line created from this "
             "request, so the buyer does not have to look it up again.",
    )
    description = fields.Char(string="Description")
    quantity = fields.Integer(string="Quantity", required=True, default=1)
    joining_requirement_id = fields.Many2one(
        "asset.joining.requirement",
        string="Source Requirement",
        copy=False,
        readonly=True,
        help="The joining-process requirement line this request line was "
             "raised for, if any. Once a unit bought against this line is "
             "received, it is pre-picked on that requirement for its "
             "employee automatically.",
    )

    @api.constrains("quantity")
    def _check_quantity(self):
        for rec in self:
            if rec.quantity <= 0:
                raise ValidationError(_("Quantity must be greater than zero."))

    @api.onchange("asset_category_id")
    def _onchange_asset_category_id(self):
        """Narrow the Product picker to products mapped to this category.

        asset.category.mapping (asset_purchase) already records which
        product category maps to which asset category for received goods -
        reused here in reverse so the buyer isn't picking from every
        product in the database.
        """
        # asset.category.mapping lives in the optional asset_purchase module,
        # which depends on asset_management (not the reverse) - it may not be
        # installed, so this narrows the domain only when it is, and leaves
        # the picker open to every product otherwise.
        if "asset.category.mapping" not in self.env:
            return
        for rec in self:
            if not rec.asset_category_id:
                continue
            mappings = self.env["asset.category.mapping"].sudo().search(
                [("category_id", "=", rec.asset_category_id.id)])
            product_categ_ids = mappings.mapped("product_category_id").ids
            if product_categ_ids:
                return {"domain": {
                    "product_id": [("categ_id", "in", product_categ_ids)]}}


class AssetAssetWindowsUpdate(models.Model):
    _inherit = 'asset.asset'

    @api.depends('device_name', 'asset_name', 'asset_code', 'serial_number')
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
            serial = rec.serial_number
            # "NOSN-..." is the placeholder general_asset stores for a unit
            # received without a serial - not worth showing.
            if serial and serial.startswith("NOSN-"):
                serial = False
            rec.display_name = f"{label} - {serial}" if serial else label