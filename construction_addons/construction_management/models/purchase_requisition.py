# -*- coding: utf-8 -*-
from odoo import api, fields, models


class PurchaseRequisition(models.Model):
    _inherit = 'purchase.requisition'

    project_id = fields.Many2one('project.project', string='Construction Project')
    stage_id = fields.Many2one(
        'cm.stage', string='Construction Stage', domain="[('project_id', '=', project_id)]")

    @api.onchange('stage_id')
    def _onchange_stage_id(self):
        for req in self:
            if req.stage_id:
                req.project_id = req.stage_id.project_id
