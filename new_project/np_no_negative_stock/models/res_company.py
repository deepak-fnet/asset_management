from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    np_allow_negative_stock = fields.Boolean(
        string="Allow Negative Stock",
        default=False,
        help="If unchecked, no operation is allowed to bring the on-hand "
             "quantity of a storable product below zero in an internal location.",
    )
