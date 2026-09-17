# -*- coding: utf-8 -*-
from odoo import fields, models


class StockMove(models.Model):
    _inherit = 'stock.move'

    currency_id = fields.Many2one(related='company_id.currency_id', string='Currency')
    issue_amount = fields.Monetary(
        string='Amount', currency_field='currency_id',
        help="Real value of this quantity issued to site - entered by the storekeeper, since "
             "the rate actually paid for the material can differ from the BOQ's estimated rate.")
