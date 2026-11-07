"""One place that turns an asset event into a Smart Notification.

Every workflow calls ``self._sn(...)`` with who should hear about it and a few
label / value rows; this builds a tidy card body, drops the person who did it
from the recipients and sends it with sudo (approvers often lack write access).
"""
from markupsafe import Markup

from odoo import _, models
from odoo.tools.misc import format_date

MANAGER = 'asset_management.group_asset_manager'
IT = 'asset_management.group_asset_user_it'
HR = 'asset_management.group_asset_user_hr'


class AssetSmartNotify(models.AbstractModel):
    _name = 'asset.sn.mixin'
    _description = 'Asset notification helper'

    # ---------------------------------------------------------------- people
    def _sn_users(self, *who):
        """Users from res.users, hr.employee records or group xml ids, in one recordset."""
        users = self.env['res.users']
        for w in who:
            if not w:
                continue
            if isinstance(w, str):
                g = self.env.ref(w, raise_if_not_found=False)
                users |= g.sudo().all_user_ids if g else users
            elif w._name == 'res.users':
                users |= w
            elif w._name == 'hr.employee':
                users |= w.sudo().user_id
        return users

    def _sn_manager_of(self, employee):
        employee = employee.sudo() if employee else employee
        return employee.parent_id.user_id if employee and employee.parent_id else self.env['res.users']

    # ---------------------------------------------------------------- body
    def _sn_body(self, rows, note=None):
        cells = Markup('').join(
            Markup('<tr><td>%s</td><td><b>%s</b></td></tr>') % (k, v) for k, v in rows if v not in (None, False, ''))
        body = Markup('<table>%s</table>') % cells if cells else Markup('')
        if note:
            body += Markup('<p><i>&ldquo;%s&rdquo;</i></p>') % note
        return body

    def _sn_date(self, d):
        return format_date(self.env, d) if d else ''

    # ---------------------------------------------------------------- send
    def _sn(self, users, title, rows=(), *, note=None, kind='info', policy='info', action=None, label=None,
            close=None, done=None, reject=None, priority='normal', icon=None, tell_sender=False, keep_actor=False,
            html=None):
        """Notify ``users`` about this record (one notification per record)."""
        Notif = self.env['smart.notification'].sudo()
        for rec in self:
            to = users if not callable(users) else users(rec)
            if not keep_actor:
                to = to - self.env.user
            if not to:
                continue
            try:
                Notif.notify(rec, to, title if not callable(title) else title(rec),
                             body=(Markup(html) if html else Markup('')) + rec._sn_body(
                                 rows(rec) if callable(rows) else rows, note(rec) if callable(note) else note),
                             kind=kind, policy=policy, action_method=action, action_label=label,
                             close_methods=close, done_methods=done, reject_method=reject,
                             priority=priority, icon=icon, notify_sender=tell_sender,
                             sender=self.env.user)
            except Exception:  # noqa: BLE001 - a notice must never block the workflow
                import logging
                logging.getLogger(__name__).exception("smart notification failed for %s", rec)

    def _sn_close(self, reason):
        for rec in self:
            self.env['smart.notification'].sudo().close_for(rec, reason, self.env.user)
