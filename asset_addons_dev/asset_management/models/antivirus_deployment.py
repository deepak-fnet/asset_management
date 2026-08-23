# -*- coding: utf-8 -*-
"""
Antivirus Deployment Model

This model tracks the deployment status of antivirus software to assets.
It manages the lifecycle from pending deployment to installation completion.
"""

from odoo import models, fields, api, _
import logging

_logger = logging.getLogger(__name__)


class AntivirusDeployment(models.Model):
    """
    Model to track antivirus deployment to assets.
    
    Fields:
    - asset_id: Reference to the target asset
    - config_id: Antivirus configuration to use
    - status: Current deployment status (pending/downloading/installing/installed/failed/cancelled)
    - deployed_by: User who initiated the deployment
    - Timestamps for tracking deployment progress
    - Error handling and logging fields
    """
    _name = "antivirus.deployment"
    _description = "Antivirus Deployment"
    _order = "create_date desc"

    # Asset reference (required, cascade delete)
    asset_id = fields.Many2one(
        'asset.asset',
        string='Asset',
        required=True,
        ondelete='cascade',
        help='Target asset for antivirus deployment'
    )

    # Related fields from asset (stored for quick access)
    asset_code = fields.Char(
        string='Asset Code',
        related='asset_id.asset_code',
        store=True,
        readonly=True
    )

    device_name = fields.Char(
        string='Device Name',
        related='asset_id.asset_name',
        store=True,
        readonly=True
    )

    serial_number = fields.Char(
        string='Serial Number',
        related='asset_id.serial_number',
        store=True,
        readonly=True
    )

    os_platform = fields.Selection(
        [
            ('windows', 'Windows'),
            ('linux', 'Linux'),
            ('macos', 'macOS'),
            ('unknown', 'Unknown')
        ],
        string='OS Platform',
        related='asset_id.os_platform',
        store=True,
        readonly=True
    )

    # Antivirus configuration
    config_id = fields.Many2one(
        'antivirus.config',
        string='Antivirus Configuration',
        ondelete='set null',
        help='Antivirus configuration to use for this deployment'
    )

    # Deployment status
    status = fields.Selection([
        ('pending', 'Pending'),
        ('downloading', 'Downloading'),
        ('installing', 'Installing'),
        ('installed', 'Installed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ], string='Status', required=True, default='pending', index=True)

    # User who deployed
    deployed_by = fields.Many2one(
        'res.users',
        string='Deployed By',
        default=lambda self: self.env.user,
        readonly=True
    )

    # Timestamps
    started_at = fields.Datetime(string='Started At')
    completed_at = fields.Datetime(string='Completed At')

    # Error handling
    error_message = fields.Text(string='Error Message')
    agent_log = fields.Text(string='Agent Log')

    # Installation details
    installer_url_used = fields.Char(string='Installer URL Used')
    av_version_installed = fields.Char(string='Antivirus Version Installed')

    # Computed duration
    duration_minutes = fields.Float(
        string='Duration (Minutes)',
        compute='_compute_duration_minutes',
        store=True,
        help='Installation duration in minutes'
    )

    @api.depends('started_at', 'completed_at')
    def _compute_duration_minutes(self):
        """Compute duration in minutes from started_at to completed_at."""
        for record in self:
            if record.started_at and record.completed_at:
                delta = record.completed_at - record.started_at
                record.duration_minutes = delta.total_seconds() / 60.0
            else:
                record.duration_minutes = 0.0

    def deploy_to_assets(self, asset_ids, config_id=None):
        """
        Deploy antivirus to multiple assets.
        
        Args:
            asset_ids: List of asset IDs to deploy to
            config_id: Optional antivirus configuration ID. If not provided, uses default config.
        
        Returns:
            dict with 'deployed_count', 'skipped_count', and 'message'
        """
        if not asset_ids:
            return {
                'deployed_count': 0,
                'skipped_count': 0,
                'message': 'No assets provided for deployment'
            }

        # Find default config if not provided
        if not config_id:
            config = self.env['antivirus.config'].search([('is_default', '=', True)], limit=1)
            if not config:
                config = self.env['antivirus.config'].search([], limit=1)
            if config:
                config_id = config.id

        if not config_id:
            return {
                'deployed_count': 0,
                'skipped_count': len(asset_ids),
                'message': 'No antivirus configuration available. Please configure antivirus settings first.'
            }

        deployed_count = 0
        skipped_count = 0
        skipped_assets = []

        for asset_id in asset_ids:
            asset = self.env['asset.asset'].browse(asset_id)
            if not asset.exists():
                skipped_count += 1
                continue

            # Check if asset already has a pending/downloading/installing deployment
            existing = self.search([
                ('asset_id', '=', asset_id),
                ('status', 'in', ['pending', 'downloading', 'installing'])
            ], limit=1)

            if existing:
                skipped_count += 1
                skipped_assets.append(asset.asset_name or asset.serial_number or f'Asset #{asset_id}')
                continue

            # Create new pending deployment
            try:
                self.create({
                    'asset_id': asset_id,
                    'config_id': config_id,
                    'status': 'pending'
                })
                deployed_count += 1
            except Exception as e:
                _logger.error(f"Failed to create deployment for asset {asset_id}: {e}")
                skipped_count += 1

        # Build message
        if deployed_count > 0 and skipped_count > 0:
            message = f"Successfully queued {deployed_count} asset(s) for deployment. Skipped {skipped_count} asset(s) with active deployments: {', '.join(skipped_assets)}"
        elif deployed_count > 0:
            message = f"Successfully queued {deployed_count} asset(s) for antivirus deployment"
        elif skipped_count > 0:
            message = f"All {skipped_count} asset(s) skipped. Assets with active deployments: {', '.join(skipped_assets)}"
        else:
            message = 'No assets were processed'

        return {
            'deployed_count': deployed_count,
            'skipped_count': skipped_count,
            'message': message
        }
