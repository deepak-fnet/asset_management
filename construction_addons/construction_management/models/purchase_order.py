# -*- coding: utf-8 -*-
from odoo import api, fields, models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    stage_id = fields.Many2one(
        'cm.stage', string='Construction Stage', ondelete='set null', tracking=True,
        domain="[('project_id', '=', project_id)] if project_id else []")

    @api.onchange('stage_id')
    def _onchange_stage_id(self):
        for order in self:
            if order.stage_id:
                order.project_id = order.stage_id.project_id

    @api.onchange('requisition_id')
    def _onchange_requisition_id_cm_stage(self):
        for order in self:
            if order.requisition_id and order.requisition_id.stage_id:
                order.stage_id = order.requisition_id.stage_id
                order.project_id = order.requisition_id.project_id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('requisition_id') and not vals.get('stage_id'):
                requisition = self.env['purchase.requisition'].browse(vals['requisition_id'])
                if requisition.stage_id:
                    vals['stage_id'] = requisition.stage_id.id
                    vals['project_id'] = requisition.project_id.id
        return super().create(vals_list)

    def button_confirm(self):
        res = super().button_confirm()
        for order in self:
            if order.stage_id:
                order.picking_ids.filtered(lambda p: not p.stage_id).write({
                    'stage_id': order.stage_id.id,
                    'project_id': order.project_id.id,
                })
        return res

    def _prepare_invoice(self):
        vals = super()._prepare_invoice()
        if self.stage_id:
            vals['stage_id'] = self.stage_id.id
            vals['project_id'] = self.project_id.id
        return vals
