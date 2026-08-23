# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class AssetLinuxUpdate(models.Model):
    _name = 'asset.linux.update'
    _description = 'Asset Linux Update'
    _order = 'detected_date desc, id desc'
    _rec_name = 'package_name'

    asset_id = fields.Many2one(
        'asset.asset', string='Asset',
        required=True, ondelete='cascade', index=True,
    )
    package_name  = fields.Char(string='Package Name', required=True, index=True)
    title         = fields.Char(string='Update Title')
    detected_date = fields.Date(string='Detected Date', default=fields.Date.today)
    current_version = fields.Char(string='Current Version')
    new_version   = fields.Char(string='New Version')
    repository    = fields.Char(string='Repository')
    severity      = fields.Selection([
        ('security',  'Security'),
        ('critical',  'Critical'),
        ('important', 'Important'),
        ('optional',  'Optional'),
    ], string='Severity', default='optional', required=True)
    size          = fields.Char(string='Size')
    description   = fields.Text(string='Description')

    status = fields.Selection([
        ('pending',      'Pending'),
        ('allowed',      'Allowed'),
        ('blocked',      'Blocked'),
        ('installing',   'Installing'),
        ('installed',    'Installed'),
        ('uninstalling', 'Uninstalling'),
        ('uninstalled',  'Uninstalled'),
        ('failed',       'Failed'),
    ], string='Status', default='pending', required=True)

    action_by   = fields.Many2one('res.users', string='Action By', readonly=True)
    action_date = fields.Datetime(string='Action Date', readonly=True)

    is_actionable = fields.Boolean(
        string='Is Actionable',
        compute='_compute_is_actionable',
    )

    @api.depends('status')
    def _compute_is_actionable(self):
        for r in self:
            r.is_actionable = r.status in ('pending', 'allowed', 'blocked')

    def _require_manager(self):
        if not self.env.user.has_group('asset_management.group_asset_manager'):
            raise UserError('Only Asset Managers can perform this action.')

    def action_block_update(self):
        self.ensure_one()
        self._require_manager()
        if self.status not in ('pending', 'allowed'):
            raise UserError(f'Cannot block an update with status "{self.status}".')
        self.write({'status': 'blocked', 'action_by': self.env.user.id, 'action_date': fields.Datetime.now()})
        _logger.info(f'[LU] {self.package_name} blocked by {self.env.user.name}')
        return True

    def action_push_update(self):
        self.ensure_one()
        self._require_manager()
        if self.status not in ('pending', 'allowed', 'blocked', 'failed'):
            raise UserError(f'Cannot queue update with status "{self.status}".')
        self.write({'status': 'installing', 'action_by': self.env.user.id, 'action_date': fields.Datetime.now()})
        _logger.info(f'[LU] {self.package_name} queued for install by {self.env.user.name}')
        return True

    def action_allow_update(self):
        self.ensure_one()
        self._require_manager()
        if self.status != 'blocked':
            raise UserError(f'Can only allow a blocked update.')
        self.write({'status': 'allowed', 'action_by': self.env.user.id, 'action_date': fields.Datetime.now()})
        return True

    def action_uninstall_update(self):
        """Queue this update for silent remote uninstall via agent."""
        self.ensure_one()
        self._require_manager()
        if self.status not in ('installed',):
            raise UserError(f'Can only uninstall an update that is installed (current: {self.status}).')
        self.write({'status': 'uninstalling', 'action_by': self.env.user.id, 'action_date': fields.Datetime.now()})
        _logger.info(f'[LU] {self.package_name} queued for uninstall by {self.env.user.name}')
        return True


class AssetAssetLinuxUpdate(models.Model):
    _inherit = 'asset.asset'

    linux_update_locked = fields.Boolean(
        string='Linux Updates Locked', default=False, tracking=True,
    )
    linux_update_ids = fields.One2many(
        'asset.linux.update', 'asset_id', string='Linux Updates',
    )
    linux_update_count = fields.Integer(
        string='Linux Update Count', compute='_compute_linux_update_count',
    )
    linux_pending_updates = fields.Integer(
        string='Linux Pending Updates', compute='_compute_linux_update_count',
    )

    @api.depends('linux_update_ids', 'linux_update_ids.status')
    def _compute_linux_update_count(self):
        for asset in self:
            asset.linux_update_count   = len(asset.linux_update_ids)
            asset.linux_pending_updates = len(
                asset.linux_update_ids.filtered(lambda u: u.status == 'pending')
            )

    def action_toggle_linux_lock(self):
        self.ensure_one()
        if not self.env.user.has_group('asset_management.group_asset_manager'):
            raise UserError('Only Asset Managers can lock/unlock Linux updates.')
        self.linux_update_locked = not self.linux_update_locked
        return True

    def action_lock_all_linux_updates(self):
        return self.action_toggle_linux_lock()

    def get_linux_update_data(self):
        """Return all data needed by the Linux update OWL widget in one call."""
        self.ensure_one()

        is_admin = self.env.user.has_group('asset_management.group_asset_manager')

        updates = []
        for u in self.linux_update_ids:
            updates.append({
                'id':              u.id,
                'package_name':    u.package_name or '',
                'title':           u.title or u.package_name or '',
                'detected_date':   u.detected_date.strftime('%d %b %Y') if u.detected_date else '',
                'current_version': u.current_version or 'N/A',
                'new_version':     u.new_version or 'N/A',
                'repository':      u.repository or '',
                'severity':        u.severity or 'optional',
                'size':            u.size or '',
                'description':     u.description or '',
                'status':          u.status or 'pending',
                'action_by':       u.action_by.name if u.action_by else '',
                'action_date':     u.action_date.strftime('%d %b %Y, %I:%M %p') if u.action_date else '',
                'is_actionable':   u.is_actionable,
            })

        activity_log = []
        for u in self.linux_update_ids.filtered(lambda u: u.action_date).sorted(
            key=lambda u: u.action_date, reverse=True
        ):
            activity_log.append({
                'id':           u.id,
                'package_name': u.package_name or '',
                'title':        u.title or u.package_name or '',
                'status':       u.status or 'pending',
                'action_by':    u.action_by.name if u.action_by else 'System',
                'action_date':  u.action_date.strftime('%d %b %Y, %I:%M %p') if u.action_date else '',
            })

        asset_name = ''
        for fname in ('asset_name', 'name', 'hostname'):
            if hasattr(self, fname):
                val = getattr(self, fname, '')
                if val:
                    asset_name = str(val)
                    break

        return {
            'asset_id':      self.id,
            'asset_name':    asset_name,
            'is_locked':     self.linux_update_locked,
            'is_admin':      is_admin,
            'update_count':  self.linux_update_count,
            'pending_count': self.linux_pending_updates,
            'updates':       updates,
            'activity_log':  activity_log,
        }
