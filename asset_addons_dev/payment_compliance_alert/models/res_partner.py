# -*- coding: utf-8 -*-
from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # In the v18 source this came from account_extended. Any non-empty value
    # marks the vendor as MSME (45-day payment rule).
    udyog_adhaar = fields.Char(
        string='Udyog Aadhaar / Udyam No.',
        help="MSME registration number. Filled = MSME vendor: unpaid bills are flagged after 45 days "
             "(MSME Act Section 15) as well as after 180 days (Section 43B(h)).")
