from odoo import models, fields, api
from odoo.exceptions import UserError


class AssetCategory(models.Model):
    _name = "asset.category"
    _description = "Asset Category"
    _order = "name"

    name = fields.Char(string="Category Name", required=True)
    code = fields.Char(string="Category Code", size=5)
    description = fields.Text(string="Description")
    active = fields.Boolean(string="Active", default=True)
    color = fields.Integer(string="Color Index", default=1)
    icon = fields.Char(string="Icon", default="fa-laptop")
    image_128 = fields.Image(string="Image", max_width=128, max_height=128)
    asset_count = fields.Integer(string="Asset Count", compute="_compute_asset_count", store=True)
    asset_ids = fields.One2many("asset.asset", "category_id", string="Assets")

    @api.depends("asset_ids")
    def _compute_asset_count(self):
        for record in self:
            record.asset_count = len(record.asset_ids)

    def unlink(self):
        """
        Override unlink to prevent deletion of categories with linked assets.
        This ensures database integrity and prevents orphaned asset records.
        """
        for category in self:
            if category.asset_count > 0:
                raise UserError(
                    f"Cannot delete category '{category.name}' because it has "
                    f"{category.asset_count} asset(s) linked to it.\n\n"
                    f"Please reassign or remove these assets before deleting the category."
                )
        return super(AssetCategory, self).unlink()