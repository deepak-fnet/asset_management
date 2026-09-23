# -*- coding: utf-8 -*-
from odoo import fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    stage_id = fields.Many2one('cm.stage', string='Construction Stage', copy=False)
    project_id = fields.Many2one('project.project', string='Construction Project', copy=False)
    milestone_bill_id = fields.Many2one('cm.milestone.bill', string='Milestone Bill', copy=False)
