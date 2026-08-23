# -*- coding: utf-8 -*-

from odoo import api, fields, models


class AssetCategoryMapping(models.Model):
    """Maps a product category onto the defaults used when a received product
    becomes a fixed asset."""

    _name = "asset.category.mapping"
    _description = "Product Category to Asset Mapping"
    _order = "product_category_id"
    _rec_name = "product_category_id"

    product_category_id = fields.Many2one(
        'product.category', string="Product Category",
        required=True, ondelete='cascade', index=True)
    is_fixed_asset = fields.Boolean(
        string="Is Fixed Asset", default=True,
        help="Products in this category are treated as fixed assets: the flag is "
             "pre-ticked on purchase order lines and receipt lines.")
    category_id = fields.Many2one(
        'asset.category', string="Asset Category",
        domain="[('is_general', '=', True)]",
        help="Category given to every asset created from this product category. "
             "Restricted to general-asset categories: an asset created under a "
             "non-general category would exist but never appear under "
             "Assets > General Assets, which looks like the receipt silently "
             "failed to create anything.")
    sub_category_id = fields.Many2one(
        'asset.sub.category', string="Asset Sub Category",
        domain="[('category_id', '=', category_id)]")
    plant_id = fields.Many2one('plant.master', string="Plant")
    # asset.location, not physical.location: general_asset uses one
    # hierarchical location model for every location role.
    location_id = fields.Many2one('asset.location', string="Physical Location")
    department_id = fields.Many2one('hr.department', string="Department")
    active = fields.Boolean(default=True)

    _product_category_uniq = models.Constraint(
        'unique (product_category_id)',
        'There is already an asset mapping for this product category.',
    )

    @api.model
    def _resolve_for_product(self, product):
        """Return the mapping for ``product``, walking up the category tree.

        ``product.category`` is ``_parent_store``, so a child category inherits
        its parent's mapping when it has none of its own.
        """
        if not product:
            return self.browse()
        categ = product.categ_id
        while categ:
            mapping = self.search([('product_category_id', '=', categ.id)], limit=1)
            if mapping:
                return mapping
            categ = categ.parent_id
        return self.browse()

    @api.model
    def _is_asset_product(self, product):
        """Whether ``product`` should default to being a fixed asset."""
        mapping = self._resolve_for_product(product)
        return bool(mapping) and mapping.is_fixed_asset
