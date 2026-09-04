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
from odoo.exceptions import ValidationError


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
    is_it_asset = fields.Boolean()
    is_network_asset = fields.Boolean()
    is_cctv_asset = fields.Boolean()
    is_dynamic_form = fields.Boolean(
        string="Use Dynamic Form Layout",
        help="Tick to show ONLY the two configured columns (Left/Right "
             "Column Fields below) on the general asset form for this "
             "category, instead of the standard fixed Classification/"
             "Placement layout. Off by default so existing categories keep "
             "the familiar form until an admin opts one in.")
    sub_category_ids = fields.One2many(
        "asset.sub.category", "category_id", "Sub Categories")

    # Two configurable columns of fields shown on the general asset form
    # once this category is picked. Two distinct comodels (see
    # asset_category_field_line.py), not one model with a side= selection
    # filtered by domain - a row added through one column's editable list
    # ending up saved against the WRONG column, because the list's context
    # default did not reliably set a shared "side" field on new rows, is a
    # real bug that model shape produced. Column identity is now which
    # table a row lives in, which cannot drift.
    left_field_ids = fields.One2many(
        "asset.category.field.line.left", "category_id",
        string="Left Column Fields")
    right_field_ids = fields.One2many(
        "asset.category.field.line.right", "category_id",
        string="Right Column Fields")

    def get_dynamic_field_lines(self):
        """The general asset form's dynamic-fields widget reads this.

        One RPC per category (not per field) - returns both columns'
        configured lines with everything the widget needs to render them:
        which asset.asset field, what label, and the readonly/invisible
        Python expressions to evaluate against the record.
        """
        self.ensure_one()

        def _serialize(lines):
            return [{
                "display_name": line.label,
                "field_name": line.field_name,
                "readonly_condition": line.readonly_condition or "",
                "invisible_condition": line.invisible_condition or "",
            } for line in lines if line.field_name]

        return {
            "left": _serialize(self.left_field_ids.sorted("sequence")),
            "right": _serialize(self.right_field_ids.sorted("sequence")),
        }
    sub_category_count = fields.Integer(compute="_compute_sub_category_count")

    @api.depends("sub_category_ids")
    def _compute_sub_category_count(self):
        for rec in self:
            rec.sub_category_count = len(rec.sub_category_ids)

    def action_view_general_assets(self):
        """Open this category's assets on the general asset form/list.

        The category form's own smart button used to fall back to
        asset_management's action_asset_asset - which is actually "Windows
        Assets", hard-domained to os_platform = 'windows' - so any category
        whose assets are not Windows-agent-reported (every general/non-IT
        asset, and most IT ones too) showed an empty list despite
        asset_count being correct. This filters on category_id instead,
        which is what a category's smart button should have done from the
        start, and always opens the general asset form/list views rather
        than whatever the platform-specific default happens to be.
        """
        self.ensure_one()
        list_view = self.env.ref("general_asset.view_asset_general_list")
        form_view = self.env.ref("general_asset.view_asset_general_form")
        return {
            "type": "ir.actions.act_window",
            "name": _("%s Assets") % self.name,
            "res_model": "asset.asset",
            "views": [(list_view.id, "list"), (form_view.id, "form")],
            "domain": [("category_id", "=", self.id)],
            "context": {
                "default_category_id": self.id,
                "general_asset": True,
            },
        }


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

    it_asset_id = fields.Many2one(
        'asset.asset', string="IT Asset",
        domain="[('monitoring_protocol', '=', 'agent'), ('is_general_asset', '=', False)]",
        help="The agent-reporting asset.asset record for this physical unit.\n"
             "Think product.template / product.product: this fixed asset is "
             "the template (procurement, assignment history, lifecycle), the "
             "agent record is the variant (live hardware/software telemetry).")
    network_asset_id = fields.Many2one('asset.network.device', string="Network Asset")
    cctv_asset_id = fields.Many2one('asset.camera', string="Camera Asset")

    _it_asset_uniq = models.Constraint(
        "UNIQUE(it_asset_id)",
        "This agent-reported asset is already mapped to another fixed asset.")
    _network_asset_uniq = models.Constraint(
        "UNIQUE(network_asset_id)",
        "This network device is already mapped to another fixed asset.")
    _cctv_asset_uniq = models.Constraint(
        "UNIQUE(cctv_asset_id)",
        "This camera is already mapped to another fixed asset.")

    @api.constrains('it_asset_id', 'network_asset_id', 'cctv_asset_id',
                    'monitoring_protocol', 'is_general_asset')
    def _check_agent_asset_mapping(self):
        """Guard the fixed-asset / agent-asset relationship.

        A UNIQUE index alone is not enough here, because Postgres treats
        every NULL as distinct - so it stops two fixed assets claiming the
        same agent record, but says nothing about the other two ways this
        relationship can go wrong:

        1. A fixed asset pointing at another FIXED asset instead of an
           agent-created one. The domain on the field discourages it, but
           domains are UI-only - anything written through the ORM, an
           import, or a server action bypasses them entirely.

        2. An agent-created record being turned into a fixed asset itself.
           Agent records exist for visibility only; if one were used as a
           fixed asset it would end up in assignment and lifecycle flows,
           which is exactly what this whole split is meant to prevent.
        """
        for rec in self:
            if rec.it_asset_id:
                if rec.it_asset_id == rec:
                    raise ValidationError(_(
                        "An asset cannot be mapped to itself."))
                if rec.it_asset_id.monitoring_protocol != 'agent':
                    raise ValidationError(_(
                        "%s is not an agent-reported asset, so it cannot be "
                        "mapped here. Only records created by a running "
                        "agent can be linked to a fixed asset."
                    ) % rec.it_asset_id.display_name)
                if rec.it_asset_id.is_general_asset:
                    raise ValidationError(_(
                        "%s is itself a fixed asset and cannot be mapped as "
                        "the agent record of another."
                    ) % rec.it_asset_id.display_name)

            # An agent record must never itself be a fixed asset.
            if rec.is_general_asset and rec.monitoring_protocol == 'agent':
                raise ValidationError(_(
                    "%s is agent-reported, so it cannot also be a fixed "
                    "asset. Agent records are for visibility only - create a "
                    "fixed asset and map this record to it instead."
                ) % rec.display_name)

    is_submit = fields.Boolean(
        string="Submitted", copy=False, readonly=True, tracking=True,
        help="Set when the asset has been submitted. On submit the purchase "
             "and warranty details entered here are pushed down onto the "
             "linked IT / network / camera record, so the two stop drifting "
             "apart the moment someone edits one of them. For a category on "
             "the fixed (non-dynamic) form layout, submitting also locks the "
             "whole form - use Reset to unlock it again. A dynamic-form "
             "category is never locked by this, since its fields are "
             "admin-configured per category rather than fixed.")

    is_general_asset = fields.Boolean( store=True,
        string="General Asset")
    is_it_asset = fields.Boolean(related="category_id.is_it_asset", store=True,)
    is_network_asset = fields.Boolean(related="category_id.is_network_asset", store=True,)
    is_cctv_asset = fields.Boolean(related="category_id.is_cctv_asset", store=True,)
    is_dynamic_form = fields.Boolean(
        related="category_id.is_dynamic_form", store=True,
        help="Drives which layout view_asset_general_form shows for this "
             "asset - the two configured columns, or the fixed "
             "Classification/Placement one.")
    # Direct on the asset, not derived from category - a category (e.g.
    # "Peripherals") can hold a mix of IoT and non-IoT units, so this is a
    # per-asset choice, ticked on the record itself rather than inherited.
    is_iot_device = fields.Boolean(string="Is IoT Device")
    is_rfid_device = fields.Boolean(
        string="Is RFID Tagged",
        help="Tick to require and show the RFID Tag ID (tag_number) on "
             "this asset - same idea as Is IoT Device.")
    iot_device_id = fields.Char(
        string="IoT Device ID",
        help="Must match the Device ID (device_id) an iot.data sensor "
             "reading was received under, in the iot_integration module.")
    iot_data_count = fields.Integer(
        string="IoT Data Logs", compute="_compute_iot_data_count",
        help="Count of iot.data rows whose device_id matches IoT Device ID "
             "above. iot_integration is optional - stays 0 if it is not "
             "installed.")
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

    @api.depends("iot_device_id")
    def _compute_iot_data_count(self):
        """Count of sensor readings for this asset, matched on Device ID.

        A plain Integer, not a relational field, so iot_integration stays
        an OPTIONAL dependency - the same "model" in self.env guard used
        everywhere else in this file for cross-module lookups. No field on
        iot.data points back at asset.asset; the match is by device_id
        string equality only.
        """
        has_iot = "iot.data" in self.env
        for rec in self:
            if not has_iot or not rec.iot_device_id:
                rec.iot_data_count = 0
                continue
            rec.iot_data_count = self.env["iot.data"].sudo().search_count([
                ("device_id", "=", rec.iot_device_id)])

    def action_view_iot_data(self):
        """Open the iot.data rows matching this asset's IoT Device ID."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("IoT Data"),
            "res_model": "iot.data",
            "view_mode": "list,form",
            "domain": [("device_id", "=", self.iot_device_id)],
        }

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
            # A fixed asset is NOT agent-reported. asset.asset.monitoring_protocol
            # defaults to "agent" for the whole model (correct for the IT
            # assets that model was originally written for), so anything
            # created here would silently inherit it - and then a fixed asset
            # would look identical to an agent record in every domain that
            # filters on this field, which is the exact distinction this
            # split depends on.
            #
            # Set explicitly to "manual": these records are created by a
            # person or by a goods receipt, never by a running agent. The
            # agent record gets linked separately via it_asset_id.
            if not vals.get("monitoring_protocol"):
                vals["monitoring_protocol"] = "manual"

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

    def action_view_it_asset(self):
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window',
            'name': 'IT Asset',
            'res_model': 'asset.asset',
            'view_mode': 'form',
            'res_id': self.it_asset_id.id,
            'target': 'current',
        }

        form_view = self.env.ref("asset_management.view_asset_asset_form",
                                 raise_if_not_found=False)
        if form_view:
            action["views"] = [(form_view.id, "form")]

        return action

    def action_view_network_asset(self):
        self.ensure_one()

        action = {
            'type': 'ir.actions.act_window',
            'name': 'Network Asset',
            'res_model': 'asset.network.device',
            'view_mode': 'form',
            'res_id': self.network_asset_id.id,
            'target': 'current',
        }
        form_view = self.env.ref("asset_management.view_asset_network_device_form",
                                 raise_if_not_found=False)
        if form_view:
            action["views"] = [(form_view.id, "form")]
        return action

    def action_view_cctv_asset(self):
        self.ensure_one()

        action = {
            'type': 'ir.actions.act_window',
            'name': 'Camera Asset',
            'res_model': 'asset.camera',
            'view_mode': 'form',
            'res_id': self.cctv_asset_id.id,
            'target': 'current',
        }
        form_view = self.env.ref("asset_management.view_asset_camera_form",
                                 raise_if_not_found=False)
        if form_view:
            action["views"] = [(form_view.id, "form")]
        return action

    # ── Submit ────────────────────────────────────────────────────────────
    # Which fields travel from the fixed asset down to the linked record, per
    # target model. The linked models were written independently and do not
    # share field names (asset.asset has procurement_vendor_id, the network
    # device has vendor_id, the camera has neither), so the mapping is
    # explicit per model rather than a blind copy of a shared field list.
    # Anything missing on the target is skipped at runtime, which keeps this
    # working when only part of the suite is installed.
    _SUBMIT_SYNC_MAP = {
        "asset.asset": {
            "purchase_date": "purchase_date",
            "purchase_cost": "purchase_cost",
            "procurement_vendor_id": "procurement_vendor_id",
            "invoice_ref": "invoice_ref",
            "warranty_period_months": "warranty_period_months",
            "warranty_start_date": "warranty_start_date",
            "warranty_end_date": "warranty_end_date",
            "warranty_file": "warranty_file",
            "warranty_filename": "warranty_filename",
        },
        "asset.network.device": {
            "purchase_date": "purchase_date",
            "purchase_cost": "purchase_cost",
            "procurement_vendor_id": "vendor_id",
        },
        "asset.camera": {},
    }

    def _submit_linked_records(self):
        """The linked records this asset must have, given its category flags.

        Returns a list of (field_name, label, record) so the caller can both
        complain about the empty ones and sync the filled ones without
        repeating the flag checks.
        """
        self.ensure_one()
        wanted = []
        if self.is_it_asset:
            wanted.append(("it_asset_id", _("IT Asset"), self.it_asset_id))
        if self.is_network_asset:
            wanted.append(
                ("network_asset_id", _("Network Asset"), self.network_asset_id))
        if self.is_cctv_asset:
            wanted.append(("cctv_asset_id", _("Camera Asset"), self.cctv_asset_id))
        return wanted

    def _sync_submit_details(self, record):
        """Copy purchase + warranty details onto one linked record."""
        mapping = self._SUBMIT_SYNC_MAP.get(record._name, {})
        vals = {}
        for src, dest in mapping.items():
            if dest not in record._fields:
                # Target model does not carry this field in the installed
                # version - nothing to do, and certainly not a hard error.
                continue
            value = self[src]
            if isinstance(value, models.Model):
                value = value.id
            vals[dest] = value
        if vals:
            record.sudo().write(vals)
        return vals

    def action_submit(self):
        """Validate the category-driven links, then push the details down."""
        for rec in self:
            links = rec._submit_linked_records()
            missing = [label for _fname, label, target in links if not target]
            if missing:
                raise ValidationError(_(
                    "Select the %s before submitting - this asset's category "
                    "says it has one, and the purchase and warranty details "
                    "are copied onto it on submit."
                ) % ", ".join(missing))
            for _fname, _label, target in links:
                rec._sync_submit_details(target)
            rec.is_submit = True
        return True

    def action_reset(self):
        """Undo Submit - unlocks the form again on a fixed-layout category.

        Does not touch anything already pushed onto the linked IT/network/
        camera record; it only flips the flag that locks this form. Submit
        (or Update) can be pressed again afterwards to re-sync.
        """
        for rec in self:
            rec.is_submit = False
        return True

    # ── Update ────────────────────────────────────────────────────────────
    # Everything the Update button pushes down ON TOP OF the submit map.
    # Same shape (source field -> destination field) and the same
    # skip-if-absent behaviour, split out only so Submit stays the narrow
    # purchase + warranty action it already is and Update is the full
    # re-sync.
    #
    # asset.network.device and asset.camera get nothing here on purpose:
    # their assigned_employee_id / department_id / assignment_date are
    # related, stored, readonly fields hanging off their own asset_id, so
    # writing them directly is either refused or immediately recomputed
    # away. They are handled by the back-link below instead - pointing
    # asset_id at this fixed asset makes the whole assignment flow down on
    # its own, and keeps flowing on every later change.
    # Format is "source_field_on_self": "dest_field_on_target" - the
    # opposite order from a "dest: self.source" note, so translate before
    # adding: `vendor_id: self.procurement_vendor_id` becomes the entry
    # "procurement_vendor_id": "vendor_id" under whichever target model it
    # belongs to. Add new lines here; only fields already present on the
    # target model actually get written (see _sync_submit_details/
    # action_update - anything missing on the target is skipped, not an
    # error), so it is always safe to add a mapping speculatively.
    _UPDATE_SYNC_MAP = {
        "asset.asset": {
            # Workflow state - it_asset_id (the agent-reported counterpart)
            # does not run its own draft/assigned/maintenance/scrapped
            # workflow; it mirrors the fixed asset's, which is the one
            # actually driven through Assign/Maintenance/Scrap.
            "state": "state",
            # Procurement information
            "acquisition_type": "acquisition_type",
            "product_id": "product_id",
            "lot_id": "lot_id",
            "stock_move_id": "stock_move_id",
            "stock_move_line_id": "stock_move_line_id",
            "picking_id": "picking_id",
            "purchase_order_id": "purchase_order_id",
            "receipt_date": "receipt_date",
            "procurement_department_id": "procurement_department_id",
            "invoice_file": "invoice_file",
            "invoice_filename": "invoice_filename",
            # Employee assignment
            "assigned_employee_id": "assigned_employee_id",
            "assignment_date": "assignment_date",
            "expected_return_date": "expected_return_date",
            "location_id": "location_id",
            # Maintenance summary
            "last_maintenance_date": "last_maintenance_date",
            "next_maintenance_date": "next_maintenance_date",
            "maintenance_type": "maintenance_type",
            "maintenance_status": "maintenance_status",
            "maintenance_reason": "maintenance_reason",
            "amc_status": "amc_status",
            # ── add your custom mapping below ──────────────────────────
            # "source_field": "dest_field",
        },
        "asset.network.device": {
            # ── add your custom mapping below ──────────────────────────
            # "source_field": "dest_field",
        },
        "asset.camera": {
            # ── add your custom mapping below ──────────────────────────
            # "source_field": "dest_field",
        },
    }

    # One2many logs mirrored onto the linked record, as
    # source o2m field -> (target o2m field, copied fields, dedup key fields).
    # Only asset.asset carries these logs; the other two models have no
    # equivalent, so nothing is mirrored to them.
    _UPDATE_HISTORY_MAP = {
        "asset.asset": {
            "assignment_history_ids": (
                "assignment_history_ids",
                ("employee_id", "date", "action", "description"),
                ("employee_id", "date", "action"),
            ),
            "maintenance_ids": (
                "maintenance_ids",
                ("maintenance_type", "request_date", "maintenance_date",
                 "cost", "notes", "state"),
                ("maintenance_type", "request_date", "maintenance_date",
                 "cost"),
            ),
        },
    }

    @staticmethod
    def _history_key(record, key_fields):
        """Comparable identity for one log row.


        Relational values are reduced to ids so a row read off the source and
        the copy already sitting on the target compare equal - comparing
        recordsets would make every row look new and duplicate the whole log
        on each Update.
        """
        key = []
        for fname in key_fields:
            value = record[fname]
            key.append(value.id if isinstance(value, models.Model) else value)
        return tuple(key)

    def _mirror_submit_history(self, record):
        """Copy missing log rows from this asset onto the linked record."""
        self.ensure_one()
        mapping = self._UPDATE_HISTORY_MAP.get(record._name, {})
        created = 0
        for src_field, (dest_field, copy_fields, key_fields) in mapping.items():
            if src_field not in self._fields or dest_field not in record._fields:
                continue
            # sudo() on both reads: assignment_history_ids/maintenance_ids
            # are restricted-visibility on some group setups, and a Update
            # click run by a user without read access to them would
            # otherwise see an empty `dest`/`self[src_field]` and silently
            # mirror nothing - looking exactly like "not updated" with no
            # error anywhere.
            dest = record.sudo()[dest_field]
            inverse = record._fields[dest_field].inverse_name
            existing = {self._history_key(row, key_fields) for row in dest}
            vals_list = []
            for row in self.sudo()[src_field]:
                if self._history_key(row, key_fields) in existing:
                    continue
                vals = {}
                for fname in copy_fields:
                    if fname not in row._fields:
                        continue
                    value = row[fname]
                    vals[fname] = (value.id if isinstance(value, models.Model)
                                   else value)
                vals[inverse] = record.id
                vals_list.append(vals)
            if vals_list:
                self.env[dest._name].sudo().create(vals_list)
                created += len(vals_list)
        return created

    def _backlink_submit_record(self, record):
        """Point the linked record's asset_id back at this fixed asset.

        This is what carries the employee assignment to the network device and
        the camera: their assignment fields are related to asset_id, so the
        link is the sync. Guarded against stealing a record that is already
        pointed at a DIFFERENT fixed asset - that would silently rewrite
        someone else's mapping, and the UNIQUE constraints on the link fields
        exist to stop exactly that.
        """
        self.ensure_one()
        if "asset_id" not in record._fields:
            return False
        current = record.asset_id
        if current and current.id != self.id:
            raise ValidationError(_(
                "%(linked)s is already linked to the asset %(other)s. Unlink "
                "it there before updating from here."
            ) % {"linked": record.display_name,
                 "other": current.display_name})
        if not current:
            record.sudo().write({"asset_id": self.id})
            return True
        return False

    def action_update(self):
        """Re-push every detail group onto the linked records.

        Same validation and same direction as action_submit, just the full set
        of groups: purchase, warranty, procurement information, employee
        assignment, maintenance summary, and the assignment / maintenance
        history rows. Safe to press repeatedly - scalars are overwritten and
        history rows are matched before being created.
        """
        for rec in self:
            links = rec._submit_linked_records()
            missing = [label for _fname, label, target in links if not target]
            if missing:
                raise ValidationError(_(
                    "Select the %s before updating - there is nothing to push "
                    "the details onto."
                ) % ", ".join(missing))
            for _fname, _label, target in links:
                rec._backlink_submit_record(target)
                # Purchase + warranty, exactly as on submit.
                rec._sync_submit_details(target)
                # Then the wider groups and the history rows.
                mapping = rec._UPDATE_SYNC_MAP.get(target._name, {})
                vals = {}
                for src, dest in mapping.items():
                    if dest not in target._fields or src not in rec._fields:
                        continue
                    value = rec[src]
                    vals[dest] = (value.id if isinstance(value, models.Model)
                                  else value)
                # "state" is now in _UPDATE_SYNC_MAP, so vals["state"] above
                # already carries this fixed asset's own workflow state
                # straight across - it_asset_id mirrors it rather than
                # running an independent one. The one case that direct copy
                # cannot cover on its own: an employee got assigned on this
                # record (assigned_employee_id set in vals) while its OWN
                # state is still "draft" (set by hand, bypassing
                # action_assign()) - without this, the mirror would leave
                # the linked record sitting in Draft despite an employee
                # already on it.
                if (target._name == "asset.asset"
                        and vals.get("assigned_employee_id")
                        and vals.get("state") == "draft"):
                    vals["state"] = "assigned"
                if vals:
                    target.sudo().write(vals)
                rec._mirror_submit_history(target)
        return True
