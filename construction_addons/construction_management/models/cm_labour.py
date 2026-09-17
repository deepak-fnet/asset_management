# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ConstructionLabourGang(models.Model):
    _name = 'cm.labour.gang'
    _description = 'Labour Gang / Contractor Crew'
    _order = 'name'

    name = fields.Char(required=True)
    contractor_partner_id = fields.Many2one('res.partner', string='Labour Contractor')
    category = fields.Selection([
        ('mason', 'Mason'),
        ('helper', 'Helper'),
        ('carpenter', 'Carpenter'),
        ('bar_bender', 'Bar Bender / Steel Fixer'),
        ('electrician', 'Electrician'),
        ('plumber', 'Plumber'),
        ('general', 'General Labour'),
    ], required=True, default='general')
    standard_hour_rate = fields.Float(string='Standard Hour Rate')
    active = fields.Boolean(default=True)
    attendance_count = fields.Integer(compute='_compute_attendance_count')

    def _compute_attendance_count(self):
        counts = self.env['cm.labour.attendance']._read_group(
            [('gang_id', 'in', self.ids)], ['gang_id'], ['__count'])
        count_by_gang = {gang.id: count for gang, count in counts}
        for gang in self:
            gang.attendance_count = count_by_gang.get(gang.id, 0)

    def action_view_attendance(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Attendance',
            'res_model': 'cm.labour.attendance',
            'view_mode': 'list,form',
            'domain': [('gang_id', '=', self.id)],
            'context': {'default_gang_id': self.id},
        }


class ConstructionLabourAttendance(models.Model):
    _name = 'cm.labour.attendance'
    _description = 'Daily Labour Attendance'
    _order = 'date desc, id desc'

    project_id = fields.Many2one('project.project', required=True, index=True)
    stage_id = fields.Many2one('cm.stage', string='Stage', domain="[('project_id', '=', project_id)]")
    date = fields.Date(required=True, default=fields.Date.context_today)
    gang_id = fields.Many2one('cm.labour.gang', required=True, string='Labour Gang')
    category = fields.Selection(related='gang_id.category', store=True)
    count_present = fields.Integer(string='Count Present', required=True, default=1)
    hours = fields.Float(default=8.0)
    hour_rate = fields.Float(related='gang_id.standard_hour_rate')
    currency_id = fields.Many2one(related='project_id.currency_id', store=True)
    wage_amount = fields.Monetary(compute='_compute_wage_amount', store=True)
    remarks = fields.Char()

    @api.depends('count_present', 'hours', 'hour_rate')
    def _compute_wage_amount(self):
        for rec in self:
            rec.wage_amount = rec.count_present * rec.hours * rec.hour_rate
