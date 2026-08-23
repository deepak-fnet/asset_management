# -*- coding: utf-8 -*-
from odoo import api, fields, models

from odoo.exceptions import UserError

class AssetHelpdesk(models.Model):
    _name = 'asset.helpdesk'
    _description = 'Asset Helpdesk Ticket'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    ticket_number = fields.Char(
        string='Ticket Number',
        readonly=True,
        copy=False,
        default='New',
    )
    subject = fields.Char(string='Subject', required=True, tracking=True)
    ticket_type_id = fields.Many2one(
        'asset.helpdesk.type',
        string='Ticket Type',
        required=True,
        tracking=True,
    )
    category_id = fields.Many2one(
        'asset.helpdesk.category',
        string='Category',
        tracking=True,
    )
    repair_id = fields.Many2one('repair.management')
    is_repair_flow = fields.Boolean(related="category_id.is_repair_flow",store=True)

    priority = fields.Selection(
        selection=[
            ('low', 'Low'),
            ('medium', 'Medium'),
            ('high', 'High'),
            ('critical', 'Critical'),
        ],
        string='Priority',
        default='medium',
        tracking=True,
    )
    assigned_to_id = fields.Many2one(
        'res.users',
        string='Assigned To',
        tracking=True,
    )
    team_id = fields.Many2one(
        'asset.helpdesk.team',
        string='Team',
        tracking=True,
    )
    state = fields.Selection(
        selection=[
            ('open', 'Open'),
            ('in_progress', 'In Progress'),
            ('solved', 'Solved'),
            ('cancelled', 'Cancelled'),
        ],
        string='Status',
        default='open',
        tracking=True,
    )
    history_ids = fields.One2many(
        'asset.helpdesk.history',
        'ticket_id',
        string='History',
    )
    asset_id = fields.Many2one('asset.asset')
    notes = fields.Text()

    def action_create_repair_management(self):
        for rec in self:
            if not rec.assigned_to_id:
                raise UserError("Please Select Engineer")
            if not rec.repair_id:
                if not rec.asset_id:
                    raise UserError("Please Enter Asset ID")
                repair_record = self.env['repair.management'].create({
                        'engineer_id': rec.assigned_to_id.employee_id.id,
                        'asset_id': rec.asset_id.id,
                        'issue_type': 'hardware' if 'hardware' in rec.category_id.name else 'software',
                    })
                rec.repair_id = repair_record.id
                rec.state = 'in_progress'
            return {
                'type': 'ir.actions.act_window',
                'name': ('Repair Management'),
                'res_model': 'repair.management',
                'view_mode': 'form',
                'res_id': rec.repair_id.id,
                'target': 'current',
            }


    def write(self, vals):
        if 'state' in vals:
            history_vals = [
                {
                    'ticket_id': ticket.id,
                    'old_state': ticket.state,
                    'new_state': vals['state'],
                    'changed_by': self.env.user.id,
                }
                for ticket in self
                if ticket.state != vals['state']
            ]
            if history_vals:
                self.env['asset.helpdesk.history'].sudo().create(history_vals)
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('ticket_number', 'New') == 'New':
                vals['ticket_number'] = (
                    self.env['ir.sequence'].next_by_code('asset.helpdesk') or 'New'
                )
        return super().create(vals_list)

    def action_start_progress(self):
        self.write({'state': 'in_progress'})

    def action_mark_solved(self):
        self.write({'state': 'solved'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def action_reset_to_open(self):
        self.write({'state': 'open'})

# asset_helpdesk_history.py

STATE_SELECTION = [
    ('open', 'Open'),
    ('in_progress', 'In Progress'),
    ('solved', 'Solved'),
    ('cancelled', 'Cancelled'),
]


class AssetHelpdeskHistory(models.Model):
    _name = 'asset.helpdesk.history'
    _description = 'Asset Helpdesk Ticket History'
    _order = 'change_date desc'

    ticket_id = fields.Many2one(
        'asset.helpdesk',
        string='Ticket',
        required=True,
        ondelete='cascade',
        index=True,
    )
    old_state = fields.Selection(STATE_SELECTION, string='From State')
    new_state = fields.Selection(STATE_SELECTION, string='To State')
    changed_by = fields.Many2one('res.users', string='Changed By')
    change_date = fields.Datetime(string='Changed On', default=fields.Datetime.now)

class RepairManagement(models.Model):
    _inherit = 'repair.management'


    def action_done(self):
        res = super().action_done()

        records = self.env['asset.helpdesk'].search([
            ('repair_id', '=', self.id)
        ])
        records.write({'state': 'solved'})

        return res

    def action_not_repairable(self):
        res = super().action_not_repairable()

        records = self.env['asset.helpdesk'].search([
            ('repair_id', '=', self.id)
        ])
        records.write({'state': 'solved'})

        return res