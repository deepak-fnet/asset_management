"""Hooks on the asset workflows: who hears what, and when.

Approval notices use policy 'any' with the approve method as the done button,
so the first approver closes it for the rest ("Approved by ..."), and the
reject / cancel methods close it for everyone. The sender is told the outcome.
"""
from odoo import _, models

from .notify_mixin import HR, IT, MANAGER

APPROVAL = dict(kind='approval', policy='any', priority='high', tell_sender=True)


def _emp(e):
    return e.display_name if e else ''


# ====================================================================== Asset request
class AssetRequest(models.Model):
    _name = 'asset.request'
    _inherit = ['asset.request', 'asset.sn.mixin']

    def _sn_rows(self):
        return [(_('Requested by'), self.requested_by.name), (_('Reference'), self.name)]

    def action_submit(self):
        res = super().action_submit()
        for r in self:
            mgr = r._requester_manager_user() if hasattr(r, '_requester_manager_user') else self.env['res.users']
            r._sn(r._sn_users(mgr, MANAGER), _('Approve asset request %s') % r.name, r._sn_rows(),
                  action='action_approve', label=_('Approve'), close='action_reject,action_cancel',
                  reject='action_reject', **APPROVAL)
        return res

    def action_approve(self):
        res = super().action_approve()
        self._sn(lambda r: r._sn_users(IT, 'purchase.group_purchase_user'),
                 lambda r: _('Asset request %s approved - arrange the assets') % r.name,
                 lambda r: r._sn_rows(), kind='success', icon='fa-shopping-cart')
        return res

    def action_store_done(self):
        res = super().action_store_done()
        self._sn(lambda r: r.requested_by, lambda r: _('Your asset request %s is ready') % r.name,
                 lambda r: r._sn_rows(), kind='success', icon='fa-gift', priority='high')
        return res


# ====================================================================== Spare request / issue
class SpareRequest(models.Model):
    _name = 'spare.request'
    _inherit = ['spare.request', 'asset.sn.mixin']

    def _sn_rows(self):
        return [(_('Employee'), _emp(self.employee_id)), (_('For asset'), self.asset_id.display_name),
                (_('Reference'), self.name)]

    def action_submit(self):
        res = super().action_submit()
        self._sn(lambda r: r._sn_users(MANAGER), lambda r: _('Approve spare request %s') % r.name,
                 lambda r: r._sn_rows(), note=lambda r: r.reason, action='action_approve', label=_('Approve'),
                 close='action_reject,action_cancel', reject='action_reject', **APPROVAL)
        return res

    def action_approve(self):
        res = super().action_approve()
        self._sn(lambda r: r._sn_users(r.create_uid, r.employee_id),
                 lambda r: _('Spare request %s approved') % r.name, lambda r: r._sn_rows(), kind='success')
        return res


class SpareIssue(models.Model):
    _name = 'spare.issue'
    _inherit = ['spare.issue', 'asset.sn.mixin']

    def action_issue(self):
        res = super().action_issue()
        self._sn(lambda r: r._sn_users(r.requested_by), lambda r: _('Spare issued: %s') % r.display_name,
                 kind='success', icon='fa-wrench')
        return res


# ====================================================================== Repair
class RepairManagement(models.Model):
    _name = 'repair.management'
    _inherit = ['repair.management', 'asset.sn.mixin']

    def _sn_rows(self):
        return [(_('Asset'), self.asset_id.display_name), (_('Engineer'), _emp(self.engineer_id)),
                (_('Reference'), self.name)]

    def _sn_holder(self):
        a = self.asset_id.sudo()
        emp = a.assigned_employee_id if 'assigned_employee_id' in a._fields else False
        return self._sn_users(emp)

    def action_start(self):
        res = super().action_start()
        for r in self:
            r._sn(r._sn_users(r.engineer_id), _('Repair assigned to you: %s') % r.asset_id.display_name,
                  r._sn_rows(), kind='reminder', icon='fa-wrench', policy='any',
                  action='action_done', label=_('Mark repaired'), close='action_not_repairable,action_reset_to_new')
            r._sn(r._sn_holder(), _('Your %s went for repair') % r.asset_id.display_name, r._sn_rows(),
                  kind='info', icon='fa-wrench')
        return res

    def action_done(self):
        res = super().action_done()
        self._sn(lambda r: r._sn_holder() | r._sn_users(r.create_uid),
                 lambda r: _('Repair done: %s') % r.asset_id.display_name, lambda r: r._sn_rows(), kind='success')
        return res

    def action_not_repairable(self):
        res = super().action_not_repairable()
        self._sn(lambda r: r._sn_users(MANAGER, r.create_uid),
                 lambda r: _('Not repairable: %s - decide scrap or replacement') % r.asset_id.display_name,
                 lambda r: r._sn_rows(), kind='danger', priority='high')
        return res


# ====================================================================== OS upgrade
class AssetOsUpgrade(models.Model):
    _name = 'asset.os.upgrade.request'
    _inherit = ['asset.os.upgrade.request', 'asset.sn.mixin']

    def _sn_rows(self):
        return [(_('Asset'), self.asset_id.display_name), (_('Reference'), self.name)]

    def action_approve(self):
        res = super().action_approve()
        self._sn(lambda r: r.requested_by, lambda r: _('OS upgrade approved: %s') % r.asset_id.display_name,
                 lambda r: r._sn_rows(), kind='success', icon='fa-level-up')
        return res

    def write(self, vals):
        res = super().write(vals)
        if vals.get('state') in ('done', 'failed'):
            ok = vals['state'] == 'done'
            self._sn(lambda r: r._sn_users(r.requested_by, MANAGER),
                     lambda r: (_('OS upgrade finished: %s') if ok else _('OS upgrade failed: %s')) % r.asset_id.display_name,
                     lambda r: r._sn_rows(), kind='success' if ok else 'danger', keep_actor=True,
                     priority='normal' if ok else 'high')
        return res


# ====================================================================== Assign (joining) process
class AssetJoiningProcess(models.Model):
    _name = 'asset.joining.process'
    _inherit = ['asset.joining.process', 'asset.sn.mixin']

    def _sn_rows(self):
        lines = self['request_line_ids'] if 'request_line_ids' in self._fields else []
        items = ', '.join('%s x%d' % (l.description or l.product_id.display_name or '', l.quantity) for l in lines)
        return [(_('Employee'), _emp(self.employee_id)), (_('Department'), self.department_id.display_name),
                (_('Items'), items), (_('Reference'), self.name)]

    def _sn_approvers(self):
        """The employee's manager, plus whoever the button-access rule lets press Approve."""
        mgr = self._sn_manager_of(self.employee_id)
        rule = self.env['asset.button.access'].sudo().search(
            [('res_model', '=', self._name), ('button_key', '=', 'action_approve')], limit=1)
        if rule and rule.visibility_type == 'user':
            others = rule.user_ids
        elif rule and rule.visibility_type == 'group' and rule.group_id:
            others = rule.group_id.all_user_ids
        elif rule and rule.visibility_type == 'all':
            others = self._sn_users(MANAGER)
        else:
            others = self._sn_users(MANAGER)
        return mgr | others

    def action_confirm(self):
        res = super().action_confirm()
        for r in self:
            r._sn(r._sn_approvers(), _('Approve assets for %s') % _emp(r.employee_id), r._sn_rows(),
                  note=r.description, action='action_approve', label=_('Approve'), close='action_cancel', **APPROVAL)
        return res

    def action_approve(self):
        res = super().action_approve()
        self._sn(lambda r: r._sn_users(IT), lambda r: _('Assign assets to %s') % _emp(r.employee_id),
                 lambda r: r._sn_rows(), kind='reminder', icon='fa-laptop', policy='any',
                 action='action_assign_assets', label=_('Assign'), close='action_cancel', priority='high')
        return res

    def action_assign_assets(self):
        before = {r.id: r.state for r in self}
        res = super().action_assign_assets()
        for r in self.filtered(lambda r: r.state in ('partial', 'done') and before.get(r.id) != r.state):
            assets = r.allocation_ids.mapped('asset_ids') if 'allocation_ids' in r._fields else []
            rows = [(_('Assets'), ', '.join(a.asset_code or a.display_name for a in assets))] + r._sn_rows()[1:]
            full = r.state == 'done'
            r._sn(r._sn_users(r.employee_id), _('Your assets are ready') if full else _('Some of your assets are ready'),
                  rows, kind='success', icon='fa-gift', priority='high')
            r._sn(r._sn_users(r.requested_by_id, r._sn_manager_of(r.employee_id)),
                  (_('All assets handed over to %s') if full else _('Assets partly handed over to %s')) % _emp(r.employee_id),
                  rows, kind='success' if full else 'warning')
        return res

    def action_cancel(self):
        res = super().action_cancel()
        self._sn(lambda r: r._sn_users(r.requested_by_id), lambda r: _('Assign request %s cancelled') % r.name,
                 lambda r: r._sn_rows(), kind='danger')
        return res


# ====================================================================== Exit process
class AssetExitProcess(models.Model):
    _name = 'asset.exit.process'
    _inherit = ['asset.exit.process', 'asset.sn.mixin']

    def _sn_rows(self):
        return [(_('Employee'), _emp(self.employee_id)), (_('Exit date'), self._sn_date(self.exit_date)),
                (_('Reference'), self.name)]

    def action_start(self):
        res = super().action_start()
        self._sn(lambda r: r._sn_users(IT), lambda r: _('Collect assets from %s') % _emp(r.employee_id),
                 lambda r: r._sn_rows(), kind='reminder', icon='fa-sign-out', policy='any',
                 action='action_complete', label=_('Complete'), close='action_cancel', priority='high')
        self._sn(lambda r: r._sn_users(r.employee_id), lambda r: _('Please return your assets before %s') % (
            r._sn_date(r.exit_date) or _('your last day')), lambda r: r._sn_rows(), kind='warning', icon='fa-undo')
        return res

    def action_complete(self):
        res = super().action_complete()
        self._sn(lambda r: r._sn_users(HR, r._sn_manager_of(r.employee_id)),
                 lambda r: _('Asset clearance done for %s') % _emp(r.employee_id),
                 lambda r: r._sn_rows(), kind='success', icon='fa-check-circle')
        return res


# ====================================================================== Transfer / Scrap
class AssetSnTwoStep(models.AbstractModel):
    """Submit -> Approve -> Done flow shared by transfers and scraps."""
    _name = 'asset.sn.twostep'
    _inherit = 'asset.sn.mixin'
    _description = 'Asset two-step notifications'
    _sn_what = ''

    def _sn_rows(self):
        return []

    def _sn_ts_submit(self):
        self._sn(lambda r: r._sn_users(MANAGER), lambda r: _('Approve %s %s') % (r._sn_what, r.name),
                 lambda r: r._sn_rows(), action='action_approve', label=_('Approve'), close='action_cancel', **APPROVAL)

    def _sn_ts_approve(self):
        self._sn(lambda r: r._sn_users(IT), lambda r: _('Carry out %s %s') % (r._sn_what, r.name),
                 lambda r: r._sn_rows(), kind='reminder', policy='any', action='action_done', label=_('Done'),
                 close='action_cancel')


class AssetTransfer(models.Model):
    _name = 'asset.transfer'
    _inherit = ['asset.transfer', 'asset.sn.twostep']
    _sn_what = 'transfer'

    def action_submit(self):
        res = super().action_submit()
        self._sn_ts_submit()
        return res

    def action_approve(self):
        res = super().action_approve()
        self._sn_ts_approve()
        return res

    def _sn_rows(self):
        frm = _emp(self.from_employee_id) or self.from_location_id.display_name or self.from_department_id.display_name
        to = _emp(self.to_employee_id) or self.to_location_id.display_name or self.to_department_id.display_name
        return [(_('Asset'), self.asset_id.display_name), (_('From'), frm), (_('To'), to),
                (_('Requested by'), self.requested_by.name)]

    def action_done(self):
        res = super().action_done()
        self._sn(lambda r: r._sn_users(r.to_employee_id), lambda r: _('%s is now with you') % r.asset_id.display_name,
                 lambda r: r._sn_rows(), kind='success', icon='fa-exchange')
        self._sn(lambda r: r._sn_users(r.from_employee_id), lambda r: _('%s moved out of your name') % r.asset_id.display_name,
                 lambda r: r._sn_rows(), kind='info', icon='fa-exchange')
        return res


class AssetScrap(models.Model):
    _name = 'asset.scrap'
    _inherit = ['asset.scrap', 'asset.sn.twostep']
    _sn_what = 'scrap'

    def action_submit(self):
        res = super().action_submit()
        self._sn_ts_submit()
        return res

    def action_approve(self):
        res = super().action_approve()
        self._sn_ts_approve()
        return res

    def _sn_rows(self):
        reason = dict(self._fields['scrap_reason']._description_selection(self.env)).get(self.scrap_reason, '')
        return [(_('Asset'), self.asset_id.display_name), (_('Reason'), reason),
                (_('Requested by'), self.requested_by.name)]

    def action_done(self):
        res = super().action_done()
        self._sn(lambda r: r._sn_users(MANAGER, r.requested_by), lambda r: _('Scrapped: %s') % r.asset_id.display_name,
                 lambda r: r._sn_rows(), kind='danger', icon='fa-trash')
        return res


# ====================================================================== Physical verification
class PhysicalVerification(models.Model):
    _name = 'physical.verification'
    _inherit = ['physical.verification', 'asset.sn.mixin']

    def action_done(self):
        res = super().action_done()
        for r in self:
            lines = r.line_ids if 'line_ids' in r._fields else []
            missing = len([l for l in lines if l.status == 'missing']) if lines and 'status' in lines._fields else 0
            extra = len([l for l in lines if getattr(l, 'is_extra', False)])
            rows = [(_('Location'), r.location_id.display_name), (_('Department'), r.department_id.display_name),
                    (_('Checked'), len(lines)), (_('Missing'), missing), (_('Found elsewhere'), extra),
                    (_('Transfers raised'), getattr(r, 'transfer_count', 0))]
            r._sn(r._sn_users(MANAGER), (_('Verification %s: %d missing') % (r.name, missing)) if missing
                  else _('Verification %s completed - all found') % r.name, rows,
                  kind='alert' if missing else 'success', priority='high' if missing else 'normal')
        return res


# ====================================================================== Warranty claim
class WarrantyClaim(models.Model):
    _name = 'asset.warranty.claim'
    _inherit = ['asset.warranty.claim', 'asset.sn.mixin']

    def _sn_rows(self):
        return [(_('Asset'), self.asset_id.display_name), (_('Provider'), self.provider_id.display_name),
                (_('Ticket'), self.provider_ticket_no), (_('Reference'), self.name)]

    def action_submit(self):
        res = super().action_submit()
        self._sn(lambda r: r._sn_users(MANAGER), lambda r: _('Warranty claim raised: %s') % r.asset_id.display_name,
                 lambda r: r._sn_rows(), kind='info', icon='fa-shield')
        return res

    def action_approve(self):
        res = super().action_approve()
        self._sn(lambda r: r._sn_users(r.create_uid, r.employee_id), lambda r: _('Warranty claim approved: %s') % r.asset_id.display_name,
                 lambda r: r._sn_rows(), kind='success', icon='fa-shield')
        return res

    def action_reject(self):
        res = super().action_reject()
        self._sn(lambda r: r._sn_users(r.create_uid, r.employee_id, MANAGER),
                 lambda r: _('Warranty claim rejected: %s') % r.asset_id.display_name,
                 lambda r: r._sn_rows(), kind='danger', icon='fa-shield')
        return res

    def action_complete(self):
        res = super().action_complete()
        self._sn(lambda r: r._sn_users(r.create_uid, r.employee_id), lambda r: _('Warranty claim completed: %s') % r.asset_id.display_name,
                 lambda r: r._sn_rows(), kind='success', icon='fa-shield')
        return res


# ====================================================================== Direct assign
class DirectAssignWizard(models.TransientModel):
    _inherit = 'asset.direct.assign.wizard'

    def action_assign(self):
        res = super().action_assign()
        for w in self:
            emp = w.employee_id if 'employee_id' in w._fields else False
            asset = w.asset_id if 'asset_id' in w._fields else False
            if emp and asset and emp.user_id:
                asset._sn(
                    emp.user_id, _('%s is assigned to you') % asset.display_name,
                    [(_('Asset'), asset.display_name), (_('Code'), asset.asset_code)], kind='success', icon='fa-gift')
        return res


class AssetAsset(models.Model):
    _name = 'asset.asset'
    _inherit = ['asset.asset', 'asset.sn.mixin']
