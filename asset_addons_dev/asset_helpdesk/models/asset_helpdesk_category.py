# -*- coding: utf-8 -*-
from odoo import fields, models


class AssetHelpdeskCategory(models.Model):
    _name = 'asset.helpdesk.category'
    _description = 'Asset Helpdesk Category'
    _order = 'sequence, name'

    name = fields.Char(string='Name', required=True)
    sequence = fields.Integer(string='Sequence', default=10)
    active = fields.Boolean(string='Active', default=True)
    description = fields.Text(string='Description')
    is_repair_flow = fields.Boolean()
    is_general = fields.Boolean(
        string="Non-IT / General Repair",
        help="Tick for categories covering non-IT assets - furniture, "
             "electrical fixtures, plant equipment. Routes tickets in this "
             "category to issue_type='general' on the repair record, "
             "instead of hardware/software.")
    default_team_id = fields.Many2one(
        'asset.helpdesk.team', string='Default Team',
        help="Team a ticket in this category is routed to when nobody picks "
             "one explicitly - used by the public self-service ticket form, "
             "where the submitter has no reason to know the team structure.",
    )

    _sql_constraints = [
        ('name_uniq', 'unique(name)', 'Category name must be unique.'),
    ]
