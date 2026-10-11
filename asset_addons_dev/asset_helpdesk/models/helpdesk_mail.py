# -*- coding: utf-8 -*-
"""All Mails: every helpdesk request, whether it came in by email or was
raised from the Raise Ticket screen.

    Mail (New) --Create Ticket--> Ticket --Create Task--> Repair task
    task closed -> ticket solved -> mail Closed (requester emailed)

Each mail gets a ticket number the moment it arrives and the sender gets an
automatic acknowledgement. Replies that keep "[Ticket#<number>]" in the
subject land on the same mail record instead of opening a new one.
"""
import re

from markupsafe import Markup

from odoo import _, api, fields, models, tools
from odoo.exceptions import UserError

TICKET_TAG = re.compile(r'\[Ticket#(\d+)\]', re.IGNORECASE)


class AssetHelpdeskMail(models.Model):
    _name = 'asset.helpdesk.mail'
    _description = 'Helpdesk Mail'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'
    _rec_name = 'subject'

    ticket_ref = fields.Char(string='Ticket No.', readonly=True, copy=False, index=True)
    subject = fields.Char(string='Subject', required=True, tracking=True)
    body = fields.Html(string='Message', sanitize_style=True)
    email_from = fields.Char(string='Customer Email', tracking=True)
    partner_id = fields.Many2one('res.partner', string='Customer')
    user_id = fields.Many2one('res.users', string='Raised By', default=lambda self: self.env.user,
                              readonly=True)
    source = fields.Selection([('email', 'Email'), ('portal', 'Raise Ticket')],
                              string='Source', default='portal', readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('new', 'New'),
        ('created', 'Created'),
        ('closed', 'Closed'),
        ('archived', 'Archived'),
    ], string='State', default='draft', tracking=True,
        help="Raise Ticket starts as Draft until Submit; an incoming email starts as New.")
    attachment_ids = fields.Many2many('ir.attachment', 'asset_helpdesk_mail_attachment_rel',
                                      'mail_id', 'attachment_id', string='Attachments')
    ticket_id = fields.Many2one('asset.helpdesk', string='Ticket', readonly=True, copy=False)
    ticket_state = fields.Selection(related='ticket_id.state', string='Ticket Status')

    # ── creation ─────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Raise Ticket: a draft until the user submits it; the ticket
            # number and acknowledgement come with Submit.
            if vals.get('source', 'portal') == 'portal' and 'state' not in vals:
                vals['state'] = 'draft'
            if not vals.get('email_from') and vals.get('source', 'portal') == 'portal':
                user = self.env['res.users'].browse(vals.get('user_id') or self.env.uid)
                vals['email_from'] = user.email_formatted or user.email
                vals.setdefault('partner_id', user.partner_id.id)
        records = super().create(vals_list)
        records.filtered(lambda m: m.state == 'new')._register_request()
        return records

    def _register_request(self):
        for rec in self:
            if not rec.ticket_ref:
                rec.ticket_ref = self.env['ir.sequence'].sudo().next_by_code('asset.helpdesk.mail') or '/'
            rec._send_acknowledgement()

    def action_submit(self):
        """Raise Ticket -> Submit: the request becomes the first mail of the
        conversation (from the user to the helpdesk), then the
        acknowledgement goes back to the user's email."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only a draft request can be submitted."))
            user = rec.user_id or self.env.user
            if not user.email:
                raise UserError(_("Your user has no email address. Add one to your contact "
                                  "(Preferences) so the helpdesk can reply to you."))
            rec.write({'state': 'new', 'email_from': user.email_formatted,
                       'partner_id': user.partner_id.id})
            if not rec.ticket_ref:
                rec.ticket_ref = self.env['ir.sequence'].sudo().next_by_code('asset.helpdesk.mail') or '/'
            for att in rec.attachment_ids:
                att.sudo().write({'res_model': rec._name, 'res_id': rec.id})
            rec.sudo().with_context(
                mail_post_autofollow=False,
                helpdesk_line_vals={'email_to': rec._helpdesk_from_address(), 'is_read': False},
            ).message_post(
                body=rec.body, subject=rec.subject, author_id=user.partner_id.id,
                email_from=user.email_formatted, attachment_ids=rec.attachment_ids.ids,
                message_type='email', subtype_xmlid='mail.mt_comment')
            rec._send_acknowledgement()

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        """Incoming email. A reply carrying [Ticket#N] in the subject is
        added to that ticket's mail instead of creating a new one."""
        subject = msg_dict.get('subject') or _('No Subject')
        match = TICKET_TAG.search(subject)
        if match:
            existing = self.search([('ticket_ref', '=', match.group(1))], limit=1)
            if existing:
                return existing
        email_from = msg_dict.get('email_from') or ''
        values = dict(custom_values or {}, **{
            'state': 'new',
            'subject': subject,
            'body': msg_dict.get('body'),
            'email_from': email_from,
            'partner_id': msg_dict.get('author_id') or False,
            'source': 'email',
            'user_id': False,
        })
        return super().message_new(msg_dict, custom_values=values)

    def _reply_subject(self):
        self.ensure_one()
        return '[Ticket#%s] %s' % (self.ticket_ref, TICKET_TAG.sub('', self.subject or '').strip())

    def _send_mail_to_customer(self, subject, body_html):
        self.ensure_one()
        if not self.email_from:
            return
        self.env['mail.mail'].sudo().create({
            'subject': subject,
            'body_html': body_html,
            'email_to': self.email_from,
            'email_from': self._helpdesk_from_address(),
            'model': self._name,
            'res_id': self.id,
            'auto_delete': False,
        }).send()

    def _mail_customer(self, body):
        """Mail the customer from the helpdesk mailbox and keep it in the
        ticket conversation (mail entries)."""
        for rec in self:
            rec._send_mail_to_customer(rec._reply_subject(), body)
            rec.sudo().message_post(body=body, subject=rec._reply_subject(),
                                    email_from=rec._helpdesk_from_address(),
                                    message_type='comment', subtype_xmlid='mail.mt_note')

    def _helpdesk_from_address(self):
        ICP = self.env['ir.config_parameter'].sudo()
        return ICP.get_param('asset_helpdesk.mailbox') or self.env.company.email_formatted

    def _send_acknowledgement(self):
        self.ensure_one()
        body = Markup(
            '<p>Thank you for contacting us!</p>'
            '<p>We acknowledge your message. This is an automatic response. '
            'A new Ticket <b>%(ref)s</b> has been created. You will be receiving a more '
            'personalized update soon.</p>'
            '<p>Any further communication you have with us will automatically become part '
            'of the log for this call. Please do not alter the Subject line when replying '
            'via email since it contains the identifying "TICKET NUMBER". When contacting '
            'us, please refer to this number %(ref)s.</p>'
            '<p>We will contact you as soon as possible.</p>') % {'ref': self.ticket_ref}
        self._send_mail_to_customer(self._reply_subject(), body)
        self.message_post(body=body, subject=self._reply_subject(),
                          email_from=self._helpdesk_from_address(),
                          message_type='comment', subtype_xmlid='mail.mt_note')

    # ── actions ──────────────────────────────────────────────────────────
    def action_create_ticket(self):
        self.ensure_one()
        if self.ticket_id:
            return self.action_open_ticket()
        if self.state != 'new':
            raise UserError(_("Only a New mail can be turned into a ticket."))
        email = tools.email_normalize(self.email_from or '')
        user = self.user_id or (email and self.env['res.users'].sudo().search(
            [('email', '=ilike', email)], limit=1))
        ticket = self.env['asset.helpdesk'].create({
            'subject': self.subject,
            'problem_html': self.body,
            'customer_id': user.id if user else False,
            'email': email or self.email_from,
            'mail_id': self.id,
        })
        self.write({'ticket_id': ticket.id, 'state': 'created'})
        self.message_post(body=_("Ticket %s created.", ticket.ticket_number))
        return self.action_open_ticket()

    def action_open_ticket(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'asset.helpdesk',
            'view_mode': 'form',
            'res_id': self.ticket_id.id,
        }

    def action_archive_mail(self):
        self.filtered(lambda m: m.state == 'new').write({'state': 'archived'})

    def action_reset_new(self):
        self.filtered(lambda m: m.state == 'archived').write({'state': 'new'})

    def action_mail_reply(self):
        """Reply to the customer by email from the mail's chatter composer."""
        self.ensure_one()
        partner = self.partner_id
        if not partner and self.email_from:
            partner = self.env['res.partner'].sudo().find_or_create(self.email_from)
            self.partner_id = partner
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mail.compose.message',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_model': self._name,
                'default_res_ids': self.ids,
                'default_composition_mode': 'comment',
                'default_subject': self._reply_subject(),
                'default_partner_ids': partner.ids,
                'default_subtype_xmlid': 'mail.mt_comment',
            },
        }

    def _close_from_ticket(self):
        for rec in self.filtered(lambda m: m.state == 'created'):
            rec.state = 'closed'
            if self.env.context.get('helpdesk_closing_mail_sent'):
                continue
            rec._mail_customer(
                Markup('<p>Your ticket <b>%s</b> has been resolved and closed.</p>'
                       '<p>If you need further help, reply to this mail or raise a new ticket.</p>')
                % rec.ticket_ref)


class AssetHelpdesk(models.Model):
    _inherit = 'asset.helpdesk'

    _rec_name = 'ticket_number'

    mail_id = fields.Many2one('asset.helpdesk.mail', string='Source Mail', readonly=True, copy=False)
    problem_html = fields.Html(string='Problem', sanitize_style=True)

    @api.depends('ticket_number')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = 'Ticket#%s' % rec.ticket_number if rec.ticket_number not in (False, 'New') \
                else _('New Ticket')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Same number as the mail it came from (Ticket#2026101000462);
            # a ticket made directly takes the next number of that series.
            if vals.get('ticket_number', 'New') == 'New':
                mail = self.env['asset.helpdesk.mail'].browse(vals.get('mail_id'))
                vals['ticket_number'] = mail.ticket_ref or self.env['ir.sequence'].sudo().next_by_code(
                    'asset.helpdesk.mail') or 'New'
            # notes (plain text) feeds the AI suggestion.
            if vals.get('problem_html') and not vals.get('notes'):
                vals['notes'] = tools.html2plaintext(vals['problem_html'])
        return super().create(vals_list)

    conversation_ids = fields.Many2many(
        'mail.message', string='Conversation', compute='_compute_conversation_ids')

    @api.depends('mail_id.message_ids')
    def _compute_conversation_ids(self):
        for rec in self:
            rec.conversation_ids = rec.mail_id.message_ids.filtered(
                lambda m: m.message_type in ('email', 'comment') and m.body
            ).sorted('id', reverse=True)

    def action_mail_reply(self):
        """Reply to the customer; goes out with [Ticket#N] and stays in
        the conversation."""
        self.ensure_one()
        if not self.mail_id:
            raise UserError(_("This ticket did not come from a mail - there is no one to reply to."))
        return self.mail_id.action_mail_reply()

    def action_open_mail(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'asset.helpdesk.mail',
                'view_mode': 'form', 'res_id': self.mail_id.id}

    def write(self, vals):
        res = super().write(vals)
        if vals.get('state') in ('solved', 'replaced'):
            self.mail_id._close_from_ticket()
        return res

    def action_create_repair_management(self):
        """Create Task: open a new repair task prefilled from the ticket; the
        engineer completes it (asset, type, engineer) and saves."""
        self.ensure_one()
        if self.repair_id:
            return {
                'type': 'ir.actions.act_window', 'res_model': 'repair.management',
                'view_mode': 'form', 'res_id': self.repair_id.id,
            }
        if self.state not in ('open', 'created'):
            raise UserError(_("A task can only be created for an open ticket."))
        return {
            'type': 'ir.actions.act_window',
            'name': _("New Task"),
            'res_model': 'repair.management',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'helpdesk_ticket_id': self.id,
                'default_asset_id': self.asset_id.id,
                'default_description': self.notes or self.subject,
            },
        }


class RepairManagement(models.Model):
    _inherit = 'repair.management'

    standby_needed = fields.Selection(
        [('yes', 'Yes'), ('no', 'No')], string='Temporary Asset Needed?', tracking=True,
        help="Ask the user whether they need a temporary (standby) asset while "
             "theirs is being repaired.")

    @api.model_create_multi
    def create(self, vals_list):
        repairs = super().create(vals_list)
        ticket_id = self.env.context.get('helpdesk_ticket_id')
        if ticket_id:
            ticket = self.env['asset.helpdesk'].browse(ticket_id)
            for repair in repairs[:1]:
                ticket.write({'repair_id': repair.id, 'state': 'created'})
                if not ticket.asset_id:
                    ticket.asset_id = repair.asset_id
                repair._notify_repair_mailbox(ticket)
        return repairs

    def _notify_repair_mailbox(self, ticket):
        """Copy of every new task to the repair mailbox (for now
        deepak@futurenet.in; Settings: asset_helpdesk.repair_notify_email)."""
        to = self.env['ir.config_parameter'].sudo().get_param(
            'asset_helpdesk.repair_notify_email', 'deepak@futurenet.in')
        if not to:
            return
        self.env['mail.mail'].sudo().create({
            'subject': _("New repair task %s - %s", self.name, ticket.subject),
            'body_html': Markup('<p>Task <b>%s</b> created from ticket %s.</p><p>Asset: %s<br/>'
                                'Engineer: %s<br/>Requested by: %s</p><p>%s</p>') % (
                self.name, ticket.ticket_number, self.asset_id.display_name or '-',
                self.engineer_id.name or '-', ticket.customer_id.name or ticket.email or '-',
                self.description or ''),
            'email_to': to,
            'auto_delete': False,
        }).send()

    def action_mail_reply(self):
        self.ensure_one()
        ticket = self.helpdesk_ticket_ids[:1]
        if not ticket:
            raise UserError(_("This task has no helpdesk ticket to reply on."))
        return ticket.action_mail_reply()

    def action_give_standby(self):
        """User said yes to a temporary asset: open the standby process."""
        self.ensure_one()
        if self.standby_needed != 'yes':
            raise UserError(_("Set 'Temporary Asset Needed?' to Yes first."))
        return self.action_open_assign_process()


class AssetHelpdeskMailMap(models.Model):
    """A reply that lost the [Ticket#N] tag arrives as a new mail: map it to
    its parent ticket so the conversation stays in one place."""
    _inherit = 'asset.helpdesk.mail'

    parent_mail_id = fields.Many2one('asset.helpdesk.mail', string='Parent Ticket', readonly=True,
                                     copy=False)
    state = fields.Selection(selection_add=[('merged', 'Merged')], ondelete={'merged': 'set default'})

    def action_map_to_ticket(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Map to Parent Ticket"),
            'res_model': 'asset.helpdesk.mail.map.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_mail_id': self.id},
        }

    def _merge_into(self, parent):
        self.ensure_one()
        if parent == self or parent.parent_mail_id:
            raise UserError(_("Pick the original (parent) ticket."))
        msgs = self.env['mail.message'].sudo().search([
            ('model', '=', self._name), ('res_id', '=', self.id),
            ('message_type', 'in', ('email', 'comment'))])
        msgs.write({'res_id': parent.id})
        self.env['asset.helpdesk.mail.line'].sudo().search([('mail_id', '=', self.id)]).write(
            {'mail_id': parent.id})
        self.write({'parent_mail_id': parent.id, 'state': 'merged'})
        parent.message_post(body=_("Mail %s (%s) mapped to this ticket.", self.ticket_ref, self.subject))


class AssetHelpdeskMailMapWizard(models.TransientModel):
    _name = 'asset.helpdesk.mail.map.wizard'
    _description = 'Map Mail to Parent Ticket'

    mail_id = fields.Many2one('asset.helpdesk.mail', required=True)
    parent_mail_id = fields.Many2one(
        'asset.helpdesk.mail', string='Parent Ticket', required=True,
        domain="[('id', '!=', mail_id), ('parent_mail_id', '=', False), ('state', 'in', ('new', 'created'))]")

    def action_map(self):
        self.ensure_one()
        self.mail_id._merge_into(self.parent_mail_id)
        return {'type': 'ir.actions.act_window_close'}


class RepairManagementMails(models.Model):
    _inherit = 'repair.management'

    helpdesk_mail_id = fields.Many2one(
        'asset.helpdesk.mail', compute='_compute_helpdesk_mail', string='Ticket Mail')
    mail_count = fields.Integer(compute='_compute_helpdesk_mail', string='Mails')

    @api.depends('helpdesk_ticket_ids.mail_id')
    def _compute_helpdesk_mail(self):
        Message = self.env['mail.message']
        for rec in self:
            mail = rec.helpdesk_ticket_ids[:1].mail_id
            rec.helpdesk_mail_id = mail
            rec.mail_count = Message.search_count([
                ('model', '=', 'asset.helpdesk.mail'), ('res_id', '=', mail.id),
                ('message_type', 'in', ('email', 'comment'))]) if mail else 0

    def action_view_mails(self):
        """Webmail-style list of every mail on this task's ticket."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Mails - %s", self.helpdesk_mail_id.display_name or self.name),
            'res_model': 'mail.message',
            'view_mode': 'list,form',
            'views': [(self.env.ref('asset_helpdesk.view_helpdesk_mail_message_list').id, 'list'),
                      (False, 'form')],
            'domain': [('model', '=', 'asset.helpdesk.mail'), ('res_id', '=', self.helpdesk_mail_id.id),
                       ('message_type', 'in', ('email', 'comment'))],
            'context': {'create': False},
        }

    def action_assign_task(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Assign Task"),
            'res_model': 'repair.assign.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_repair_id': self.id, 'default_team_id': self.team_id.id},
        }


class RepairAssignWizard(models.TransientModel):
    _name = 'repair.assign.wizard'
    _description = 'Assign Repair Task'

    repair_id = fields.Many2one('repair.management', required=True)
    team_id = fields.Many2one('asset.helpdesk.team', string='Team', required=True)
    team_member_ids = fields.Many2many('res.users', related='team_id.member_ids')
    user_id = fields.Many2one('res.users', string='Assign To', required=True,
                              domain="[('id', 'in', team_member_ids)]")

    @api.onchange('team_id')
    def _onchange_team_id(self):
        if self.user_id not in self.team_id.member_ids:
            self.user_id = False

    def action_assign(self):
        self.ensure_one()
        employee = self.user_id.employee_id
        if not employee:
            raise UserError(_("%s has no linked Employee record.", self.user_id.name))
        repair = self.repair_id
        repair.write({'team_id': self.team_id.id, 'engineer_id': employee.id})
        partners = self.user_id.partner_id
        repair.message_post(
            body=Markup('<p>Task <b>%s</b> assigned to you by %s.</p>') % (repair.name, self.env.user.name),
            subject=_("Task assigned: %s", repair.name),
            partner_ids=partners.ids, message_type='comment', subtype_xmlid='mail.mt_comment')
        return {'type': 'ir.actions.act_window_close'}
