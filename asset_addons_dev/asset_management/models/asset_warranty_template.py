from odoo import models, fields


class AssetWarrantyTemplate(models.Model):
    _name = "asset.warranty.template"
    _description = "Asset Warranty Template"
    _order = "sequence, name"

    name = fields.Char(string="Template Name", required=True)
    sequence = fields.Integer(default=10)
    period_months = fields.Integer(
        string="Warranty Period (Months)", required=True,
        help="Number of months from the warranty start date this template's "
             "warranty covers. Applied to warranty_period_months on any "
             "asset that selects this template.")
    active = fields.Boolean(default=True)
    note = fields.Text(string="Notes")
