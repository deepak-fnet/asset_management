# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ConstructionDPR(models.Model):
    _name = 'cm.dpr'
    _description = 'Daily Progress Report'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    project_id = fields.Many2one('project.project', required=True, index=True)
    stage_id = fields.Many2one('cm.stage', string='Stage', domain="[('project_id', '=', project_id)]")
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    weather = fields.Selection([
        ('sunny', 'Sunny'),
        ('cloudy', 'Cloudy'),
        ('rainy', 'Rainy'),
        ('extreme_heat', 'Extreme Heat'),
    ], default='sunny')
    narrative = fields.Text(string='Work Done Today')
    material_consumed = fields.Text(string='Material Consumed')
    equipment_used = fields.Text(string='Equipment Used')
    total_labour_present = fields.Integer(compute='_compute_labour_stats', store=True)
    labour_wage_total = fields.Monetary(compute='_compute_labour_stats', store=True)
    currency_id = fields.Many2one(related='project_id.currency_id', store=True)
    reported_by = fields.Many2one('res.users', default=lambda self: self.env.user)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
    ], default='draft', tracking=True, copy=False)

    @api.depends('project_id', 'date')
    def _compute_labour_stats(self):
        Attendance = self.env['cm.labour.attendance']
        for dpr in self:
            lines = Attendance.search([
                ('project_id', '=', dpr.project_id.id), ('date', '=', dpr.date),
            ]) if dpr.project_id and dpr.date else Attendance.browse()
            dpr.total_labour_present = sum(lines.mapped('count_present'))
            dpr.labour_wage_total = sum(lines.mapped('wage_amount'))

    def action_submit(self):
        self.write({'state': 'submitted'})

    def action_approve(self):
        self.write({'state': 'approved'})

    def action_reset_to_draft(self):
        self.write({'state': 'draft'})
