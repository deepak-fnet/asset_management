from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    is_asset = fields.Boolean(
        string='Is IT Asset',
        help='Enable this for products that should be managed as IT assets.'
    )


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    is_asset = fields.Boolean(
        string='Is IT Asset',
        store=True,
        readonly=True
    )