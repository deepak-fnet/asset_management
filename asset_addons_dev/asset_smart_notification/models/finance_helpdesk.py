"""Purchase approval, bill approval and helpdesk through Smart Notifications.

These modules already decide who to tell (``_approval_request_tier``,
``_approval_notify``, ``_bill_notify``, ``_ticket_notify``); each of those is
mirrored here, so the bell carries the same message as the chatter/email,
with Approve / Reject buttons where the step is an approval.
"""
from markupsafe import Markup

from odoo import _, models
from odoo.tools.misc import formatLang

from .notify_mixin import MANAGER


# ====================================================================== Purchase order approval
class PurchaseOrder(models.Model):
    _name = 'purchase.order'
    _inherit = ['purchase.order', 'asset.sn.mixin']

    def _sn_rows(self):
        return [(_('Vendor'), self.partner_id.display_name),
                (_('Amount'), formatLang(self.env, self.amount_total, currency_obj=self.currency_id)),
                (_('Buyer'), self.user_id.name), (_('Submitted by'), self.approval_submitted_by.name)]

    def _approval_request_tier(self, lines):
        res = super(PurchaseOrder, self.with_context(sn_skip_mirror=True))._approval_request_tier(lines)
        level = lines[:1].sequence
        self._sn(lines.approver_id, _('Approve purchase %s') % self.name, self._sn_rows(),
                 kind='approval', policy='all', priority='high', tell_sender=True, icon='fa-shopping-cart',
                 action='action_approve_order', label=_('Approve'), reject='action_sn_reject',
                 close='action_reset_approval,button_cancel')
        return res

    def _approval_notify(self, users, subject, body):
        res = super()._approval_notify(users, subject, body)
        if not self.env.context.get('sn_skip_mirror'):
            done = subject.lower().startswith('approved')
            self._sn(users, subject, self._sn_rows(), html=body, kind='success' if done else 'info',
                     icon='fa-shopping-cart')
        return res

    def action_sn_reject(self, reason):
        """Reject from the notification panel, with the reason typed there."""
        self.ensure_one()
        return self._reject_with_reason(reason)


class PurchaseApprovalLine(models.Model):
    _inherit = 'purchase.approval.line'

    def action_reject(self):
        res = super().action_reject()
        for line in self.filtered(lambda l: l.status == 'rejected'):
            order = line.order_id
            order._sn_close(_('Rejected by %s') % line.approver_id.name)
            order._sn(order._approval_requesters(), _('Purchase %s rejected') % order.name, order._sn_rows(),
                      note=line.comment, kind='danger', priority='high', icon='fa-shopping-cart')
        return res


# ====================================================================== Bill approval
BILL_STEP = {  # stage -> (method that completes it, button label)
    'to_dept': ('action_dept_approve', 'Approve'),
    'to_stores': ('action_stores_approve', 'Validate'),
    'to_audit': ('action_audit_verify', 'Verify'),
    'to_finance': ('action_post', 'Post'),
}


class AccountMove(models.Model):
    _name = 'account.move'
    _inherit = ['account.move', 'asset.sn.mixin']

    def _sn_rows(self):
        stage = dict(self._fields['bill_approval_state']._description_selection(self.env)).get(self.bill_approval_state)
        return [(_('Vendor'), self.partner_id.display_name), (_('Bill ref'), self.ref),
                (_('Amount'), formatLang(self.env, self.amount_total, currency_obj=self.currency_id)),
                (_('Stage'), stage), (_('Submitted by'), self.bill_submitted_by.name)]

    def _bill_notify(self, users, subject, body):
        res = super()._bill_notify(users, subject, body)
        step = BILL_STEP.get(self.bill_approval_state) if self.state == 'draft' else None
        if step and 'reject' not in (subject or '').lower():
            self._sn(users, subject, self._sn_rows(), html=body, kind='approval', policy='any', priority='high',
                     tell_sender=True, icon='fa-file-text-o', action=step[0], label=_(step[1]),
                     reject='action_sn_bill_reject', close='action_bill_reject,button_draft,button_cancel')
        else:
            bad = 'reject' in (subject or '').lower()
            self._sn(users, subject, self._sn_rows(), html=body, kind='danger' if bad else 'success',
                     priority='high' if bad else 'normal', icon='fa-file-text-o')
        return res

    def _bill_reject(self, reason):
        self._sn_close(_('Rejected by %s') % self.env.user.name)
        return super()._bill_reject(reason)

    def action_sn_bill_reject(self, reason):
        self.ensure_one()
        return self._bill_reject(reason)


# ====================================================================== Helpdesk
class AssetHelpdesk(models.Model):
    _name = 'asset.helpdesk'
    _inherit = ['asset.helpdesk', 'asset.sn.mixin']

    def _sn_rows(self):
        prio = dict(self._fields['priority']._description_selection(self.env)).get(self.priority) \
            if 'priority' in self._fields else ''
        return [(_('Ticket'), self.ticket_number), (_('Raised by'), self.customer_id.name),
                (_('Asset'), self.asset_id.display_name), (_('Priority'), prio),
                (_('Team'), self.team_id.display_name)]

    def _notify_new_ticket(self):
        return super(AssetHelpdesk, self.with_context(sn_new_ticket=True))._notify_new_ticket()

    def _ticket_notify(self, users, subject, body):
        res = super()._ticket_notify(users, subject, body)
        if self.env.context.get('sn_new_ticket'):
            urgent = self.priority in ('high', 'critical') if 'priority' in self._fields else False
            self._sn(users, subject, self._sn_rows(), html=body, kind='alert' if urgent else 'reminder',
                     policy='any', priority='high' if urgent else 'normal', icon='fa-life-ring',
                     action='action_start_progress', label=_('Take it'),
                     done='action_start_progress,action_create_repair_management,action_mark_solved',
                     close='action_cancel', tell_sender=False)
        else:
            solved = 'solved' in (subject or '').lower()
            self._sn(users, subject, self._sn_rows(), html=body, kind='success' if solved else 'info',
                     icon='fa-life-ring')
        return res

    def write(self, vals):
        res = super().write(vals)
        if vals.get('state') == 'replace_pending':
            self._sn(lambda t: t.customer_id, lambda t: _('Your asset cannot be repaired - choose what to do'),
                     lambda t: t._sn_rows(), kind='warning', priority='high', icon='fa-question-circle',
                     keep_actor=True, policy='any', action='action_keep_standby', label=_('Keep standby'),
                     done='action_keep_standby,action_replace_yes,action_replace_no')
        if vals.get('state') in ('cancelled',):
            self._sn_close(_('Ticket cancelled'))
        return res


class RepairAssignWizard(models.TransientModel):
    _inherit = 'repair.assign.wizard'

    def action_assign(self):
        res = super().action_assign()
        repair = self.repair_id
        repair._sn(self.user_id, _('Task assigned to you: %s') % repair.name, repair._sn_rows(),
                   kind='reminder', priority='high', icon='fa-wrench', policy='any',
                   action='action_done', label=_('Mark repaired'), close='action_not_repairable')
        return res
