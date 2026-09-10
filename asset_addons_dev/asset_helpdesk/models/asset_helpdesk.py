# -*- coding: utf-8 -*-
from odoo import api, fields, models, _

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
    is_general = fields.Boolean(related="category_id.is_general", store=True)

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
    team_id = fields.Many2one(
        'asset.helpdesk.team',
        string='Team',
        required=True,
        tracking=True,
    )
    # Not stored: exists only so the view can build a domain on
    # assigned_to_id from it ("[('id', 'in', team_member_ids)]") - Odoo
    # view domains cannot dot into a related record's own field directly.
    team_member_ids = fields.Many2many(
        'res.users', related='team_id.member_ids', string='Team Members',
    )
    assigned_to_id = fields.Many2one(
        'res.users',
        string='Assigned To',
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

    # ── Requester / contact details ──────────────────────────────────────
    customer_id = fields.Many2one('res.users', string='Customer', tracking=True)
    email = fields.Char(string='Email')
    phone = fields.Char(string='Phone')
    department_id = fields.Many2one('hr.department', string='Department', tracking=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
    )

    @api.onchange('customer_id')
    def _onchange_customer_id(self):
        """Convenience prefill, not a lock - Department stays plain editable
        so a ticket can still record a different one than whatever the
        customer's employee record says, if that's ever needed."""
        if self.customer_id.employee_id:
            self.department_id = self.customer_id.employee_id.department_id

    @api.onchange('team_id')
    def _onchange_team_id(self):
        """Assigned To must belong to the selected Team - changing the team
        clears a choice made under the previous one instead of leaving a
        stale assignee that the domain would otherwise silently hide.
        """
        if self.assigned_to_id and self.assigned_to_id not in self.team_id.member_ids:
            self.assigned_to_id = False

    @api.constrains('team_id', 'assigned_to_id')
    def _check_assigned_to_is_team_member(self):
        """The view domain on assigned_to_id is UI-only and bypassable (API,
        import, dev tools) - this is the real guard.
        """
        for rec in self:
            if rec.assigned_to_id and rec.assigned_to_id not in rec.team_id.member_ids:
                raise UserError(_(
                    "%(user)s is not a member of %(team)s - assign someone "
                    "from that team, or change the team first."
                ) % {'user': rec.assigned_to_id.name, 'team': rec.team_id.name})

    def action_create_repair_management(self):
        for rec in self:
            if not rec.assigned_to_id:
                raise UserError(_("Please Select Engineer"))
            if not rec.assigned_to_id.employee_id:
                # engineer_id on repair.management is an hr.employee, not a
                # res.users - assigned_to_id must resolve to one, or the
                # repair gets created with no engineer and silently blocks
                # action_start() later (it requires engineer_id) with no
                # clue why.
                raise UserError(_(
                    "%s has no linked Employee record, so they cannot be "
                    "set as the repair engineer. Link one under Settings > "
                    "Users, or assign this ticket to someone else."
                ) % rec.assigned_to_id.name)
            if not rec.repair_id:
                if not rec.asset_id:
                    raise UserError(_("Please Enter Asset ID"))
                if rec.category_id.is_general:
                    issue_type = 'general'
                elif 'hardware' in (rec.category_id.name or '').lower():
                    issue_type = 'hardware'
                else:
                    issue_type = 'software'
                repair_record = self.env['repair.management'].create({
                        'engineer_id': rec.assigned_to_id.employee_id.id,
                        'asset_id': rec.asset_id.id,
                        'issue_type': issue_type,
                        'team_id': rec.team_id.id,
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

    def _send_portal_confirmation_email(self):
        """Acknowledgement email for a ticket raised through the public
        self-service form - confirms it was received and gives the
        submitter the ticket number to reference later.
        """
        template = self.env.ref(
            'asset_helpdesk.mail_template_ticket_confirmation',
            raise_if_not_found=False)
        for rec in self:
            if template and (rec.email or rec.customer_id.email):
                template.send_mail(rec.id, force_send=True)

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
        for rec in self:
            # Tickets whose category routes through repair (hardware/
            # software/general) are solved automatically when the repair
            # record reaches Done or Not Repairable (see the repair.management
            # override below) - this button is hidden for them in the view,
            # but guarded here too since state can be written any other way
            # (API, import, dev tools).
            if (rec.is_repair_flow or rec.is_general) \
                    and rec.repair_id.state not in ('done', 'not_repairable'):
                raise UserError(_(
                    "This ticket is tied to a repair - it is marked Solved "
                    "automatically once that repair is Done or Not "
                    "Repairable, not by hand."))
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

    # Only meaningful for Internal Team repairs - shown/used there so the
    # engineer picker can be restricted to that team's own members. Lives
    # here (not in asset_management) because asset.helpdesk.team is defined
    # in THIS module - asset_management must not depend on asset_helpdesk.
    team_id = fields.Many2one(
        'asset.helpdesk.team', string='Team', tracking=True,
        help="Restricts Assigned Engineer to this team's members. Carried "
             "over automatically from the originating ticket when a repair "
             "is created via Create Repair Management.",
    )
    # Not stored: exists only so the view can build a domain on engineer_id
    # from it - same reason asset.helpdesk.team_member_ids exists (Odoo view
    # domains cannot dot into a related record's own field directly).
    team_member_ids = fields.Many2many(
        'res.users', related='team_id.member_ids', string='Team Members',
    )

    @api.onchange('team_id')
    def _onchange_team_id(self):
        """Changing the team clears an engineer picked under the previous
        one, instead of leaving a stale choice the new domain would
        otherwise silently hide."""
        if self.engineer_id and self.engineer_id.user_id not in self.team_id.member_ids:
            self.engineer_id = False

    @api.constrains('team_id', 'engineer_id')
    def _check_engineer_is_team_member(self):
        """The view domain on engineer_id is UI-only and bypassable (API,
        import, dev tools) - this is the real guard. Only enforced once a
        team is actually set - a repair with no team picked yet (or a
        External Team repair, which has no use for this field) is not
        constrained by it.
        """
        for rec in self:
            if rec.team_id and rec.engineer_id \
                    and rec.engineer_id.user_id not in rec.team_id.member_ids:
                raise UserError(_(
                    "%(engineer)s is not a member of %(team)s - assign "
                    "someone from that team, or change the team first."
                ) % {'engineer': rec.engineer_id.name, 'team': rec.team_id.name})

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