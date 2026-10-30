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
    store_notes = fields.Text(
        string="Store Notes", tracking=True,
        help="Why the request was closed without buying (e.g. issued from "
             "stock). Required for Mark Done.")
    can_approve = fields.Boolean(compute="_compute_can_approve")
    issue_picking_ids = fields.Many2many(
        "stock.picking", "asset_request_issue_picking_rel", "request_id", "picking_id",
        string="Deliveries", copy=False, readonly=True,
        help="Stock taken out for the consumable (non-asset) lines.")
    issue_picking_count = fields.Integer(compute="_compute_issue_picking_count")

    # ----------------------------------------------------------------
    # Workflow
    # ----------------------------------------------------------------
    # STATE FLOW:
    #   draft → submitted → approved → rfq → po_created → received → done
    #   (or approved → done by the store, with Store Notes)
    #
    # po_created → received is automatic once every committed PO is
    # received; the store then checks availability and presses Done, which
    # delivers the consumable lines out of stock.
    #
    # The requester describes the need and submits; the requester's manager
    # approves; the store then sets the product on each line (category
    # follows the product), checks availability and either raises an RFQ or
    # closes the request with notes.
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
            ("received", "Received"),
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
    # Counters read the purchase orders as superuser: asset users have no
    # purchase rights but still see how far their request has got.
    rfq_count = fields.Integer(
        string="RFQs",
        compute="_compute_rfq_count",
        compute_sudo=True,
    )

    @api.depends("purchase_order_ids.state")
    def _compute_rfq_count(self):
        for rec in self:
            rec.rfq_count = len(rec._rfq_documents())

    def _is_order_document(self, po):
        """A real purchase order: split from an RFQ (vendor_management's
        rfq_id) or created directly (direct_order) - as opposed to the RFQ
        itself."""
        return bool(getattr(po, "rfq_id", False) or getattr(po, "direct_order", False))

    def _rfq_documents(self):
        self.ensure_one()
        return self.purchase_order_ids.filtered(
            lambda p: not self._is_order_document(p) and p.state != "cancel")

    def _order_documents(self):
        self.ensure_one()
        return self.purchase_order_ids.filtered(
            lambda p: self._is_order_document(p) and p.state != "cancel")

    # ----------------------------------------------------------------
    # Computed counters / smart-button helpers
    # ----------------------------------------------------------------
    po_count = fields.Integer(
        string="POs",
        compute="_compute_po_count",
        compute_sudo=True,
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
        compute_sudo=True,
        help="True when at least one non-cancelled PO exists and "
             "every non-cancelled PO has state == 'done'.",
    )

    @api.depends("purchase_order_ids")
    def _compute_po_count(self):
        for rec in self:
            rec.po_count = len(rec._order_documents())

    @api.depends("purchase_order_ids")
    def _compute_asset_ids(self):
        Asset = self.env["asset.asset"]
        has_link = "purchase_order_id" in Asset._fields
        for rec in self:
            assets = Asset.search([
                ("purchase_order_id", "in", rec.sudo().purchase_order_ids.ids),
            ]) if has_link and rec.sudo().purchase_order_ids else Asset
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
    def _requester_manager_user(self):
        self.ensure_one()
        employee = self.requested_by.sudo().employee_id
        return employee.parent_id.user_id

    @api.depends_context("uid")
    @api.depends("requested_by")
    def _compute_can_approve(self):
        # The requester's manager approves; an Asset Manager can stand in.
        is_asset_manager = self.env.user.has_group("asset_management.group_asset_manager")
        for rec in self:
            rec.can_approve = is_asset_manager or rec._requester_manager_user() == self.env.user

    def action_submit(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only Draft requests can be submitted."))
            if not rec.line_ids:
                raise UserError(_("Add at least one request line before submitting."))
            rec.state = "submitted"
            manager = rec._requester_manager_user()
            if manager:
                rec.message_post(
                    body=_("%(user)s submitted this asset request. Waiting for your approval.",
                           user=rec.requested_by.name),
                    partner_ids=manager.partner_id.ids,
                    message_type="comment", subtype_xmlid="mail.mt_comment")

    def action_approve(self):
        for rec in self:
            if rec.state != "submitted":
                raise UserError(_("Only Submitted requests can be approved."))
            if not rec.can_approve:
                raise UserError(_("Only %s (the requester's manager) can approve this request.",
                                  rec._requester_manager_user().name or _("the requester's manager")))
            # Checked just above; a manager may have no write right on requests.
            rec.sudo().state = "approved"

    def _check_products_set(self):
        missing = self.line_ids.filtered(lambda l: not l.product_id)
        if missing:
            raise UserError(_("Set the product on: %s", ", ".join(missing.mapped("description"))))

    @api.depends("issue_picking_ids")
    def _compute_issue_picking_count(self):
        for rec in self:
            rec.issue_picking_count = len(rec.issue_picking_ids)

    def action_open_issue_pickings(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Deliveries"),
            "res_model": "stock.picking",
            "view_mode": "list,form",
            "domain": [("id", "in", self.issue_picking_ids.ids)],
        }

    def _stock_source(self):
        warehouse = self.env["stock.warehouse"].sudo().search(
            [("company_id", "=", self.env.company.id)], limit=1)
        if not warehouse:
            raise UserError(_("No warehouse configured."))
        return warehouse

    def _consumption_location(self):
        Location = self.env["stock.location"].sudo()
        loc = Location.search([("name", "=", "Department Consumption"), ("usage", "=", "inventory"),
                               ("company_id", "in", (False, self.env.company.id))], limit=1)
        if not loc:
            parent = self.env.ref("stock.stock_location_locations_virtual", raise_if_not_found=False)
            loc = Location.create({"name": "Department Consumption", "usage": "inventory",
                                   "location_id": parent.id if parent else False,
                                   "company_id": self.env.company.id})
        return loc

    def _issue_consumables(self, partial=False):
        """Deliver the consumable lines out of stock (one validated picking).

        partial=False (Mark Done): every line must be fully in stock.
        partial=True (after the purchase is received): issue what is on hand
        and note any shortfall instead of blocking the receipt."""
        self.ensure_one()
        lines = self.line_ids.filtered(lambda l: l.is_consumable and l.product_id.type == "consu")
        if not lines:
            return
        warehouse = self._stock_source()
        source = warehouse.lot_stock_id
        todo, short = [], []
        for line in lines:
            if not line.product_id.is_storable:
                # Untracked (no Track Inventory): no stock to check, the
                # out move only records what the department took.
                todo.append((line, line.quantity))
                continue
            on_hand = line.product_id.with_context(location=source.id).qty_available
            qty = min(line.quantity, on_hand) if partial else line.quantity
            if on_hand < line.quantity:
                short.append(_("%(p)s: need %(q)s %(u)s, on hand %(h)s",
                               p=line.product_id.display_name, q=line.quantity,
                               u=line.product_id.uom_id.name, h=on_hand))
            if qty > 0:
                todo.append((line, qty))
        if short and not partial:
            raise UserError(_("Not enough stock:\n%s\n\nCreate an RFQ instead.") % "\n".join(short))
        if todo:
            dest = self._consumption_location()
            Picking = self.env["stock.picking"].sudo()
            picking = Picking.create({
                "picking_type_id": (warehouse.out_type_id or warehouse.int_type_id).id,
                "location_id": source.id,
                "location_dest_id": dest.id,
                "origin": self.name,
            })
            for line, qty in todo:
                self.env["stock.move"].sudo().create({
                    "description_picking": line.description,
                    "product_id": line.product_id.id,
                    "product_uom_qty": qty,
                    "product_uom": line.product_id.uom_id.id,
                    "picking_id": picking.id,
                    "location_id": source.id,
                    "location_dest_id": dest.id,
                })
            picking.action_confirm()
            picking.action_assign()
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
                move.picked = True
            picking.with_context(skip_backorder=True).button_validate()
            self.sudo().issue_picking_ids = [(4, picking.id)]
            self.message_post(body=_("Delivered from stock (%(pick)s): %(lines)s",
                                     pick=picking.name,
                                     lines=", ".join("%s x %s" % (l.product_id.display_name, q) for l, q in todo)))
        if short and partial:
            self.message_post(body=_("Not fully delivered - short on stock: %s", "; ".join(short)))

    def action_check_availability(self):
        """Per line: free assets (Submitted, not in repair) for asset
        products; on-hand stock for consumables."""
        self.ensure_one()
        self._check_products_set()
        rows, short = [], False
        for line in self.line_ids:
            free = line.available_qty
            short = short or free < line.quantity
            if line.is_consumable and not line.product_id.is_storable:
                rows.append(_("• %(prod)s: need %(need)s %(uom)s, received on this request %(free)s %(uom)s (stock not tracked)",
                              prod=line.product_id.display_name, need=line.quantity,
                              free=free, uom=line.product_id.uom_id.name))
            elif line.is_consumable:
                rows.append(_("• %(prod)s: need %(need)s %(uom)s, on hand %(free)s %(uom)s",
                              prod=line.product_id.display_name, need=line.quantity,
                              free=free, uom=line.product_id.uom_id.name))
            else:
                rows.append(_("• %(prod)s: need %(need)s, free assets %(free)s",
                              prod=line.product_id.display_name, need=line.quantity, free=free))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Availability"),
                "message": "\n".join(rows),
                "type": "warning" if short else "success",
                "sticky": short,
            },
        }

    def action_store_done(self):
        """Store closes the request: Approved (no purchase - Store Notes say
        why) or Received (the purchase arrived). Consumable lines are
        delivered out of stock."""
        for rec in self:
            if rec.state not in ("approved", "received"):
                raise UserError(_("Only an Approved or Received request can be marked done."))
            if rec.state == "approved" and not (rec.store_notes or "").strip():
                raise UserError(_("Enter the Store Notes first (why no purchase is needed)."))
            rec._check_products_set()
            rec._issue_consumables()
            rec.state = "done"
            rec.message_post(body=_("Closed by the store: %s", rec.store_notes or _("received goods issued")))

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
        self._check_products_set()

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
            "domain": [("id", "in", self._rfq_documents().ids)],
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
                # vendor_management: a single-vendor order, not an RFQ.
                "default_direct_order": True,
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

            rec.state = "received"
            rec._finalize_received_assets()
            rec.message_post(body=_(
                "All Purchase Orders are received. %d asset(s) created from "
                "the receipts. The store can check availability and press Done."
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
            "domain": [("id", "in", self._order_documents().ids)],
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
    # The requester only describes the need; the store sets the product once
    # the request is approved, and the category follows the product.
    asset_category_id = fields.Many2one(
        "product.category",
        string="Asset Category",
        compute="_compute_asset_category_id", store=True, readonly=False,
    )
    product_uom_id = fields.Many2one(related="product_id.uom_id", string="Unit")
    is_consumable = fields.Boolean(
        compute="_compute_is_consumable",
        help="Not tracked as an asset (e.g. wire): taken out of stock by a delivery.")
    available_qty = fields.Float(
        string="Available", compute="_compute_available_qty", digits="Product Unit",
        help="Asset products: Submitted assets not under repair. "
             "Consumables: on-hand stock.")
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        help="Exact product to buy for this line, if already known. When "
             "set, this pre-fills the Purchase Order line created from this "
             "request, so the buyer does not have to look it up again.",
    )
    description = fields.Char(string="Description", required=True)
    quantity = fields.Float(string="Quantity", required=True, default=1, digits="Product Unit")
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

    @api.depends("product_id")
    def _compute_asset_category_id(self):
        for line in self:
            if line.product_id:
                line.asset_category_id = line.product_id.categ_id

    @api.depends("product_id")
    def _compute_is_consumable(self):
        for line in self:
            product = line.product_id
            # is_fixed_asset comes from asset_purchase (optional here).
            is_asset = product.is_asset or ("is_fixed_asset" in product._fields and product.is_fixed_asset)
            line.is_consumable = bool(product) and not is_asset

    @api.depends("product_id")
    def _compute_available_qty(self):
        Asset = self.env["asset.asset"].sudo()
        # asset.asset.product_id comes from general_asset (optional here).
        has_product = "product_id" in Asset._fields
        warehouse = self.env["stock.warehouse"].sudo().search(
            [("company_id", "=", self.env.company.id)], limit=1)
        for line in self:
            if not line.product_id:
                line.available_qty = 0
            elif line.is_consumable and not line.product_id.is_storable:
                req = line.request_id.sudo()
                received = sum(req.purchase_order_ids.filtered(lambda p: p.state in ("purchase", "done"))
                               .order_line.filtered(lambda l: l.product_id == line.product_id).mapped("qty_received"))
                issued = sum(req.issue_picking_ids.move_ids.filtered(
                    lambda m: m.product_id == line.product_id and m.state == "done").mapped("quantity"))
                line.available_qty = received - issued
            elif line.is_consumable:
                line.available_qty = line.product_id.sudo().with_context(
                    location=warehouse.lot_stock_id.id).qty_available if warehouse else 0
            else:
                line.available_qty = Asset.search_count([
                    ("product_id", "=", line.product_id.id), ("state", "=", "submit"),
                    ("in_repair", "=", False)]) if has_product else 0

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