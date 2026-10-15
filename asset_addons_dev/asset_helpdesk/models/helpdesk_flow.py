# -*- coding: utf-8 -*-
"""Helpdesk -> Repair -> Replace flow.

1. Raise Ticket: an employee describes the problem ("Room 1 AC not working").
   Requester, employee, department and location are filled automatically;
   asset_helpdesk_llm (when enabled) adds an AI suggestion.
2. The ticket incharge picks a team and a person: Assign.
3. The engineer categorises it and either solves it on the ticket or starts
   a Repair (Create Repair). Spares, spare requests (-> RFQ/PO) and costs
   live on the repair. A standby asset can be lent from the repair.
4. Repair Done: the asset goes back to its previous state, the ticket is
   Solved and the requester is emailed.
5. Not Repairable: the ticket waits for the requester's decision.
   * Replace: a new asset takes over the employee / department / location
     of the broken one; the broken one is scrapped.
   * A standby lent during the repair can be kept permanently instead.
   * No replacement: the broken asset is scrapped, the ticket closed.
"""
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AssetHelpdesk(models.Model):
    _inherit = 'asset.helpdesk'

    requester_employee_id = fields.Many2one(
        'hr.employee', string='Requester Employee',
        compute='_compute_requester_employee', store=True, readonly=False)
    location_id = fields.Many2one('asset.location', string='Location', tracking=True)
    asset_repair_count = fields.Integer(related='asset_id.repair_count', string='Past Repairs')
    asset_last_maintenance = fields.Date(related='asset_id.last_maintenance_date',
                                         string='Last Maintenance')
    asset_ticket_count = fields.Integer(compute='_compute_asset_ticket_count', string='Past Tickets')
    issue_category = fields.Selection([
        ('software', 'Software Issue'),
        ('hardware', 'Hardware Issue'),
        ('maintenance', 'General Maintenance'),
        ('repair', 'Repair'),
    ], string='Issue Category', tracking=True,
        help="Set by the engineer after checking the problem.")
    replacement_asset_id = fields.Many2one('asset.asset', string='Replacement Asset',
                                           readonly=True, copy=False, tracking=True)
    standby_process_id = fields.Many2one('asset.assign.process', string='Standby',
                                         compute='_compute_standby_process_id')
    can_decide_replace = fields.Boolean(compute='_compute_can_decide_replace')

    @api.depends('customer_id')
    def _compute_requester_employee(self):
        for rec in self:
            employee = rec.customer_id.sudo().employee_id
            if employee:
                rec.requester_employee_id = employee

    @api.depends('asset_id')
    def _compute_asset_ticket_count(self):
        for rec in self:
            rec.asset_ticket_count = self.search_count([
                ('asset_id', '=', rec.asset_id.id), ('id', '!=', rec._origin.id or 0)
            ]) if rec.asset_id else 0

    @api.depends('repair_id')
    def _compute_standby_process_id(self):
        for rec in self:
            # sudo: the requester (an ordinary employee) decides on it but
            # has no access to assign processes.
            rec.standby_process_id = self.env['asset.assign.process'].sudo().search([
                ('repair_ticket_id', '=', rec.repair_id.id), ('state', '=', 'replaced'),
            ], limit=1) if rec.repair_id else False

    @api.depends_context('uid')
    @api.depends('customer_id', 'team_id')
    def _compute_can_decide_replace(self):
        is_manager = self.env.user.has_group('asset_management.group_asset_manager')
        for rec in self:
            rec.can_decide_replace = (
                rec.customer_id == self.env.user or is_manager
                or self.env.user == rec.team_id.leader_id)

    @api.onchange('asset_id')
    def _onchange_asset_location(self):
        if self.asset_id and self.asset_id.location_id:
            self.location_id = self.asset_id.location_id
        if self.asset_id and self.asset_id.department_id and not self.department_id:
            self.department_id = self.asset_id.department_id

    @api.onchange('requester_employee_id')
    def _onchange_requester_employee(self):
        if self.requester_employee_id:
            self.department_id = self.requester_employee_id.department_id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # An employee raising a ticket can't read hr.employee details
            # (phone, department) - read them with elevated rights.
            # A ticket made from an incoming mail may have no user at all.
            if 'customer_id' in vals and not vals['customer_id']:
                continue
            user = self.env['res.users'].sudo().browse(vals.get('customer_id') or self.env.uid)
            employee = user.employee_id
            vals.setdefault('email', user.email)
            if employee:
                vals.setdefault('department_id', employee.department_id.id)
                vals.setdefault('phone', employee.mobile_phone or employee.work_phone)
            if vals.get('asset_id') and not vals.get('location_id'):
                vals['location_id'] = self.env['asset.asset'].sudo().browse(vals['asset_id']).location_id.id
        tickets = super().create(vals_list)
        for ticket in tickets.sudo():
            ticket._notify_new_ticket()
        return tickets

    # ── notifications ────────────────────────────────────────────────────
    def _ticket_notify(self, users, subject, body):
        self.ensure_one()
        partners = (users - self.env.user).filtered('partner_id').partner_id
        if partners:
            self.message_post(body=body, subject=subject, partner_ids=partners.ids,
                              message_type='comment', subtype_xmlid='mail.mt_comment')
        else:
            self.message_post(body=body)

    def _notify_new_ticket(self):
        """Tell the people who triage tickets that one has come in."""
        leaders = self.env['asset.helpdesk.team'].sudo().search([]).leader_id
        managers = self.env.ref('asset_management.group_asset_manager').sudo().all_user_ids \
            if 'all_user_ids' in self.env['res.groups']._fields else self.env['res.users']
        self.sudo()._ticket_notify(
            leaders | managers,
            _("New ticket %s: %s", self.ticket_number, self.subject),
            Markup('<p>%s raised a ticket: <b>%s</b></p><p>Location: %s - Department: %s</p>') % (
                self.customer_id.name or '', self.subject,
                self.location_id.complete_name or '-', self.department_id.name or '-'))

    def _requester_name(self):
        self.ensure_one()
        from email.utils import parseaddr
        return (self.customer_id.name or self.mail_id.partner_id.name
                or parseaddr(self.mail_id.email_from or self.email or '')[0]
                or _('Customer'))

    def _notify_requester(self, subject, body):
        """Tell the requester. A ticket that came from a mail answers by
        mail on that thread, so it is in the conversation too."""
        self.ensure_one()
        if self.mail_id:
            self.mail_id._mail_customer(body)
            self.message_post(body=body, subject=subject)
        else:
            self._ticket_notify(self.customer_id, subject, body)

    # ── triage ───────────────────────────────────────────────────────────
    def action_mark_solved(self):
        res = super().action_mark_solved()
        for rec in self:
            rec._notify_requester(_("Ticket solved: %s", rec.ticket_number),
                                  Markup('<p>Your ticket <b>%s</b> (%s) has been solved.</p>') % (
                                      rec.ticket_number, rec.subject))
        return res

    # ── not repairable: replace decision ─────────────────────────────────
    def _check_replace_decision(self):
        for rec in self:
            if rec.state != 'replace_pending':
                raise UserError(_("This ticket is not waiting for a replace decision."))
            if not rec.can_decide_replace:
                raise UserError(_("Only the requester (or an asset manager) can decide this."))

    def action_replace_yes(self):
        """Requester wants a replacement: pick the new asset."""
        self._check_replace_decision()
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Replace Asset"),
            'res_model': 'asset.helpdesk.replace.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_ticket_id': self.id},
        }

    def action_keep_standby(self):
        """Not repairable, and a standby is already with the employee: keep it."""
        self._check_replace_decision()
        for rec in self:
            standby = rec.sudo().standby_process_id
            if not standby:
                raise UserError(_("No standby asset was given for this ticket."))
            rec.sudo()._replace_with(standby.replacement_asset_id, standby_process=standby)

    def action_replace_no(self):
        """No replacement wanted: return any standby, scrap the broken asset."""
        self._check_replace_decision()
        for rec in self:
            rec = rec.sudo()
            standby = rec.standby_process_id
            if standby:
                standby.sudo().replacement_asset_id.write({
                    'assigned_employee_id': False, 'department_id': False, 'location_id': False,
                    'assignment_date': False, 'state': 'submit'})
                standby.sudo().state = 'done'
            rec.asset_id.write({'state': 'scrapped'})
            rec.state = 'solved'
            rec.message_post(body=_("No replacement wanted. %s scrapped.", rec.asset_id.display_name))

    def _replace_with(self, new_asset, standby_process=False):
        """The new asset takes over the old one's employee / department /
        location; the old one is scrapped. Callers check who may decide
        (_check_replace_decision); the requester has no asset write rights,
        so the changes themselves run with elevated rights."""
        self.ensure_one()
        self = self.sudo()
        new_asset = new_asset.sudo()
        standby_process = standby_process and standby_process.sudo()
        old = self.asset_id
        new_asset.write({
            'assigned_employee_id': old.assigned_employee_id.id,
            'department_id': old.department_id.id,
            'location_id': old.location_id.id,
            'assignment_date': fields.Date.context_today(self),
            'state': 'assigned',
        })
        old.write({'state': 'scrapped'})
        if standby_process:
            standby_process.state = 'done'
        elif self.standby_process_id:
            # A different asset was chosen: the standby comes back to stock.
            sb = self.standby_process_id.sudo()
            sb.replacement_asset_id.write({
                'assigned_employee_id': False, 'department_id': False, 'location_id': False,
                    'assignment_date': False, 'state': 'submit'})
            sb.state = 'done'
        self.write({'replacement_asset_id': new_asset.id, 'state': 'replaced'})
        self._notify_requester(
            _("Asset replaced: %s", self.ticket_number),
            Markup('<p>%s could not be repaired and has been replaced with <b>%s</b>.</p>') % (
                old.display_name, new_asset.display_name))


class AssetHelpdeskReplaceWizard(models.TransientModel):
    _name = 'asset.helpdesk.replace.wizard'
    _description = 'Replace a Not Repairable Asset'

    ticket_id = fields.Many2one('asset.helpdesk', required=True)
    old_asset_id = fields.Many2one(related='ticket_id.asset_id', string='Current Asset')
    category_id = fields.Many2one(related='ticket_id.asset_id.category_id')
    new_asset_id = fields.Many2one(
        'asset.asset', string='New Asset', required=True,
        domain="[('category_id', '=', category_id), ('state', '=', 'submit'), ('in_repair', '=', False)]")

    def action_replace(self):
        self.ensure_one()
        new = self.new_asset_id
        if new.state != 'submit' or new.in_repair or new.category_id != self.category_id:
            raise UserError(_("Pick a Submitted asset of the same category that is not under repair."))
        self.ticket_id._check_replace_decision()
        self.ticket_id.sudo()._replace_with(new)
        return {'type': 'ir.actions.act_window_close'}


class RepairManagement(models.Model):
    _inherit = 'repair.management'

    helpdesk_ticket_ids = fields.One2many('asset.helpdesk', 'repair_id', string='Tickets')

    def _scrap_on_not_repairable(self):
        # With a ticket, the requester decides first (replace / keep standby
        # / no replacement) - the asset is scrapped as part of that.
        return not self.helpdesk_ticket_ids

    def action_done(self):
        res = super().action_done()
        for rec in self:
            for ticket in rec.helpdesk_ticket_ids.filtered(
                    lambda t: t.state not in ('solved', 'replaced', 'cancelled')):
                where = ", ".join(n for n in [rec.asset_id.assigned_employee_id.name,
                                              rec.asset_id.location_id.complete_name] if n)
                if rec.standby_kept:
                    ticket._notify_requester(
                        _("Repair completed: %s", ticket.ticket_number),
                        Markup('<p>Dear %s,</p><p>The repair of <b>%s</b> is complete and it has been '
                               'returned to stock. The temporary asset you were given stays with you '
                               'permanently. Ticket <b>%s</b> is now resolved and closed.</p>') % (
                            ticket._requester_name(), rec.asset_id.display_name, ticket.ticket_number))
                    ticket.with_context(helpdesk_closing_mail_sent=True).state = 'solved'
                    continue
                ticket._notify_requester(
                    _("Repair completed: %s", ticket.ticket_number),
                    Markup('<p>Dear %s,</p><p>Your asset <b>%s</b> has been repaired and is assigned '
                           'back%s. Ticket <b>%s</b> is now resolved and closed.</p>'
                           '<p>If the issue comes back, reply to this mail.</p>') % (
                        ticket._requester_name(), rec.asset_id.display_name,
                        Markup(' to <b>%s</b>') % where if where else '', ticket.ticket_number))
                # The mail above already says it is closed.
                ticket.with_context(helpdesk_closing_mail_sent=True).state = 'solved'
        return res

    def action_not_repairable(self):
        res = super().action_not_repairable()
        for rec in self:
            for ticket in rec.helpdesk_ticket_ids.filtered(
                    lambda t: t.state not in ('solved', 'replaced', 'cancelled')):
                ticket.state = 'replace_pending'
                standby = ticket.standby_process_id
                extra = Markup('<p>You already have the standby asset <b>%s</b>: you can keep it '
                               'permanently.</p>') % standby.replacement_asset_id.display_name \
                    if standby else Markup('')
                ticket._notify_requester(
                    _("Asset not repairable - replace? %s", ticket.ticket_number),
                    Markup('<p>%s could not be repaired.</p>%s<p>Open ticket <b>%s</b> and choose '
                           '<b>Replace</b> or <b>No Replacement</b>.</p>') % (
                        rec.asset_id.display_name, extra, ticket.ticket_number))
        return res
