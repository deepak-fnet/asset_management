# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ConstructionMasterProject(models.Model):
    _name = 'cm.master.project'
    _description = 'Construction Master Project'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(
        string='Master Project No.', required=True, copy=False, readonly=True,
        default=lambda self: _('New'))
    partner_id = fields.Many2one('res.partner', string='Client', required=True, tracking=True)
    lead_id = fields.Many2one('crm.lead', string='Source Opportunity', copy=False)
    sale_order_id = fields.Many2one('sale.order', string='Contract (Sale Order)', copy=False, tracking=True)
    retention_percent = fields.Float(related='sale_order_id.retention_percent', readonly=True)
    dlp_months = fields.Integer(related='sale_order_id.dlp_months', readonly=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id', store=True)
    total_contract_value = fields.Monetary(string='Total Contract Value', tracking=True)
    advance_received = fields.Monetary(string='Advance Received', tracking=True)
    start_date = fields.Date(string='Start Date', default=fields.Date.context_today)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('closed', 'Closed'),
    ], default='draft', tracking=True, copy=False)

    subproject_ids = fields.One2many('project.project', 'master_project_id', string='Sub-Projects')
    subproject_count = fields.Integer(compute='_compute_subproject_count')
    overall_completion = fields.Float(
        string='Overall Completion (%)', compute='_compute_overall_completion', store=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('cm.master.project') or _('New')
        records = super().create(vals_list)
        for record in records:
            if record.state == 'draft':
                record.state = 'active'
        return records

    def _compute_subproject_count(self):
        for record in self:
            record.subproject_count = len(record.subproject_ids)

    @api.depends('subproject_ids.completion_percent', 'subproject_ids.cm_budget')
    def _compute_overall_completion(self):
        for record in self:
            total_budget = sum(record.subproject_ids.mapped('cm_budget'))
            if total_budget:
                record.overall_completion = sum(
                    sp.cm_budget * sp.completion_percent for sp in record.subproject_ids
                ) / total_budget
            else:
                record.overall_completion = 0.0

    def action_close(self):
        for record in self:
            if not record.subproject_ids:
                raise UserError(_("Add at least one sub-project before closing the master project."))
            unclosed = record.subproject_ids.filtered(lambda sp: sp.cm_status != 'closed')
            if unclosed:
                raise UserError(_(
                    "All sub-projects must be closed before closing the master project. "
                    "Still open: %s") % ', '.join(unclosed.mapped('name')))
            record.state = 'closed'

    def action_view_subprojects(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sub-Projects'),
            'res_model': 'project.project',
            'view_mode': 'list,form',
            'domain': [('master_project_id', '=', self.id)],
            'context': {'default_master_project_id': self.id, 'default_partner_id': self.partner_id.id},
        }

    def action_view_flowchart(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'cm_flowchart_client_action',
            'name': _('Flow Chart'),
            'context': {'default_master_id': self.id},
        }

    def action_view_sale_order(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sale Order'),
            'res_model': 'sale.order',
            'view_mode': 'form',
            'res_id': self.sale_order_id.id,
        }
