# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class ConstructionSafetyIncident(models.Model):
    _name = 'cm.safety.incident'
    _description = 'Safety Incident'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(default=lambda self: _('New'), copy=False, readonly=True, tracking=True)
    project_id = fields.Many2one('project.project', required=True, index=True, tracking=True)
    stage_id = fields.Many2one('cm.stage', string='Stage', domain="[('project_id', '=', project_id)]")
    incident_date = fields.Date(default=fields.Date.context_today, required=True)
    severity = fields.Selection([
        ('near_miss', 'Near Miss'),
        ('minor', 'Minor'),
        ('major', 'Major'),
        ('fatal', 'Fatal'),
    ], default='near_miss', required=True, tracking=True)
    description = fields.Text(required=True)
    corrective_action = fields.Text()
    reported_by = fields.Many2one('res.users', default=lambda self: self.env.user)
    state = fields.Selection([
        ('open', 'Open'),
        ('closed', 'Closed'),
    ], default='open', tracking=True, copy=False)
    closed_date = fields.Date(readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('cm.safety.incident') or _('New')
        return super().create(vals_list)

    def action_close(self):
        self.write({'state': 'closed', 'closed_date': fields.Date.context_today(self)})

    def action_reopen(self):
        self.write({'state': 'open', 'closed_date': False})
