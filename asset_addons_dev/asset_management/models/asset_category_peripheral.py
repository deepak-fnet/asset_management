# -*- coding: utf-8 -*-
"""Peripherals (mouse, keyboard, monitor, bag, ...) as asset.asset records.

Why these stay in asset.asset rather than a separate model
------------------------------------------------------------
asset.list.asset_id points at asset.asset, and the whole
asset.request -> PO -> asset.list pipeline is built around that link.
A separate "asset.peripheral" model would need its own parallel
request/PO/list wiring duplicated from scratch for no benefit - the
category is what differs (a mouse has no OS, no agent, no hardware specs),
not the fact that it's "an asset with a code and an owner". So peripherals
get their own is_peripheral flag on asset.category, a lightweight dedicated
form, and a separate per-category coding scheme - but the same table, the
same audit trail, and the same asset.list linkage as laptops.

Per-category coding scheme
---------------------------
Each peripheral category gets its own ir.sequence, created on first use,
so "Mouse" and "Keyboard" each count from 1 independently
(FN-MS-00001, FN-MS-00002, ... / FN-KB-00001, ...) rather than sharing one
running number. The company-wide prefix ("FN-" by default, matching the
scheme already in use) is read once from a config parameter and baked into
the sequence's own `prefix` field at creation time - changing the config
parameter later only affects categories that get their first sequence
created after the change. This is deliberate: retroactively changing the
prefix on parts that may already have a physical printed asset tag would
be actively harmful, not just cosmetic.
"""

from odoo import models, fields, api


class AssetCategoryPeripheral(models.Model):
    _inherit = "asset.category"

    is_peripheral = fields.Boolean(
        string="Is Peripheral",
        help="Small assets with no OS and no agent - mouse, keyboard, "
             "monitor, headset, bag, etc. Categories ticked here appear in "
             "the Peripherals menu with a simplified form, and get their own "
             "auto-generated asset code (e.g. FN-MS-00001) instead of the "
             "platform-based Windows/Linux/macOS coding scheme.")
    sequence_id = fields.Many2one(
        "ir.sequence", string="Code Sequence", readonly=True, copy=False,
        help="Auto-created the first time a peripheral is saved under this "
             "category. One independent counter per category.")

    def _get_next_peripheral_code(self):
        """Return the next asset code for this category, creating its
        dedicated sequence on first use."""
        self.ensure_one()
        if not self.sequence_id:
            company_prefix = self.env["ir.config_parameter"].sudo().get_param(
                "asset_management.peripheral_code_prefix", default="FN-")
            category_code = (self.code or "GEN").strip().upper()
            sequence = self.env["ir.sequence"].sudo().create({
                "name": f"Asset Peripheral Code - {self.name}",
                "code": f"asset.category.peripheral.{self.id}",
                "prefix": f"{company_prefix}{category_code}-",
                "padding": 5,
                "company_id": False,
            })
            self.sequence_id = sequence.id
        return self.sequence_id.next_by_id()


class AssetAssetNotes(models.Model):
    """A plain free-text notes field, generically useful but added here
    because peripherals - unlike agent-managed laptops - have no other
    structured place to record something like "case is cracked" or
    "charger not included"."""
    _inherit = "asset.asset"

    notes = fields.Text(string="Notes")


class AssetAssetPeripheralCreate(models.Model):
    """Hooks asset.asset creation to generate a peripheral code, without
    touching the existing platform-based create() override.

    Odoo chains multiple create() overrides across inheriting model classes
    via super(), regardless of which file defines them, so this runs
    alongside the original create() in asset_asset.py rather than replacing
    it. Pre-filling vals['asset_code'] here before the original override
    runs makes its own `if not vals.get("asset_code")` check a no-op for
    peripheral rows, so nothing there needs to change.
    """
    _inherit = "asset.asset"

    @api.model_create_multi
    def create(self, vals_list):
        Category = self.env["asset.category"]
        for vals in vals_list:
            category_id = vals.get("category_id")
            if not category_id or vals.get("asset_code"):
                continue
            category = Category.browse(category_id)
            if not category.exists() or not category.is_peripheral:
                continue

            code = category._get_next_peripheral_code()
            vals["asset_code"] = code

            # serial_number is required=True on asset.asset, but most
            # peripherals (mice, keyboards, cables) have no meaningfully
            # trackable manufacturer serial printed anywhere. Default it to
            # the same tag rather than force the user to type a duplicate
            # value just to satisfy the constraint.
            if not vals.get("serial_number"):
                vals["serial_number"] = code

        return super().create(vals_list)