# -*- coding: utf-8 -*-
"""General (non-IT) asset support.

Brings the useful parts of the standalone `asset` / `asset_purchase` modules
into asset_management, without dragging in their duplicate models.

Key decisions:

* asset.addition is NOT recreated. General assets are asset.asset records
  whose category is flagged is_general - the same pattern already used for
  peripherals. That keeps one asset table, so transfers, scrap, warranty,
  licences and the asset.list procurement chain all work on general assets
  for free instead of needing a parallel implementation.

* asset.category is NOT redefined here. asset_management already owns it;
  this only extends it. The incoming module's own asset_count compute and
  action_view_assets are dropped rather than merged, since two computes on
  one field silently overwrite each other.

* physical.location / from.location / to.location are NOT created. All three
  are locations, and asset.location already exists and is hierarchical.
"""

from odoo import models, fields, api, _


class Plant(models.Model):
    _name = "plant.master"
    _description = "Plant"
    _order = "name"

    name = fields.Char("Plant", required=True)
    code = fields.Char("Plant Code")
    address = fields.Text()
    active = fields.Boolean(default=True)

    _name_uniq = models.Constraint(
        "unique (name)", "A plant with this name already exists.")


class AssetSubCategory(models.Model):
    _name = "asset.sub.category"
    _description = "Asset Sub Category"
    _order = "category_id, name"

    name = fields.Char("Sub Category", required=True)
    category_id = fields.Many2one(
        "asset.category", "Category", required=True,
        ondelete="cascade", index=True)
    active = fields.Boolean(default=True)

    _name_per_category_uniq = models.Constraint(
        "unique (name, category_id)",
        "This sub category already exists for the selected category.")

    @api.depends("name", "category_id")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = (f"{rec.category_id.name} / {rec.name}"
                                if rec.category_id else rec.name)


class AssetCategoryGeneral(models.Model):
    _inherit = "asset.category"

    is_general = fields.Boolean(
        string="Is General Asset",
        help="Tick for non-IT assets - machinery, furniture, plant equipment. "
             "These have no agent, so they appear under General Assets with a "
             "simplified form and go through transfer/scrap rather than the "
             "repair and helpdesk flows.")
    sub_category_ids = fields.One2many(
        "asset.sub.category", "category_id", "Sub Categories")
    sub_category_count = fields.Integer(compute="_compute_sub_category_count")

    # Per-category view overrides. When set, opening assets of this category
    # uses these views instead of the defaults - so a category whose assets
    # need a different layout can point at its own form/list without any code
    # change. Left empty, the standard general-asset views are used.
    form_view_id = fields.Many2one(
        "ir.ui.view", string="Custom Form View",
        domain="[('model', '=', 'asset.asset'), ('type', '=', 'form')]",
        help="Optional. Opening an asset in this category uses this form "
             "instead of the default general-asset form.")
    list_view_id = fields.Many2one(
        "ir.ui.view", string="Custom List View",
        domain="[('model', '=', 'asset.asset'), ('type', '=', 'list')]",
        help="Optional. Same idea as the form view, for the list.")

    @api.depends("sub_category_ids")
    def _compute_sub_category_count(self):
        for rec in self:
            rec.sub_category_count = len(rec.sub_category_ids)

    def action_view_general_assets(self):
        """Open this category's assets, honouring the custom views if set."""
        self.ensure_one()
        action = {
            "type": "ir.actions.act_window",
            "name": _("%s Assets") % self.name,
            "res_model": "asset.asset",
            "view_mode": "list,form",
            "domain": [("category_id", "=", self.id)],
            "context": {
                "default_category_id": self.id,
                "general_asset": True,
            },
        }
        # Only override when BOTH are set. Mixing a custom list with the
        # default form (or vice versa) tends to surprise people more than it
        # helps, and Odoo needs a consistent pair here anyway.
        if self.form_view_id and self.list_view_id:
            action["views"] = [(self.list_view_id.id, "list"),
                               (self.form_view_id.id, "form")]
        return action


class ProductCategoryMapping(models.Model):
    """Maps a purchased product category to an asset category.

    Used when goods are received: it decides which asset category the
    resulting asset record should get, so receipts can create assets without
    someone classifying each one by hand.
    """
    _name = "product.category.mapping"
    _description = "Product Category Mapping"
    _order = "product_categ_id"
    _rec_name = "product_categ_id"

    product_categ_id = fields.Many2one(
        "product.category", string="Product Category",
        required=True, ondelete="cascade", index=True)
    asset_category_id = fields.Many2one(
        "asset.category", string="Asset Category",
        required=True, ondelete="cascade")
    asset_sub_category_id = fields.Many2one(
        "asset.sub.category", string="Asset Sub Category",
        domain="[('category_id', '=', asset_category_id)]")
    create_asset = fields.Boolean(
        string="Auto-Create Asset", default=True,
        help="Create an asset record when a product in this category is "
             "received.")
    active = fields.Boolean(default=True)

    _product_categ_uniq = models.Constraint(
        "unique (product_categ_id)",
        "This product category is already mapped.")

    @api.onchange("asset_category_id")
    def _onchange_asset_category_id(self):
        if self.asset_sub_category_id and \
                self.asset_sub_category_id.category_id != self.asset_category_id:
            self.asset_sub_category_id = False


class AssetAssetGeneral(models.Model):
    """Fields that only mean something for general (non-IT) assets."""
    _inherit = "asset.asset"

    is_general_asset = fields.Boolean(
        related="category_id.is_general", store=True, index=True,
        string="General Asset")

    tag_number = fields.Char("Tag ID", copy=False, index=True,
                             help="Physical asset tag / RFID tag number.")
    sub_category_id = fields.Many2one(
        "asset.sub.category", string="Sub Category", index=True,
        domain="[('category_id', '=', category_id)]")
    plant_id = fields.Many2one("plant.master", string="Plant", tracking=True)

    # Extra serials for assets made up of several serialised parts.
    serial_number_1 = fields.Char("Serial Number 2")
    serial_number_2 = fields.Char("Serial Number 3")
    serial_number_3 = fields.Char("Serial Number 4")

    asset_value = fields.Monetary(
        string="Asset Value", currency_field="currency_id",
        help="Book value, where it differs from the purchase cost.")
    acquisition_date = fields.Date()

    # ── AMC ───────────────────────────────────────────────────────────────
    is_amc = fields.Boolean("Under AMC")
    amc_start_date = fields.Date("AMC Start")
    amc_end_date = fields.Date("AMC End")
    amc_vendor_id = fields.Many2one("res.partner", "AMC Vendor")
    amc_nature = fields.Char("AMC Nature")
    amc_file = fields.Binary("AMC Document")
    amc_filename = fields.Char()

    # ── Licence ───────────────────────────────────────────────────────────
    is_licence = fields.Boolean("Has Licence")
    licence_start_date = fields.Date("Licence Start")
    licence_end_date = fields.Date("Licence End")
    licence_vendor_id = fields.Many2one("res.partner", "Licence Vendor")
    licence_nature = fields.Char("Licence Nature")
    licence_file = fields.Binary("Licence Document")
    licence_filename = fields.Char()

    # ── Procurement traceability ──────────────────────────────────────────
    # Populated when an asset is created automatically from a goods receipt.
    # Kept here rather than in asset_purchase so the fields exist even if that
    # module is not installed - otherwise every view referencing them would
    # have to be conditional.
    product_id = fields.Many2one("product.product", string="Product", index=True)
    lot_id = fields.Many2one("stock.lot", string="Lot / Serial")
    stock_move_id = fields.Many2one("stock.move", string="Stock Move", index=True)
    stock_move_line_id = fields.Many2one("stock.move.line", string="Move Line")
    picking_id = fields.Many2one("stock.picking", string="Receipt", index=True)
    purchase_order_id = fields.Many2one(
        "purchase.order", string="Purchase Order", index=True)
    receipt_date = fields.Date(string="Receipt Date")

    # department_id on asset.asset is related to the assigned employee and is
    # readonly, so it cannot be set at receipt time when nobody holds the asset
    # yet. This is the department the asset was BOUGHT for, which is a
    # different thing and worth keeping separately.
    procurement_department_id = fields.Many2one(
        "hr.department", string="Procured For Department")

    @api.onchange("category_id")
    def _onchange_category_clear_sub(self):
        if self.sub_category_id and \
                self.sub_category_id.category_id != self.category_id:
            self.sub_category_id = False

    @api.model_create_multi
    def create(self, vals_list):
        """Give assets a unique serial when they have no real one.

        asset.asset.serial_number is required AND uniqueness-constrained
        (_check_serial_number_unique), because IT assets always report a real
        serial from their agent. Machinery, furniture and consumables often
        have no readable serial at all.

        Two things this has to get right, both learned the hard way:

        1. The placeholder must be UNIQUE per record. A shared literal like
           "/" means the second serial-less asset in the same batch fails the
           constraint and takes the whole create() down with it.

        2. The fallback must NOT be gated on category.is_general. It was, and
           that silently skipped exactly the rows that needed it: a receipt
           whose mapped asset category was not flagged general passed
           serial_number='' straight through to the constraint. A blank serial
           is invalid for ANY asset, general or not, so the guard now keys off
           the blank value itself.

        asset_code is not usable as the fallback either: asset_management's
        own create() generates it, and that runs AFTER this one (this module
        loads later, so its create sits earlier in the MRO). It is still empty
        at this point, so the sequence is pulled directly instead.
        """
        Sequence = self.env["ir.sequence"].sudo()
        for vals in vals_list:
            # Note: "in vals" is deliberately NOT used. Callers commonly pass
            # serial_number='' for untracked products, and an empty string
            # needs the fallback just as much as a missing key does.
            if vals.get("serial_number"):
                continue
            serial = vals.get("tag_number")
            if not serial:
                serial = "NOSN-%s" % (
                    Sequence.next_by_code("asset.asset.sequence")
                    or fields.Datetime.now().strftime("%Y%m%d%H%M%S%f"))
            vals["serial_number"] = serial
        return super().create(vals_list)