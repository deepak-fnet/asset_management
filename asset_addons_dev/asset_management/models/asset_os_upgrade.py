# -*- coding: utf-8 -*-
"""Linux distro release upgrades (e.g. Ubuntu 24.04 -> 26.04).

Deliberately separate from asset.linux.update: that model tracks individual
apt packages (patch-level, low-risk, routine). A distro release upgrade is a
single, long-running, disruptive operation that always needs a reboot and can
leave a machine broken if it fails partway - so this gets its own model, its
own explicit admin acknowledgement, and a mandatory maintenance window,
instead of being just another row with a different severity.

The agent side is NOT included here - the production agent is a compiled
binary outside this repo. What this model provides is the Odoo-side queue: an
admin schedules an upgrade, the agent polls get_pending_for_serial() to see
if one is due, runs it, and reports back through report_result(). See
agent_snippets/os_upgrade.py for the reference implementation to paste into
the real agent.
"""
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

import logging

_logger = logging.getLogger(__name__)


class AssetOsUpgradeRequest(models.Model):
    _name = 'asset.os.upgrade.request'
    _description = 'Asset OS Upgrade Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'
    _rec_name = 'name'

    name = fields.Char(
        string='Reference', required=True, copy=False,
        readonly=True, default=lambda self: _('New'),
    )
    asset_id = fields.Many2one(
        'asset.asset', string='Asset', required=True, ondelete='cascade',
        index=True, tracking=True,
        domain=[('platform', 'in', ('linux', 'windows'))],
        help='Linux (distro release upgrade, e.g. 24.04 -> 26.04) or '
             'Windows (feature upgrade, e.g. Windows 10 -> 11). macOS is '
             'not supported by this model yet.',
    )
    serial_number = fields.Char(related='asset_id.serial_number', store=True, readonly=True)

    # Snapshot at request time, not related to asset_id - the whole point is
    # to compare "what it was on when this was requested" against "what it's
    # on now" (result_os_version), so this must not move if the asset syncs
    # again before the upgrade runs.
    current_os_version = fields.Char(string='Current Version', readonly=True)
    current_os_codename = fields.Char(string='Current Codename', readonly=True)

    target_version = fields.Char(
        string='Target Version', required=True,
        help="e.g. '26.04 LTS' (Linux) or 'Windows 11' (Windows). Free text "
             "shown to the admin/in the log - target_codename is what the "
             "agent actually acts on.")
    target_codename = fields.Char(
        string='Target Codename', required=True,
        help="Linux: e.g. 'plucky' - must match a codename do-release-"
             "upgrade on the asset can actually reach from its current "
             "release. Windows: just '11' - there is no codename "
             "equivalent, this only distinguishes the request from a "
             "future non-11 target.")

    state = fields.Selection([
        ('draft', 'Draft'),
        ('approved', 'Approved / Scheduled'),
        ('in_progress', 'In Progress'),
        ('done', 'Done'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='draft', required=True, tracking=True, copy=False)

    requires_reboot = fields.Boolean(
        default=True, readonly=True,
        help='Always true for a distro release upgrade - shown so this is '
             'never mistaken for a routine patch that applies silently.')

    admin_acknowledged = fields.Boolean(
        string='I understand this is disruptive',
        help='Must be ticked before this can be approved: a distro release '
             'upgrade is long-running, requires a reboot, has no interactive '
             'consent point once started, and can leave the machine in a '
             'broken state if it fails partway.',
    )
    scheduled_date = fields.Datetime(
        string='Maintenance Window (Start)', tracking=True,
        help='The agent will not start this upgrade before this time, even '
             'once approved.',
    )

    requested_by = fields.Many2one('res.users', string='Requested By',
                                    default=lambda self: self.env.user, readonly=True)
    request_date = fields.Datetime(string='Requested On', default=fields.Datetime.now, readonly=True)
    approved_by = fields.Many2one('res.users', string='Approved By', readonly=True, copy=False)
    approved_date = fields.Datetime(string='Approved On', readonly=True, copy=False)
    started_date = fields.Datetime(string='Started On', readonly=True, copy=False)
    completed_date = fields.Datetime(string='Completed On', readonly=True, copy=False)

    result_os_version = fields.Char(string='Resulting Version', readonly=True, copy=False)
    result_os_codename = fields.Char(string='Resulting Codename', readonly=True, copy=False)
    error_message = fields.Text(string='Error', readonly=True, copy=False)
    agent_log = fields.Text(string='Agent Log', readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'asset.os.upgrade.request') or _('New')
            if vals.get('asset_id') and not vals.get('current_os_version'):
                asset = self.env['asset.asset'].browse(vals['asset_id'])
                vals['current_os_version'] = asset.os_version
                vals['current_os_codename'] = asset.os_codename
        return super().create(vals_list)

    @api.constrains('scheduled_date', 'state')
    def _check_scheduled_date(self):
        for rec in self:
            if rec.state == 'approved' and not rec.scheduled_date:
                raise ValidationError(_('Set a maintenance window before approving.'))

    def _require_manager(self):
        if not self.env.user.has_group('asset_management.group_asset_manager'):
            raise UserError(_('Only Asset Managers can perform this action.'))

    def action_approve(self):
        self._require_manager()
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_('Only a Draft request can be approved.'))
            if not rec.admin_acknowledged:
                raise UserError(_(
                    'Tick "I understand this is disruptive" before approving - '
                    'this action requires a reboot and cannot be interactively '
                    'confirmed once it starts on the machine.'))
            if not rec.scheduled_date:
                raise UserError(_('Set a maintenance window (start time) before approving.'))
            if rec.scheduled_date < fields.Datetime.now():
                raise UserError(_('The maintenance window must be in the future.'))
            if rec.asset_id.platform == 'windows' and rec.target_codename == '11' \
                    and rec.asset_id.win11_status != 'eligible':
                raise UserError(_(
                    'This asset is not confirmed eligible for Windows 11 '
                    '(status: %s). Run/refresh the Windows 11 eligibility '
                    'scan first - approving here would start an upgrade '
                    'that is likely to fail or be blocked on the machine.'
                ) % (dict(rec.asset_id._fields['win11_status'].selection)
                     .get(rec.asset_id.win11_status, rec.asset_id.win11_status)))
            rec.write({
                'state': 'approved',
                'approved_by': self.env.user.id,
                'approved_date': fields.Datetime.now(),
            })
            rec.message_post(body=_(
                'Approved. Scheduled for %s. The agent will start this once it '
                'polls at or after that time.'
            ) % rec.scheduled_date)

    def action_cancel(self):
        for rec in self:
            if rec.state in ('done',):
                raise UserError(_('A completed upgrade cannot be cancelled.'))
            if rec.state == 'in_progress':
                rec.message_post(body=_(
                    'Cancelled from Odoo - this does NOT stop an upgrade '
                    'already running on the machine. It only stops Odoo from '
                    'reporting this as outstanding.'))
            rec.state = 'cancelled'

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state not in ('cancelled', 'failed'):
                raise UserError(_('Only a Cancelled or Failed request can be reset to Draft.'))
            rec.write({
                'state': 'draft',
                'approved_by': False,
                'approved_date': False,
                'error_message': False,
            })

    # ------------------------------------------------------------------
    # Agent-facing (called from controllers/asset_os_upgrade_api.py)
    # ------------------------------------------------------------------
    @api.model
    def get_pending_for_serial(self, serial_number):
        """The one approved, due request for this asset, if any.

        Only ever returns ONE record: an asset mid-upgrade should not be
        offered a second one, and picking the earliest due keeps this
        deterministic if more than one was ever approved by mistake.
        """
        asset = self.env['asset.asset'].sudo().search(
            [('serial_number', '=', serial_number)], limit=1)
        if not asset:
            return None
        req = self.sudo().search([
            ('asset_id', '=', asset.id),
            ('state', '=', 'approved'),
            ('scheduled_date', '<=', fields.Datetime.now()),
        ], order='scheduled_date asc', limit=1)
        if not req:
            return None
        return {
            'request_id': req.id,
            'target_version': req.target_version,
            'target_codename': req.target_codename,
        }

    @api.model
    def report_result(self, serial_number, request_id, status, message=None,
                       new_os_version=None, new_os_codename=None):
        """Agent reports progress/result. status: in_progress|done|failed."""
        req = self.sudo().browse(request_id)
        if not req.exists():
            return {'success': False, 'message': 'Request not found'}
        if req.asset_id.serial_number != serial_number:
            # Never let one machine's agent update another asset's request.
            return {'success': False, 'message': 'serial_number does not match this request'}

        if status == 'in_progress':
            vals = {'state': 'in_progress'}
            if not req.started_date:
                vals['started_date'] = fields.Datetime.now()
        elif status == 'done':
            vals = {
                'state': 'done',
                'completed_date': fields.Datetime.now(),
                'result_os_version': new_os_version,
                'result_os_codename': new_os_codename,
            }
            if new_os_version:
                req.asset_id.sudo().write({
                    'os_version': new_os_version,
                    'os_codename': new_os_codename,
                })
        elif status == 'failed':
            vals = {
                'state': 'failed',
                'completed_date': fields.Datetime.now(),
                'error_message': message or 'Unknown error',
            }
        else:
            return {'success': False, 'message': 'status must be in_progress/done/failed'}

        if message:
            existing = req.agent_log or ''
            stamp = fields.Datetime.now()
            vals['agent_log'] = f"{existing}\n[{stamp}] {message}".strip()

        req.write(vals)
        req.message_post(body=_('Agent reported: %s%s') % (
            status, f' - {message}' if message else ''))
        return {'success': True}
