# -*- coding: utf-8 -*-
from odoo import fields, models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    stage_id = fields.Many2one(
        'cm.stage', string='Construction Stage', domain="[('project_id', '=', project_id)]")
    is_material_issue = fields.Boolean(
        string='Material Issue', copy=False,
        help="Set on the internal transfer created by a stage's 'Create Material Issue' action, "
             "to distinguish site-issue moves from incoming purchase receipts on the same product.")
