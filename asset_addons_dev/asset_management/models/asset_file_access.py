# -*- coding: utf-8 -*-

from odoo import models, fields, api , _
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)


class AssetFileAccessRecord(models.Model):
    """
    Stores scanned files from Desktop/Documents/Downloads on each asset.
    Agent pushes this data every 60 seconds via /api/asset/file_access/scan.
    Odoo UI reads ONLY from this DB table — no live calls to agent IPs.
    """
    _name = 'asset.file.access.record'
    _description = 'Asset File Access Records'
    _order = 'parent_folder, record_type desc, name'
    # Composite index on (asset_id, parent_folder) for fast per-asset-folder queries

    asset_id = fields.Many2one(
        'asset.asset', string='Asset', required=True,
        ondelete='cascade', index=True
    )
    serial_number = fields.Char(
        related='asset_id.serial_number', string='Serial Number',
        store=True, readonly=True, index=True
    )

    record_type = fields.Selection([
        ('file', 'File'),
        ('folder', 'Folder'),
    ], string='Type', default='file', required=True)

    name = fields.Char(string='Name', required=True, index=True)
    path = fields.Char(string='Full Path', required=True)

    # The path of the containing folder. Used for subfolder navigation:
    #   SELECT * FROM asset_file_access_record
    #   WHERE asset_id = X AND parent_path = currentPath
    # Example: path = C:\Users\X\Desktop\agent files\agent.py
    #          parent_path = C:\Users\X\Desktop\agent files
    parent_path = fields.Char(string='Parent Path', index=True)

    parent_folder = fields.Selection([
        ('Desktop', 'Desktop'),
        ('Documents', 'Documents'),
        ('Downloads', 'Downloads'),
        ('Pictures', 'Pictures'),
        ('Trash', 'Trash'),
    ], string='Parent Folder', required=True, index=True)

    size_kb = fields.Float(string='Size (KB)', digits=(16, 2))
    last_modified = fields.Datetime(string='Last Modified')

    # When the agent ran the scan that produced this record
    scanned_at = fields.Datetime(string='Scanned At', index=True)

    # Policy status — denormalised copy kept in sync with asset.file.access.policy
    is_blocked = fields.Boolean(
        string='Blocked by Policy', default=False, index=True
    )

    @api.constrains('asset_id', 'path')
    def _check_unique_asset_path(self):
        for record in self:
            if self.search_count([
                ('asset_id', '=', record.asset_id.id),
                ('path', '=', record.path),
                ('id', '!=', record.id)
            ]) > 0:
                raise ValidationError(_('This file path already exists for this asset!'))

    def toggle_block(self):
        """Toggle blocking for this specific file/folder by creating/removing a policy."""
        self.ensure_one()
        path = self.path
        asset_id = self.asset_id.id

        policy = self.env['asset.file.access.policy'].search([
            ('asset_id', '=', asset_id),
            ('path', '=', path),
        ], limit=1)

        if policy:
            policy.unlink()
            is_blocked = False
        else:
            self.env['asset.file.access.policy'].create({
                'asset_id': asset_id,
                'path': path,
                'is_blocked': True,
                'reason': f"Blocked from File Access UI for {self.name}",
            })
            is_blocked = True

        # Update ALL records for this path on this asset (covers duplicates from old scans)
        self.env['asset.file.access.record'].search([
            ('asset_id', '=', asset_id),
            ('path', '=', path),
        ]).write({'is_blocked': is_blocked})

        return True


class AssetFileAccessPolicy(models.Model):
    """
    Defines which files/folders should be blocked on each asset.
    Admin configures these policies in the UI.
    Agent polls /api/asset/file_access/policy to enforce them.
    """
    _name = 'asset.file.access.policy'
    _description = 'Asset File Access Policy'
    _order = 'asset_id, path'

    asset_id = fields.Many2one(
        'asset.asset', string='Asset', required=True,
        ondelete='cascade', index=True
    )
    serial_number = fields.Char(
        related='asset_id.serial_number', string='Serial Number',
        store=True, readonly=True
    )

    path = fields.Char(
        string='Path to Block', required=True,
        help='Exact file or folder path to block'
    )
    is_blocked = fields.Boolean(string='Block Access', default=True, index=True)

    reason = fields.Text(string='Reason for Blocking')
    created_by = fields.Many2one(
        'res.users', string='Created By', default=lambda self: self.env.user
    )
    created_date = fields.Datetime(
        string='Created Date', default=fields.Datetime.now
    )

    @api.constrains('asset_id', 'path')
    def _check_unique_asset_policy_path(self):
        for policy in self:
            if self.search_count([
                ('asset_id', '=', policy.asset_id.id),
                ('path', '=', policy.path),
                ('id', '!=', policy.id)
            ]) > 0:
                raise ValidationError(_('This path is already in the policy for this asset!'))

    def unlink_and_update(self):
        """Unlink policy and update corresponding file records to 'unblocked'."""
        for policy in self:
            path = policy.path
            asset_id = policy.asset_id.id
            policy.unlink()

            self.env['asset.file.access.record'].sudo().search([
                ('asset_id', '=', asset_id),
                ('path', '=', path),
            ]).write({'is_blocked': False})
        return True


class AssetFileAccessViolation(models.Model):
    """
    Logs when a user attempts to access a blocked file/folder.
    Agent reports these violations in real-time via /api/asset/file_access/violation.
    """
    _name = 'asset.file.access.violation'
    _description = 'Asset File Access Violations'
    _order = 'violation_time desc'

    asset_id = fields.Many2one(
        'asset.asset', string='Asset', required=True,
        ondelete='cascade', index=True
    )
    serial_number = fields.Char(
        related='asset_id.serial_number', string='Serial Number',
        store=True, readonly=True
    )

    path = fields.Char(string='Path Accessed', required=True)
    folder = fields.Char(string='Folder')
    filename = fields.Char(string='Filename')

    action_taken = fields.Selection([
        ('blocked', 'Access Blocked'),
        ('blocked_by_policy', 'Blocked by Policy'),
        ('allowed', 'Access Allowed'),
    ], string='Action Taken', default='blocked', required=True)

    violation_time = fields.Datetime(
        string='Violation Time', default=fields.Datetime.now,
        required=True, index=True
    )

    username = fields.Char(string='Username')
    process_name = fields.Char(string='Process Name')


class AssetAsset(models.Model):
    _inherit = 'asset.asset'

    # ── File Access Statistics ──────────────────────────────────────────────
    file_access_record_count = fields.Integer(
        string='File Records',
        compute='_compute_file_access_counts',
        store=False,
    )
    file_access_policy_count = fields.Integer(
        string='Blocked Policies',
        compute='_compute_file_access_counts',
        store=False,
    )
    file_access_violation_count = fields.Integer(
        string='Access Violations',
        compute='_compute_file_access_counts',
        store=False,
    )

    last_file_access_scan = fields.Datetime(
        string='Last File Scan', readonly=True
    )

    # ── One2many relationships ──────────────────────────────────────────────
    file_access_record_ids = fields.One2many(
        'asset.file.access.record', 'asset_id', string='File Access Records'
    )
    file_access_policy_ids = fields.One2many(
        'asset.file.access.policy', 'asset_id', string='File Access Policies'
    )
    file_access_violation_ids = fields.One2many(
        'asset.file.access.violation', 'asset_id', string='File Access Violations'
    )

    @api.depends('file_access_record_ids', 'file_access_policy_ids',
                 'file_access_violation_ids')
    def _compute_file_access_counts(self):
        for asset in self:
            asset.file_access_record_count = len(asset.file_access_record_ids)
            asset.file_access_policy_count = len(
                asset.file_access_policy_ids.filtered(lambda p: p.is_blocked)
            )
            asset.file_access_violation_count = len(asset.file_access_violation_ids)

    # ── Action helpers ──────────────────────────────────────────────────────
    def action_view_file_access_records(self):
        """Open file access records for this asset."""
        self.ensure_one()
        return {
            'name': f'File Access Records — {self.asset_name}',
            'type': 'ir.actions.act_window',
            'res_model': 'asset.file.access.record',
            'view_mode': 'list,form',
            'domain': [('asset_id', '=', self.id)],
            'context': {'default_asset_id': self.id},
        }

    def action_view_file_access_policies(self):
        """Open file access policies for this asset."""
        self.ensure_one()
        return {
            'name': f'File Access Policies — {self.asset_name}',
            'type': 'ir.actions.act_window',
            'res_model': 'asset.file.access.policy',
            'view_mode': 'list,form',
            'domain': [('asset_id', '=', self.id)],
            'context': {'default_asset_id': self.id},
        }

    def action_view_file_access_violations(self):
        """Open file access violations for this asset."""
        self.ensure_one()
        return {
            'name': f'Access Violations — {self.asset_name}',
            'type': 'ir.actions.act_window',
            'res_model': 'asset.file.access.violation',
            'view_mode': 'list,form',
            'domain': [('asset_id', '=', self.id)],
            'context': {'default_asset_id': self.id},
        }

    def action_lock_all_folders(self):
        """Create block policies for all top-level folders currently scanned."""
        self.ensure_one()
        folders = self.env['asset.file.access.record'].search([
            ('asset_id', '=', self.id),
            ('record_type', '=', 'folder'),
        ])
        for folder in folders:
            existing = self.env['asset.file.access.policy'].search([
                ('asset_id', '=', self.id),
                ('path', '=', folder.path),
            ], limit=1)
            if not existing:
                self.env['asset.file.access.policy'].create({
                    'asset_id': self.id,
                    'path': folder.path,
                    'is_blocked': True,
                    'reason': 'Bulk lock from dashboard',
                })
                folder.is_blocked = True
        return True

    def action_block_file(self):
        """
        Wizard action to block a file/folder on this asset.
        Called from the File Access tab in the asset form view.
        """
        self.ensure_one()
        return {
            'name': 'Block File/Folder Access',
            'type': 'ir.actions.act_window',
            'res_model': 'asset.file.access.policy',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_asset_id': self.id,
                'default_is_blocked': True,
            },
        }