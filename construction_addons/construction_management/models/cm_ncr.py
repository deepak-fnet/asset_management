# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class ConstructionNCR(models.Model):
    _name = 'cm.ncr'
    _description = 'Non-Conformance Report'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(default=lambda self: _('New'), copy=False, readonly=True, tracking=True)
    project_id = fields.Many2one('project.project', required=True, index=True, tracking=True)
    stage_id = fields.Many2one('cm.stage', string='Stage', domain="[('project_id', '=', project_id)]")
    date = fields.Date(default=fields.Date.context_today, required=True)
    description = fields.Text(required=True, string='Non-Conformance Description')
    root_cause = fields.Text()
    corrective_action = fields.Text()
    severity = fields.Selection([
        ('minor', 'Minor'),
        ('major', 'Major'),
        ('critical', 'Critical'),
    ], default='minor', required=True, tracking=True)
    raised_by = fields.Many2one('res.users', default=lambda self: self.env.user)
    state = fields.Selection([
        ('open', 'Open'),
        ('in_review', 'In Review'),
        ('closed', 'Closed'),
    ], default='open', tracking=True, copy=False)
    closed_date = fields.Date(readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('cm.ncr') or _('New')
        return super().create(vals_list)

    def action_review(self):
        self.write({'state': 'in_review'})

    def action_close(self):
        self.write({'state': 'closed', 'closed_date': fields.Date.context_today(self)})

    def action_reopen(self):
        self.write({'state': 'open', 'closed_date': False})
