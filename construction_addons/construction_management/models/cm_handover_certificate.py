# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _


class ConstructionHandoverCertificate(models.Model):
    _name = 'cm.handover.certificate'
    _description = 'Project Handover Certificate'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(
        string='Certificate No.', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    project_id = fields.Many2one(
        'project.project', string='Sub-Project', required=True, ondelete='cascade',
        index=True, readonly=True)
    master_project_id = fields.Many2one(
        related='project_id.master_project_id', store=True, string='Master Project')
    partner_id = fields.Many2one(related='project_id.partner_id', store=True, string='Client')

    handover_date = fields.Date(required=True, readonly=True)
    warranty_months = fields.Integer(string='Warranty Period (Months)', readonly=True)
    warranty_expiry_date = fields.Date(compute='_compute_warranty_expiry_date', store=True)

    final_inspection_done = fields.Boolean(string='Final Quality Inspection Done', readonly=True)
    client_signoff = fields.Boolean(string='Client Sign-off / Acceptance', readonly=True)
    keys_handed_over = fields.Boolean(readonly=True)
    electricity_connection_transferred = fields.Boolean(readonly=True)
    water_connection_transferred = fields.Boolean(readonly=True)
    warranty_documents_handed_over = fields.Boolean(readonly=True)

    issued_by = fields.Many2one('res.users', string='Issued By', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('cm.handover.certificate') or _('New')
        return super().create(vals_list)

    @api.depends('handover_date', 'warranty_months')
    def _compute_warranty_expiry_date(self):
        for cert in self:
            if cert.handover_date and cert.warranty_months:
                cert.warranty_expiry_date = cert.handover_date + relativedelta(months=cert.warranty_months)
            else:
                cert.warranty_expiry_date = False

    def action_view_project(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sub-Project'),
            'res_model': 'project.project',
            'view_mode': 'form',
            'res_id': self.project_id.id,
        }
