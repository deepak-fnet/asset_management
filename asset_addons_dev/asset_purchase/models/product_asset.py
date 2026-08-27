# -*- coding: utf-8 -*-

from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    is_fixed_asset = fields.Boolean(
        string="Is Fixed Asset",
        help="Received units of this product become individual fixed asset "
             "records.\n\n"
             "This is set on the PRODUCT, which is more specific than the "
             "product-category mapping: 'Computers' as a category may be "
             "asset-tracked while a spare keyboard in it is not, and the "
             "mapping alone cannot express that.")
    asset_category_id = fields.Many2one(
        "asset.category", string="Asset Category",
        help="Category given to fixed assets created from this product. "
             "Overrides whatever the product-category mapping would have "
             "chosen - the product knows better than its category does.")

    @api.onchange("is_fixed_asset")
    def _onchange_is_fixed_asset(self):
        """Pre-fill the asset category from the mapping as a starting point.

        Only fills a BLANK value - it never overwrites a category someone has
        already chosen, since the whole point of the product-level field is
        to be able to differ from the mapping.
        """
        if self.is_fixed_asset and not self.asset_category_id:
            mapping = self.env["asset.category.mapping"].search(
                [("product_category_id", "=", self.categ_id.id)], limit=1)
            if mapping:
                self.asset_category_id = mapping.category_id


class ProductProduct(models.Model):
    _inherit = "product.product"

    def _get_asset_category(self):
        """Asset category for fixed assets created from this product.

        Resolution order, most specific first:
          1. the product's own asset_category_id
          2. the product category's mapping row
          3. nothing - the caller decides what to do

        Kept here rather than inline at each call site so the receipt flow,
        the PO line and any future caller all resolve it the same way.
        """
        self.ensure_one()
        if self.asset_category_id:
            return self.asset_category_id
        mapping = self.env["asset.category.mapping"].search(
            [("product_category_id", "=", self.categ_id.id)], limit=1)
        return mapping.category_id if mapping else self.env["asset.category"]

    def _is_fixed_asset_product(self):
        """True when receiving this product should create fixed assets.

        The product-level flag wins outright when ticked. When it is not
        ticked the category mapping is still consulted, so existing setups
        that rely purely on the mapping keep working untouched.
        """
        self.ensure_one()
        if self.is_fixed_asset:
            return True
        return self.env["asset.category.mapping"]._is_asset_product(self)
