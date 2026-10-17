"""In-app notifications for any record, with tracking and self-closing approvals.

Usage from any module::

    self.env['smart.notification'].notify(
        record, users, "Approve PO/2026/0042",
        body="<p>Vendor <b>Sakthi Traders</b>, total <b>₹ 1,20,000</b></p>",
        action_method='action_approve', action_label="Approve",
        policy='any',               # 'info' (just read) | 'any' (one of them acts) | 'all' (each must act)
        close_methods='action_refuse,action_cancel',
        kind='approval', priority='high')

When a recipient presses the button named in ``action_method`` (or ``done_methods``) on that record,
from the form or from the notification panel, their line is done. With ``any`` the others close
("Approved by …"); with ``all`` each person's line stays pending until they act themselves.
A method in ``close_methods`` closes it for everyone. Code that acts outside a form button
calls ``_on_action(model, ids, method)`` itself.
"""
import logging

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)

KIND_STYLE = {  # kind: (font-awesome icon, colour)
    'approval': ('fa-gavel', '#d08a12'),
    'info': ('fa-info', '#3b6fd8'),
    'success': ('fa-check', '#2f7d4f'),
    'warning': ('fa-exclamation-triangle', '#d08a12'),
    'danger': ('fa-times', '#b4372f'),
    'access': ('fa-key', '#c2508a'),
    'alert': ('fa-shield', '#b4372f'),
    'reminder': ('fa-clock-o', '#7a52c7'),
}


def _methods(text):
    return [m.strip() for m in (text or '').split(',') if m.strip()]


class SmartNotification(models.Model):
    _name = 'smart.notification'
    _description = 'Notification'
    _order = 'id desc'

    name = fields.Char("Title", required=True)
    body = fields.Html(sanitize=True)
    kind = fields.Selection([(k, k.title()) for k in KIND_STYLE], default='info', required=True)
    icon = fields.Char(help="Font Awesome class, e.g. fa-gavel. Defaults to the kind's icon.")
    color = fields.Char(help="Hex colour of the icon. Defaults to the kind's colour.")
    priority = fields.Selection([('normal', 'Normal'), ('high', 'High')], default='normal')
    sender_id = fields.Many2one('res.users', "From", default=lambda s: s.env.user, ondelete='set null')

    res_model = fields.Char("Model", index=True)
    res_id = fields.Many2oneReference("Record ID", model_field='res_model', index=True)
    res_name = fields.Char("Record")
    view_id = fields.Many2one('ir.ui.view', "Form view", ondelete='set null')
    action_id = fields.Many2one('ir.actions.act_window', "Window action", ondelete='set null',
                                help="Open the record through this menu action: its form view, with its list in the breadcrumb.")
    url = fields.Char("Web URL", help="Where Open goes. Empty: the record's form view.")

    policy = fields.Selection([('info', 'Information only'), ('any', 'Any one of them acts'),
                               ('all', 'Each of them must act')], default='info', required=True)
    action_method = fields.Char("Button method", help="Called on the record from the notification, e.g. action_approve.")
    action_label = fields.Char("Button label")
    done_methods = fields.Char(help="Comma separated methods that count as done. Defaults to the button method.")
    close_methods = fields.Char(help="Comma separated methods that close the notification for everyone.")
    notify_sender = fields.Boolean(
        "Tell the sender", help="When someone approves, rejects or marks it done, the sender gets an "
                                "information notification by itself.")
    reject_method = fields.Char("Reject method", help="Called with the reason when someone rejects from the notification, "
                                                      "e.g. action_refuse(reason). Empty: the rejection is only recorded.")
    reject_label = fields.Char()

    state = fields.Selection([('open', 'Waiting'), ('done', 'Done'), ('closed', 'Closed')],
                             default='open', required=True, index=True)
    done_by_id = fields.Many2one('res.users', "Done by", readonly=True)
    done_date = fields.Datetime(readonly=True)
    close_reason = fields.Char(readonly=True)
    line_ids = fields.One2many('smart.notification.line', 'notification_id', "Recipients")
    line_count = fields.Integer(compute='_compute_progress')
    done_count = fields.Integer(compute='_compute_progress')

    @api.depends('line_ids.state')
    def _compute_progress(self):
        for n in self:
            n.line_count = len(n.line_ids)
            n.done_count = len(n.line_ids.filtered(lambda l: l.state == 'done'))

    # ------------------------------------------------------------------ create
    @api.model
    def notify(self, record, users, title, body=None, *, kind=None, policy='info', action_method=None,
               action_label=None, done_methods=None, close_methods=None, priority='normal', icon=None,
               color=None, view=None, url=None, sender=None, reject_method=None, reject_label=None, res_name=None,
               notify_sender=False, action=None):
        """Create one notification for ``record`` and push it live to every user."""
        users = users.filtered(lambda u: u.active and not u.share)
        if not users:
            return self.browse()
        if policy != 'info' and not (action_method or done_methods):
            raise UserError(_("An 'any' or 'all' notification needs the method that completes it."))
        n = self.sudo().create({
            'name': title, 'body': body or False, 'kind': kind or ('approval' if policy != 'info' else 'info'),
            'priority': priority, 'icon': icon, 'color': color,
            'sender_id': (sender or self.env.user).id,
            'res_model': record._name if record else False, 'res_id': record.id if record else False,
            'res_name': res_name or (record.display_name if record else False),
            'reject_method': reject_method, 'reject_label': reject_label, 'notify_sender': notify_sender,
            'view_id': view.id if view else False, 'url': url, 'action_id': action.id if action else False,
            'policy': policy, 'action_method': action_method, 'action_label': action_label,
            'done_methods': done_methods or action_method, 'close_methods': close_methods,
            'line_ids': [(0, 0, {'user_id': u.id}) for u in users],
        })
        for line in n.line_ids:
            line.user_id._bus_send('smart_notification/new', line._payload())
        return n

    # ------------------------------------------------------------------ completion
    @api.model
    def _on_action(self, model, ids, method, user=None):
        """``user`` pressed ``method`` on records ``ids`` of ``model``: mark the matching notifications."""
        user = user or self.env.user
        if not ids or not method:
            return
        # only what existed before this click: the action itself may have just sent the next step
        # to the same person (e.g. admin is both L1 and L2), and that one must stay open
        # (plain SQL: an ORM domain on a Datetime drops the microseconds, and both can fall in one second)
        self.env.flush_all()
        self.env.cr.execute("""
            SELECT id FROM smart_notification
             WHERE res_model = %s AND res_id IN %s AND state = 'open' AND create_date < %s
        """, [model, tuple(ids), self.env.cr.now()])
        found = self.sudo().browse([r[0] for r in self.env.cr.fetchall()])
        for n in found:
            if method in _methods(n.close_methods):
                n._close(_("%(m)s by %(u)s", m=n._method_label(method), u=user.name), user)
            elif method in _methods(n.done_methods):
                n._done_by(user, method)

    def _tell_sender(self, user, headline, kind='success', reason=None):
        """Let the person who sent it know what happened. Nothing for them to do: information only."""
        for n in self:
            if not n.notify_sender or not n.sender_id or n.sender_id == user:
                continue
            record = self.env[n.res_model].browse(n.res_id).exists() if n.res_model and n.res_id else None
            done = len(n.line_ids.filtered(lambda l: l.state == 'done'))
            rows = [(_("By"), user.name), (_("When"), fields.Datetime.to_string(fields.Datetime.now())[:16])]
            if n.policy == 'all':
                rows.append((_("Progress"), _("%(a)s of %(b)s done", a=done, b=len(n.line_ids))))
            if n.state != 'open' and n.close_reason and n.close_reason != headline:
                rows.append((_("Result"), n.close_reason))
            body = Markup('<p><b>%s</b></p><table>%s</table>') % (headline, Markup('').join(
                Markup('<tr><td style="color:#7a8293;padding-right:12px">%s</td><td><b>%s</b></td></tr>') % (k, v) for k, v in rows))
            if reason:
                body += Markup('<p style="font-style:italic">&ldquo;%s&rdquo;</p>') % reason
            self.notify(record, n.sender_id, '%s: %s' % (headline, n.res_name or n.name), body=body, kind=kind,
                        sender=user, url=n.url or None, view=n.view_id or None, res_name=n.res_name or None,
                        action=n.action_id or None,
                        priority='high' if kind == 'danger' else 'normal')

    def _method_label(self, method):
        """'action_approve' -> 'Approved', so the others read "Approved by Dheepa"."""
        self.ensure_one()
        if self.action_label and (method == self.action_method or not self.action_method):
            label = self.action_label
        else:
            label = method.replace('action_', '').strip('_').replace('_', ' ').capitalize()
        if label.endswith('ed'):
            return label
        return label + ('d' if label.endswith('e') else 'ed')

    def _done_by(self, user, method=None):
        now = fields.Datetime.now()
        for n in self:
            mine = n.line_ids.filtered(lambda l: l.user_id == user and l.state in ('new', 'seen'))
            if not mine:
                continue
            mine.write({'state': 'done', 'done_date': now, 'seen_date': mine.seen_date or now})
            label = n._method_label(method or n.action_method or '')
            if n.policy == 'any':
                others = n.line_ids.filtered(lambda l: l.state in ('new', 'seen'))
                others.write({'state': 'closed', 'closed_date': now,
                              'closed_reason': _("%(m)s by %(u)s", m=label, u=user.name)})
                n.write({'state': 'done', 'done_by_id': user.id, 'done_date': now,
                         'close_reason': _("%(m)s by %(u)s", m=label, u=user.name)})
            elif n.policy == 'all' and not n.line_ids.filtered(lambda l: l.state in ('new', 'seen')):
                n.write({'state': 'done', 'done_by_id': user.id, 'done_date': now,
                         'close_reason': _("Everyone acted")})
            elif n.policy == 'info':
                n.write({'state': 'done', 'done_by_id': user.id, 'done_date': now})
            n._push_update()
            if n.policy != 'info':
                headline = _("%(m)s by %(u)s", m=label, u=user.name)
                if n.policy == 'all':
                    left = len(n.line_ids.filtered(lambda l: l.state in ('new', 'seen')))
                    headline = _("Everyone has %(m)s", m=label.lower()) if not left else \
                        _("%(m)s by %(u)s, waiting for %(n)s more", m=label, u=user.name, n=left)
                n._tell_sender(user, headline, 'success')

    def _resolve_by_hand(self, user, outcome, reason):
        """Someone marks it done, or rejects it, from the notification, with a reason."""
        self.ensure_one()
        now = fields.Datetime.now()
        mine = self.line_ids.filtered(lambda l: l.user_id == user and l.state in ('new', 'seen'))
        if not mine:
            raise UserError(_("This is not waiting for you."))
        mine.write({'state': 'done', 'outcome': outcome, 'note': reason, 'done_date': now,
                    'seen_date': mine[:1].seen_date or now})
        verb = _("Rejected") if outcome == 'rejected' else _("Marked done")
        text = _("%(v)s by %(u)s: %(r)s", v=verb, u=user.name, r=reason)
        others = self.line_ids.filtered(lambda l: l.state in ('new', 'seen'))
        if outcome == 'rejected' or self.policy in ('any', 'info'):
            # a rejection stops it for everyone; for "any one of them" one answer is enough
            others.write({'state': 'closed', 'closed_date': now, 'closed_reason': text})
            self.write({'state': 'closed' if outcome == 'rejected' else 'done', 'done_by_id': user.id,
                        'done_date': now, 'close_reason': text})
        elif not others:
            self.write({'state': 'done', 'done_by_id': user.id, 'done_date': now, 'close_reason': _("Everyone acted")})
        self._push_update()
        self._tell_sender(user, _("%(v)s by %(u)s", v=verb, u=user.name), 'danger' if outcome == 'rejected' else 'success', reason)

    def _close(self, reason, user=None):
        now = fields.Datetime.now()
        for n in self.filtered(lambda n: n.state == 'open'):
            n.line_ids.filtered(lambda l: l.state in ('new', 'seen')).write(
                {'state': 'closed', 'closed_date': now, 'closed_reason': reason})
            n.write({'state': 'closed', 'done_by_id': user.id if user else False, 'done_date': now, 'close_reason': reason})
            n._push_update()
            if user:
                n._tell_sender(user, reason, 'danger')

    @api.model
    def close_for(self, record, reason, user=None):
        """Close every open notification of ``record`` (cancelled, deleted, decided elsewhere)."""
        self.sudo().search([('res_model', '=', record._name), ('res_id', 'in', record.ids),
                            ('state', '=', 'open')])._close(reason, user or self.env.user)

    def _push_update(self):
        for n in self:
            for line in n.line_ids:
                line.user_id._bus_send('smart_notification/update', line._payload())


class SmartNotificationLine(models.Model):
    _name = 'smart.notification.line'
    _description = 'Notification recipient'
    _order = 'id desc'
    _rec_name = 'user_id'

    notification_id = fields.Many2one('smart.notification', required=True, ondelete='cascade', index=True)
    user_id = fields.Many2one('res.users', "Recipient", required=True, index=True, ondelete='cascade')
    state = fields.Selection([('new', 'Unread'), ('seen', 'Seen'), ('done', 'Done'), ('closed', 'Closed')],
                             default='new', required=True, index=True)
    seen_date = fields.Datetime("Seen on", readonly=True)
    opened_date = fields.Datetime("Opened on", readonly=True, help="When the record was opened from the notification.")
    done_date = fields.Datetime("Done on", readonly=True)
    closed_date = fields.Datetime(readonly=True)
    closed_reason = fields.Char(readonly=True)
    outcome = fields.Selection([('done', 'Done'), ('rejected', 'Rejected')], readonly=True)
    note = fields.Char("Reason", readonly=True)
    # handy for list views and filters
    name = fields.Char(related='notification_id.name')
    res_model = fields.Char(related='notification_id.res_model', store=True)
    res_id = fields.Many2oneReference(related='notification_id.res_id', model_field='res_model')
    policy = fields.Selection(related='notification_id.policy', store=True)
    sender_id = fields.Many2one(related='notification_id.sender_id')
    create_date = fields.Datetime(readonly=True)

    def _check_mine(self):
        for l in self:
            if l.user_id != self.env.user:
                raise AccessError(_("This notification is not yours."))

    def _payload(self):
        self.ensure_one()
        n = self.notification_id
        icon, color = KIND_STYLE.get(n.kind, KIND_STYLE['info'])
        model_name = self.env['ir.model']._get(n.res_model).name if n.res_model else ''
        dt = fields.Datetime.to_string
        return {
            'id': self.id, 'state': self.state, 'seen_date': dt(self.seen_date), 'opened_date': dt(self.opened_date),
            'done_date': dt(self.done_date), 'closed_reason': self.closed_reason or '',
            'outcome': self.outcome or '', 'note': self.note or '', 'sent_date': dt(self.create_date),
            'n': {
                'id': n.id, 'title': n.name, 'body': str(n.body or ''), 'text': html2plaintext(n.body or '').replace('*', '').strip()[:180],
                'kind': n.kind, 'icon': n.icon or icon, 'color': n.color or color, 'priority': n.priority,
                'sender': {'id': n.sender_id.id, 'name': n.sender_id.name} if n.sender_id else None,
                'date': dt(n.create_date), 'res_model': n.res_model, 'model_name': model_name,
                'res_id': n.res_id, 'res_name': n.res_name or '', 'view_id': n.view_id.id or False, 'url': n.url or '',
                'policy': n.policy, 'action_method': n.action_method or '', 'action_label': n.action_label or '',
                'reject_method': n.reject_method or '', 'reject_label': n.reject_label or '',
                'state': n.state, 'done_by': n.done_by_id.name or '', 'done_date': dt(n.done_date),
                'close_reason': n.close_reason or '',
                'people': [{'id': l.user_id.id, 'name': l.user_id.name, 'state': l.state, 'date': dt(l.done_date),
                            'seen': dt(l.seen_date), 'opened': dt(l.opened_date), 'outcome': l.outcome or '',
                            'note': l.note or '', 'closed_reason': l.closed_reason or ''} for l in n.line_ids],
            },
        }

    @api.model
    def _counts(self, user=None):
        user = user or self.env.user
        Line = self.sudo()
        return {
            'unread': Line.search_count([('user_id', '=', user.id), ('state', '=', 'new')]),
            'todo': Line.search_count([('user_id', '=', user.id), ('state', 'in', ('new', 'seen')), ('policy', '!=', 'info')]),
        }
