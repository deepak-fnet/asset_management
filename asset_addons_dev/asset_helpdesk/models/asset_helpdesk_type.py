# -*- coding: utf-8 -*-
from odoo import fields, models


class AssetHelpdeskType(models.Model):
    _name = 'asset.helpdesk.type'
    _description = 'Asset Helpdesk Ticket Type'
    _order = 'sequence, name'

    name = fields.Char(string='Name', required=True)
    sequence = fields.Integer(string='Sequence', default=10)
    active = fields.Boolean(string='Active', default=True)
    description = fields.Text(string='Description')
    is_repair_flow = fields.Boolean()
    _sql_constraints = [
        ('name_uniq', 'unique(name)', 'Ticket Type name must be unique.'),
    ]
