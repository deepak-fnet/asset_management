# -*- coding: utf-8 -*-

from odoo import api, fields, models


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    is_fixed_asset = fields.Boolean(
        string="Is Fixed Asset",
        compute='_compute_is_fixed_asset', store=True, readonly=False,
        help="Received units of this line become individual fixed asset records. "
             "Pre-ticked from the product category mapping; you can override it here.")

    @api.depends('product_id')
    def _compute_is_fixed_asset(self):
        Mapping = self.env['asset.category.mapping']
        for line in self:
            line.is_fixed_asset = Mapping._is_asset_product(line.product_id)

    def _prepare_stock_move_vals(self, picking, price_unit, product_uom_qty, product_uom):
        """Carry the flag from the purchase order line onto the receipt move."""
        vals = super()._prepare_stock_move_vals(picking, price_unit, product_uom_qty, product_uom)
        vals['is_fixed_asset'] = self.is_fixed_asset
        return vals
