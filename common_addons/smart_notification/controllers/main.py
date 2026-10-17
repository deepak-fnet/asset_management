from odoo import fields, http, _
from odoo.addons.web.controllers.dataset import DataSet
from odoo.exceptions import AccessError, UserError
from odoo.http import request


class SmartNotificationButtons(DataSet):
    """Any form button pressed on a record also completes that record's notifications."""

    @http.route()
    def call_button(self, model, method, args, kwargs, path=None):
        res = super().call_button(model, method, args, kwargs, path=path)
        try:
            ids = args[0] if args and isinstance(args[0], list) else ([args[0]] if args and isinstance(args[0], int) else [])
            if ids and not method.startswith('_'):
                request.env['smart.notification'].sudo()._on_action(model, ids, method, request.env.user)
        except Exception:  # noqa: BLE001 - the button worked; the notification can catch up later
            pass
        return res


class SmartNotificationApi(http.Controller):

    def _lines(self):
        return request.env['smart.notification.line'].sudo()

    def _mine(self, line_id):
        line = self._lines().browse(int(line_id)).exists()
        if not line or line.user_id != request.env.user:
            raise AccessError(_("This notification is not yours."))
        return line

    @http.route('/smart_notification/list', type='jsonrpc', auth='user')
    def list(self, tab='todo', limit=40, offset=0):
        user = request.env.user
        domain = [('user_id', '=', user.id)]
        if tab == 'todo':
            domain += [('state', 'in', ('new', 'seen')), ('policy', '!=', 'info')]
        elif tab == 'unread':
            domain += [('state', '=', 'new')]
        elif tab == 'done':
            domain += [('state', 'in', ('done', 'closed'))]
        lines = self._lines().search(domain, limit=limit, offset=offset)
        return {'items': [l._payload() for l in lines], 'counts': self._lines()._counts(user),
                'more': self._lines().search_count(domain) > offset + limit}

    @http.route('/smart_notification/counts', type='jsonrpc', auth='user')
    def counts(self):
        return self._lines()._counts(request.env.user)

    @http.route('/smart_notification/seen', type='jsonrpc', auth='user')
    def seen(self, line_ids):
        lines = self._lines().browse([int(i) for i in line_ids]).exists().filtered(
            lambda l: l.user_id == request.env.user and l.state == 'new')
        now = fields.Datetime.now()
        lines.write({'state': 'seen', 'seen_date': now})
        # an information notice is complete once it has been read
        for l in lines.filtered(lambda l: l.policy == 'info'):
            l.write({'state': 'done', 'done_date': now})
        return self._lines()._counts(request.env.user)

    @http.route('/smart_notification/read_all', type='jsonrpc', auth='user')
    def read_all(self):
        lines = self._lines().search([('user_id', '=', request.env.user.id), ('state', '=', 'new')])
        now = fields.Datetime.now()
        lines.filtered(lambda l: l.policy == 'info').write({'state': 'done', 'seen_date': now, 'done_date': now})
        lines.filtered(lambda l: l.policy != 'info').write({'state': 'seen', 'seen_date': now})
        return self._lines()._counts(request.env.user)

    @http.route('/smart_notification/open', type='jsonrpc', auth='user')
    def open(self, line_id):
        """Track the click and tell the web client where to go."""
        line = self._mine(line_id)
        now = fields.Datetime.now()
        vals = {'opened_date': line.opened_date or now, 'seen_date': line.seen_date or now}
        if line.state == 'new':
            vals['state'] = 'done' if line.policy == 'info' else 'seen'
            if line.policy == 'info':
                vals['done_date'] = now
        line.write(vals)
        n = line.notification_id
        if n.url:
            return {'url': n.url}
        if not n.res_model or not n.res_id:
            return {}
        if n.action_id:
            # through the menu action: its own form view, and its list in the breadcrumb
            return {'action_id': n.action_id.id, 'res_id': n.res_id, 'view_id': n.view_id.id or False}
        return {'action': {
            'type': 'ir.actions.act_window', 'res_model': n.res_model, 'res_id': n.res_id,
            'views': [[n.view_id.id or False, 'form']], 'target': 'current',
        }}

    @http.route('/smart_notification/resolve', type='jsonrpc', auth='user')
    def resolve(self, line_id, outcome, reason):
        """Mark done or reject from the notification. A reason is required for both."""
        line = self._mine(line_id)
        n = line.notification_id
        reason = (reason or '').strip()
        if len(reason) < 3:
            raise UserError(_("Write a short reason."))
        if outcome not in ('done', 'rejected'):
            raise UserError(_("Unknown outcome."))
        if line.state not in ('new', 'seen') or n.state != 'open':
            raise UserError(n.close_reason or _("This was already handled."))
        res = None
        if outcome == 'rejected' and n.reject_method and not n.reject_method.startswith('_') and n.res_model:
            record = request.env[n.res_model].browse(n.res_id).exists()
            if record:
                # the real refusal on the record, with the same reason
                res = getattr(record, n.reject_method)(reason)
        line.invalidate_recordset()
        if line.state in ('new', 'seen') and n.state == 'open':
            n._resolve_by_hand(request.env.user, outcome, reason)
        elif outcome == 'rejected':
            line.write({'outcome': 'rejected', 'note': reason})
        return {'action': res if isinstance(res, dict) else False, 'item': line._payload(),
                'counts': self._lines()._counts(request.env.user)}

    @http.route('/smart_notification/act', type='jsonrpc', auth='user')
    def act(self, line_id):
        """Press the notification's button: run the method on the record as this user."""
        line = self._mine(line_id)
        n = line.notification_id
        if not n.action_method or n.action_method.startswith('_'):
            raise UserError(_("This notification has no action."))
        if line.state not in ('new', 'seen') or n.state != 'open':
            raise UserError(n.close_reason or _("This was already handled."))
        record = request.env[n.res_model].browse(n.res_id).exists()
        if not record:
            n._close(_("The record no longer exists"))
            raise UserError(_("The record no longer exists."))
        res = getattr(record, n.action_method)()
        request.env['smart.notification'].sudo()._on_action(n.res_model, [n.res_id], n.action_method, request.env.user)
        return {'action': res if isinstance(res, dict) else False, 'item': line._payload(),
                'counts': self._lines()._counts(request.env.user)}
