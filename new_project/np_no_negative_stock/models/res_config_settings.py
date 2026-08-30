from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    np_allow_negative_stock = fields.Boolean(
        related='company_id.np_allow_negative_stock',
        readonly=False,
    )
