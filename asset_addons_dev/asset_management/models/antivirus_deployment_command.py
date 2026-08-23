# -*- coding: utf-8 -*-
"""
Antivirus Deployment Command Model

This model tracks individual antivirus deployment commands sent to device agents.
Each command represents a deployment task that agents poll and execute.
"""

from odoo import models, fields, api, _
import logging

_logger = logging.getLogger(__name__)


class AntivirusDeploymentCommand(models.Model):
    """
    Model to track antivirus deployment commands sent to device agents.

    Workflow:
    1. Admin initiates deployment to selected devices
    2. Command records are created with status 'pending'
    3. Agent polls /api/agent/antivirus/pending every 2 minutes
    4. Agent downloads installer and executes installation
    5. Agent reports status updates to /api/agent/antivirus/status
    6. Command status progresses: pending -> sent -> downloading -> installing -> completed/failed
    """
    _name = "antivirus.deployment.command"
    _description = "Antivirus Deployment Command"
    _order = "create_date desc"

    # Device reference (required, cascade delete)
    device_id = fields.Many2one(
        'asset.asset',
        string='Device',
        required=True,
        ondelete='cascade',
        help='Target device for this deployment command'
    )

    # Related fields from device (stored for quick access)
    device_code = fields.Char(
        string='Device Code',
        related='device_id.asset_code',
        store=True,
        readonly=True
    )

    device_name = fields.Char(
        string='Device Name',
        related='device_id.asset_name',
        store=True,
        readonly=True
    )

    serial_number = fields.Char(
        string='Serial Number',
        related='device_id.serial_number',
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
        related='device_id.os_platform',
        store=True,
        readonly=True
    )

    # Installer URL for this deployment
    installer_url = fields.Char(
        string='Installer URL',
        required=True,
        help='URL to download the antivirus installer'
    )

    # Command type
    command_type = fields.Selection([
        ('install', 'Install'),
        ('update', 'Update'),
        ('uninstall', 'Uninstall'),
    ], string='Command Type', required=True, default='install')

    # Deployment status
    state = fields.Selection([
        ('pending', 'Pending'),
        ('sent', 'Sent to Agent'),
        ('downloading', 'Downloading'),
        ('installing', 'Installing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ], string='Status', required=True, default='pending', index=True)

    # Error handling
    error_message = fields.Text(string='Error Message')
    agent_log = fields.Text(string='Agent Log')

    # Antivirus product info
    antivirus_product = fields.Char(string='Antivirus Product')
    antivirus_version = fields.Char(string='Antivirus Version')

    # Timestamps
    created_date = fields.Datetime(string='Created Date', default=fields.Datetime.now)
    sent_date = fields.Datetime(string='Sent Date')
    downloading_date = fields.Datetime(string='Downloading Date')
    installing_date = fields.Datetime(string='Installing Date')
    completed_date = fields.Datetime(string='Completed Date')

    # Agent acknowledgment
    agent_acknowledged = fields.Boolean(string='Agent Acknowledged', default=False)
    agent_ack_date = fields.Datetime(string='Agent Acknowledgment Date')

    # Deployment priority
    priority = fields.Selection([
        ('low', 'Low'),
        ('normal', 'Normal'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ], string='Priority', default='normal')

    # Retry tracking
    retry_count = fields.Integer(string='Retry Count', default=0)
    max_retries = fields.Integer(string='Max Retries', default=3)

    # Computed duration
    duration_minutes = fields.Float(
        string='Duration (Minutes)',
        compute='_compute_duration_minutes',
        store=True,
        help='Installation duration in minutes'
    )

    @api.depends('installing_date', 'completed_date')
    def _compute_duration_minutes(self):
        """Compute duration in minutes from installing_date to completed_date."""
        for record in self:
            if record.installing_date and record.completed_date:
                delta = record.completed_date - record.installing_date
                record.duration_minutes = delta.total_seconds() / 60.0
            else:
                record.duration_minutes = 0.0

    def create_deployment_commands(self, device_ids, installer_url, command_type='install', priority='normal'):
        """
        Create deployment commands for multiple devices.

        Args:
            device_ids: List of device (asset) IDs to deploy to
            installer_url: URL to download the installer
            command_type: Type of command (install/update/uninstall)
            priority: Priority level (low/normal/high/urgent)

        Returns:
            dict with 'created_count', 'skipped_count', and 'message'
        """
        if not device_ids:
            return {
                'created_count': 0,
                'skipped_count': 0,
                'message': 'No devices provided for deployment'
            }

        created_count = 0
        skipped_count = 0
        skipped_devices = []

        for device_id in device_ids:
            device = self.env['asset.asset'].browse(device_id)
            if not device.exists():
                skipped_count += 1
                continue

            # Check if device already has a pending/active command
            existing = self.search([
                ('device_id', '=', device_id),
                ('state', 'in', ['pending', 'sent', 'downloading', 'installing'])
            ], limit=1)

            if existing:
                skipped_count += 1
                skipped_devices.append(device.asset_name or device.serial_number or f'Device #{device_id}')
                continue

            # Create new pending command
            try:
                self.create({
                    'device_id': device_id,
                    'installer_url': installer_url,
                    'command_type': command_type,
                    'state': 'pending',
                    'priority': priority,
                })
                created_count += 1
            except Exception as e:
                _logger.error(f"Failed to create deployment command for device {device_id}: {e}")
                skipped_count += 1

        # Build message
        if created_count > 0 and skipped_count > 0:
            message = f"Successfully queued {created_count} device(s) for deployment. Skipped {skipped_count} device(s) with active deployments: {', '.join(skipped_devices)}"
        elif created_count > 0:
            message = f"Successfully queued {created_count} device(s) for antivirus deployment"
        elif skipped_count > 0:
            message = f"All {skipped_count} device(s) skipped. Devices with active deployments: {', '.join(skipped_devices)}"
        else:
            message = 'No devices were processed'

        return {
            'created_count': created_count,
            'skipped_count': skipped_count,
            'message': message
        }

    def action_retry_deployment(self):
        """Retry a failed deployment command."""
        self.ensure_one()
        if self.state != 'failed':
            return {
                'success': False,
                'message': 'Can only retry failed deployments'
            }

        self.write({
            'state': 'pending',
            'retry_count': self.retry_count + 1,
            'error_message': False,
            'completed_date': False,
        })

        return {
            'success': True,
            'message': 'Deployment retry initiated'
        }

    def action_cancel_deployment(self):
        """Cancel a pending or in-progress deployment."""
        self.ensure_one()
        if self.state in ['completed', 'failed']:
            return {
                'success': False,
                'message': 'Cannot cancel completed or failed deployments'
            }

        self.write({
            'state': 'failed',
            'error_message': 'Cancelled by user',
            'completed_date': fields.Datetime.now(),
        })

        return {
            'success': True,
            'message': 'Deployment cancelled'
        }

    def deploy_antivirus_to_devices(self, device_ids):
        """
        Deploy antivirus to selected devices.
        This method is called from the frontend OWL component.

        Args:
            device_ids: List of device (asset) IDs to deploy to

        Returns:
            dict with 'success', 'deployed_count', 'skipped_count', and 'message'
        """
        if not device_ids:
            return {
                'success': False,
                'deployed_count': 0,
                'skipped_count': 0,
                'message': 'No devices provided for deployment'
            }

        # Get antivirus config
        config = self.env['antivirus.config'].search([('is_default', '=', True)], limit=1)
        if not config:
            config = self.env['antivirus.config'].search([], limit=1)

        if not config:
            return {
                'success': False,
                'deployed_count': 0,
                'skipped_count': len(device_ids),
                'message': 'No antivirus configuration available. Please configure antivirus settings first.'
            }

        # Determine installer URL based on device platform
        deployed_count = 0
        skipped_count = 0
        skipped_devices = []

        for device_id in device_ids:
            device = self.env['asset.asset'].browse(device_id)
            if not device.exists():
                skipped_count += 1
                continue

            # Check if device already has a pending/active command
            existing = self.search([
                ('device_id', '=', device_id),
                ('state', 'in', ['pending', 'sent', 'downloading', 'installing'])
            ], limit=1)

            if existing:
                skipped_count += 1
                skipped_devices.append(device.asset_name or device.serial_number or f'Device #{device_id}')
                continue

            # Get installer URL for device platform
            platform = device.os_platform or 'unknown'
            installer_url = None

            if platform == 'windows':
                installer_url = config.installer_windows
            elif platform == 'linux':
                installer_url = config.installer_linux
            elif platform == 'macos':
                installer_url = config.installer_macos

            if not installer_url:
                skipped_count += 1
                skipped_devices.append(f'{device.asset_name or "Device"} (no installer for {platform})')
                continue

            # Create deployment command
            try:
                self.create({
                    'device_id': device_id,
                    'installer_url': installer_url,
                    'command_type': 'install',
                    'state': 'pending',
                    'priority': 'normal',
                    'antivirus_product': config.antivirus_product or 'unknown',
                })
                deployed_count += 1
            except Exception as e:
                _logger.error(f"Failed to create deployment command for device {device_id}: {e}")
                skipped_count += 1

        # Build message
        if deployed_count > 0 and skipped_count > 0:
            message = f"Successfully queued {deployed_count} device(s) for deployment. Skipped {skipped_count} device(s): {', '.join(skipped_devices)}"
        elif deployed_count > 0:
            message = f"Successfully queued {deployed_count} device(s) for antivirus deployment"
        elif skipped_count > 0:
            message = f"All {skipped_count} device(s) skipped: {', '.join(skipped_devices)}"
        else:
            message = 'No devices were processed'

        return {
            'success': True,
            'deployed_count': deployed_count,
            'skipped_count': skipped_count,
            'message': message
        }
