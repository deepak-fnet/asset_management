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
        """Product first, category mapping second.

        Was mapping-only, which cannot express "this category is
        asset-tracked but this particular product in it is not". The product
        flag now wins; the mapping still applies when the product does not
        set one, so existing mapping-only setups are unaffected.
        """
        for line in self:
            product = line.product_id
            line.is_fixed_asset = (
                product._is_fixed_asset_product() if product else False)

    def _prepare_stock_move_vals(self, picking, price_unit, product_uom_qty, product_uom):
        """Carry the flag from the purchase order line onto the receipt move."""
        vals = super()._prepare_stock_move_vals(picking, price_unit, product_uom_qty, product_uom)
        vals['is_fixed_asset'] = self.is_fixed_asset
        return vals
