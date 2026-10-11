# -*- coding: utf-8 -*-
"""One record per email in a helpdesk conversation - incoming or sent -
with real From / To / Cc / Subject / Body, so a ticket's mails read like a
mailbox instead of chatter.

Lines are written automatically whenever a message is posted on a
helpdesk mail thread (incoming email, acknowledgement, Send Mail).
"""
from markupsafe import Markup

from odoo import _, api, fields, models, tools
from odoo.exceptions import UserError
from odoo.tools.mail import email_split_and_format


class AssetHelpdeskMailLine(models.Model):
    _name = 'asset.helpdesk.mail.line'
    _description = 'Helpdesk Mail Entry'
    _order = 'date desc, id desc'
    _rec_name = 'subject'

    mail_id = fields.Many2one('asset.helpdesk.mail', string='Thread', required=True,
                              ondelete='cascade', index=True)
    ticket_ref = fields.Char(related='mail_id.ticket_ref', string='Ticket No.', store=True)
    ticket_id = fields.Many2one(related='mail_id.ticket_id', store=True)
    message_id = fields.Many2one('mail.message', string='Message', ondelete='set null')
    direction = fields.Selection([('in', 'Received'), ('out', 'Sent')], required=True, default='in')
    date = fields.Datetime(default=fields.Datetime.now, required=True)
    email_from = fields.Char(string='From')
    email_to = fields.Char(string='To')
    email_cc = fields.Char(string='Cc')
    subject = fields.Char(string='Subject')
    body = fields.Html(string='Message', sanitize_style=True)
    preview = fields.Char(compute='_compute_preview', store=True)
    attachment_ids = fields.Many2many('ir.attachment', string='Attachments')
    attachment_count = fields.Integer(compute='_compute_attachment_count')
    author_id = fields.Many2one('res.partner', string='Author')
    is_read = fields.Boolean(string='Read', default=False)

    @api.depends('body')
    def _compute_preview(self):
        for rec in self:
            text = tools.html2plaintext(rec.body or '').replace('\n', ' ').strip()
            rec.preview = text[:140]

    @api.depends('attachment_ids')
    def _compute_attachment_count(self):
        for rec in self:
            rec.attachment_count = len(rec.attachment_ids)

    def action_open(self):
        self.ensure_one()
        if not self.is_read:
            self.sudo().is_read = True
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'view_mode': 'form',
            'res_id': self.id,
            'views': [(self.env.ref('asset_helpdesk.view_helpdesk_mail_line_form').id, 'form')],
        }

    def action_reply(self):
        return self.mail_id.action_mail_reply()

    def action_forward(self):
        self.ensure_one()
        return self.mail_id._open_send_mail(forward_line=self)

    def action_open_thread(self):
        self.ensure_one()
        return self.mail_id.action_view_lines()


class AssetHelpdeskMail(models.Model):
    _inherit = 'asset.helpdesk.mail'

    line_ids = fields.One2many('asset.helpdesk.mail.line', 'mail_id', string='Mails')
    line_count = fields.Integer(compute='_compute_line_count', string='Mails')

    @api.depends('line_ids')
    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)

    def _message_post_after_hook(self, message, msg_vals):
        res = super()._message_post_after_hook(message, msg_vals)
        if message.message_type in ('email', 'comment') and message.body:
            self._line_from_message(message)
        return res

    def _line_from_message(self, message):
        """Mirror a posted message into a mail entry (From / To / Cc ...)."""
        extra = self.env.context.get('helpdesk_line_vals') or {}
        mailbox = self._helpdesk_from_address()
        incoming = message.message_type == 'email'
        to = message.incoming_email_to if incoming else ', '.join(
            p.email_formatted for p in message.partner_ids if p.email)
        for rec in self:
            self.env['asset.helpdesk.mail.line'].sudo().create(dict({
                'mail_id': rec.id,
                'message_id': message.id,
                'direction': 'in' if incoming else 'out',
                'date': message.date,
                'email_from': message.email_from or (message.author_id.email_formatted if not incoming else ''),
                'email_to': to or (mailbox if incoming else rec.email_from),
                'email_cc': message.incoming_email_cc if incoming else False,
                'subject': message.subject or rec._reply_subject(),
                'body': message.body,
                'attachment_ids': [(6, 0, message.attachment_ids.ids)],
                'author_id': message.author_id.id,
                'is_read': not incoming,
            }, **extra))

    thread_html = fields.Html(compute='_compute_thread_html', sanitize=False, string='Conversation')
    thread_participants = fields.Char(compute='_compute_thread_html')

    @api.depends('line_ids')
    def _compute_thread_html(self):
        for rec in self:
            lines = rec.line_ids.sorted(lambda l: (l.date, l.id), reverse=True)
            rec.thread_html = Markup('').join(_render_mail_card(l, first=(i == 0)) for i, l in enumerate(lines)) \
                or Markup('<p class="text-muted">No mails yet.</p>')
            people, seen = [], set()
            for l in lines:
                for addr in email_split_and_format(', '.join(filter(None, [l.email_from, l.email_to, l.email_cc]))):
                    key = tools.email_normalize(addr) or addr
                    if key not in seen:
                        seen.add(key)
                        people.append(tools.parse_contact_from_email(addr)[0] or addr)
            rec.thread_participants = ', '.join(people)

    def _thread_partners(self):
        """Customer (To) and everyone ever copied on the thread (Cc)."""
        self.ensure_one()
        Partner = self.env['res.partner'].sudo()
        mailbox = tools.email_normalize(self._helpdesk_from_address() or '')
        to = self.partner_id or (self.email_from and Partner.find_or_create(self.email_from)) or Partner
        cc = Partner
        for line in self.line_ids:
            for addr in email_split_and_format(line.email_cc or ''):
                if tools.email_normalize(addr) not in (mailbox, to.email_normalized):
                    cc |= Partner.find_or_create(addr)
        return to, cc - to

    def _open_send_mail(self, to=None, cc=None, forward_line=None):
        self.ensure_one()
        default_to, default_cc = self._thread_partners()
        if forward_line:
            to_ids, cc_ids, atts = [], [], forward_line.attachment_ids.ids
            body = Markup('<p><br/></p><p>---------- Forwarded message ----------<br/>From: %s<br/>'
                          'Date: %s</p>%s') % (forward_line.email_from or '', forward_line.date,
                                               forward_line.body or '')
        else:
            to_ids = (to if to is not None else default_to).ids
            cc_ids = (cc if cc is not None else default_cc).ids
            atts, body = [], ''
        return {
            'type': 'ir.actions.act_window',
            'name': _("Forward") if forward_line else _("Reply"),
            'res_model': 'asset.helpdesk.send.mail.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_mail_id': self.id,
                'default_partner_to_ids': [(6, 0, to_ids)],
                'default_partner_cc_ids': [(6, 0, cc_ids)],
                'default_body': body,
                'default_attachment_ids': [(6, 0, atts)],
            },
        }

    def action_mail_reply(self):
        self.ensure_one()
        return self._open_send_mail()

    def action_view_lines(self):
        """Open the whole conversation on one page."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self._reply_subject(),
            'res_model': 'asset.helpdesk.mail',
            'view_mode': 'form',
            'res_id': self.id,
            'views': [(self.env.ref('asset_helpdesk.view_helpdesk_mail_thread_form').id, 'form')],
            'target': 'current',
        }


def _render_mail_card(line, first=False):
    """One mail of the conversation as an email-client card."""
    att = Markup('').join(
        Markup('<a class="o_hd_att" href="/web/content/%s?download=true" target="_blank">'
               '<i class="fa fa-paperclip"/> %s</a>') % (a.id, a.name) for a in line.attachment_ids)
    cc = Markup('<div class="o_hd_card_row"><span>Cc</span>%s</div>') % line.email_cc if line.email_cc else Markup('')
    initial = (tools.parse_contact_from_email(line.email_from or '?')[0] or line.email_from or '?')[:1].upper()
    return Markup(
        '<details class="o_hd_card o_hd_card_%(dir)s" %(open)s>'
        '<summary class="o_hd_card_head">'
        '<span class="o_hd_card_avatar">%(initial)s</span>'
        '<span class="o_hd_card_who"><b>%(from)s</b><span class="o_hd_card_preview">%(preview)s</span></span>'
        '<span class="o_hd_card_when"><span class="o_hd_badge o_hd_badge_%(dir)s">%(dirlabel)s</span>%(date)s</span>'
        '</summary>'
        '<div class="o_hd_card_meta"><div class="o_hd_card_row"><span>To</span>%(to)s</div>%(cc)s</div>'
        '<div class="o_hd_card_body">%(body)s</div>'
        '<div class="o_hd_card_atts">%(att)s</div>'
        '</details>') % {
        'dir': line.direction, 'open': Markup('open') if first else Markup(''),
        'initial': initial, 'from': line.email_from or '', 'preview': line.preview or '',
        'dirlabel': 'Received' if line.direction == 'in' else 'Sent',
        'date': fields.Datetime.context_timestamp(line, line.date).strftime('%d %b %Y, %H:%M') if line.date else '',
        'to': line.email_to or '', 'cc': cc, 'body': line.body or Markup(''), 'att': att,
    }


def _mailbox_action(env, lines, name):
    return {
        'type': 'ir.actions.act_window',
        'name': name,
        'res_model': 'asset.helpdesk.mail.line',
        'view_mode': 'list,form',
        'views': [(env.ref('asset_helpdesk.view_helpdesk_mail_line_list').id, 'list'),
                  (env.ref('asset_helpdesk.view_helpdesk_mail_line_form').id, 'form')],
        'domain': [('id', 'in', lines.ids)],
        'context': {'create': False},
    }


class AssetHelpdeskSendMailWizard(models.TransientModel):
    _name = 'asset.helpdesk.send.mail.wizard'
    _description = 'Helpdesk Send Mail'

    mail_id = fields.Many2one('asset.helpdesk.mail', required=True)
    email_from = fields.Char(string='From', compute='_compute_email_from')
    subject = fields.Char(compute='_compute_email_from')
    partner_to_ids = fields.Many2many('res.partner', 'helpdesk_send_mail_to_rel', 'wizard_id', 'partner_id',
                                      string='To')
    partner_cc_ids = fields.Many2many('res.partner', 'helpdesk_send_mail_cc_rel', 'wizard_id', 'partner_id',
                                      string='Cc')
    body = fields.Html(string='Message', sanitize_style=True)
    attachment_ids = fields.Many2many('ir.attachment', string='Attachments')
    include_tail = fields.Boolean(string='Include previous mails', default=True,
                                  help="Quote the earlier mails of this ticket below your message.")

    @api.depends('mail_id')
    def _compute_email_from(self):
        for rec in self:
            rec.email_from = rec.mail_id._helpdesk_from_address()
            rec.subject = rec.mail_id._reply_subject() if rec.mail_id else ''

    def _tail_html(self):
        lines = self.mail_id.line_ids.sorted(lambda l: (l.date, l.id), reverse=True)[:10]
        return Markup('').join(
            Markup('<div style="border-left:3px solid #d0d5dd;margin:14px 0 0;padding-left:12px;color:#555">'
                   '<p style="margin:0 0 6px"><b>From:</b> %s<br/><b>Sent:</b> %s<br/><b>To:</b> %s%s'
                   '<br/><b>Subject:</b> %s</p>%s</div>') % (
                l.email_from or '', l.date, l.email_to or '',
                Markup('<br/><b>Cc:</b> %s') % l.email_cc if l.email_cc else Markup(''),
                l.subject or '', l.body or Markup('')) for l in lines)

    def action_send(self):
        self.ensure_one()
        to = [p.email_formatted for p in self.partner_to_ids if p.email]
        cc = [p.email_formatted for p in self.partner_cc_ids if p.email]
        if not to:
            raise UserError(_("Add at least one recipient (with an email address) in To."))
        if not tools.html2plaintext(self.body or '').strip():
            raise UserError(_("Write a message."))
        for att in self.attachment_ids:
            att.sudo().write({'res_model': 'asset.helpdesk.mail', 'res_id': self.mail_id.id})
        email_body = self.body or Markup('')
        if self.include_tail and self.mail_id.line_ids:
            email_body = Markup('%s<br/>%s') % (email_body, self._tail_html())
        # The conversation keeps just this message; the email carries the
        # tail. mail.mail is created ourselves so it is always queued,
        # whoever the recipients are.
        message = self.mail_id.with_context(
            mail_post_autofollow=False,
            helpdesk_line_vals={'email_to': ', '.join(to), 'email_cc': ', '.join(cc) or False,
                                'email_from': self.email_from},
        ).message_post(
            body=self.body, subject=self.subject, partner_ids=[],
            attachment_ids=self.attachment_ids.ids, email_from=self.email_from,
            message_type='comment', subtype_xmlid='mail.mt_note')
        mail = self.env['mail.mail'].sudo().create({
            'subject': self.subject,
            'body_html': email_body,
            'email_from': self.email_from,
            'email_to': ', '.join(to),
            'email_cc': ', '.join(cc),
            'model': 'asset.helpdesk.mail',
            'res_id': self.mail_id.id,
            'mail_message_id': message.id,
            'attachment_ids': [(6, 0, self.attachment_ids.ids)],
            'auto_delete': False,
        })
        try:
            mail.send(raise_exception=False)
        except Exception:  # no SMTP server yet: it stays in the queue
            pass
        return {'type': 'ir.actions.act_window_close'}


class AssetHelpdeskTicketLines(models.Model):
    _inherit = 'asset.helpdesk'

    mail_line_ids = fields.One2many(related='mail_id.line_ids', string='Mails')
    mail_thread_html = fields.Html(related='mail_id.thread_html', string='Conversation')

    def action_view_mail_lines(self):
        self.ensure_one()
        return self.mail_id.action_view_lines()


class RepairManagementLines(models.Model):
    _inherit = 'repair.management'

    @api.depends('helpdesk_ticket_ids.mail_id')
    def _compute_helpdesk_mail(self):
        for rec in self:
            mail = rec.helpdesk_ticket_ids[:1].mail_id
            rec.helpdesk_mail_id = mail
            rec.mail_count = len(mail.line_ids)

    def action_view_mails(self):
        self.ensure_one()
        if not self.helpdesk_mail_id:
            raise UserError(_("This task has no ticket mail."))
        return self.helpdesk_mail_id.action_view_lines()
