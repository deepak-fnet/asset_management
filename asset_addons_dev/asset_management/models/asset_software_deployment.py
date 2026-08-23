# -*- coding: utf-8 -*-
"""
Asset Software Deployment Model

Tracks software deployment status to Windows devices.
Manages the lifecycle from pending deployment to installation completion.
"""

from odoo import models, fields, api
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)


class AssetSoftwareDeployment(models.Model):
    """
    Software Deployment Tracking

    Tracks the deployment of software installers to target devices,
    including status, timestamps, error handling, and retry capability.
    """
    _name = 'asset.software.deployment'
    _description = 'Software Deployment Tracking'
    _order = 'deployed_date desc'

    # =========================================================================
    # CORE FIELDS
    # =========================================================================
    device_id = fields.Many2one(
        'asset.agent',
        string='Device',
        required=True,
        ondelete='cascade',
        index=True,
        help='Target Windows device for software deployment'
    )
    software_id = fields.Many2one(
        'asset.software.catalog',
        string='Software',
        required=True,
        ondelete='cascade',
        index=True,
        help='Software to be deployed'
    )

    # =========================================================================
    # STATUS TRACKING
    # =========================================================================
    status = fields.Selection([
        ('pending', 'Pending'),
        ('downloading', 'Downloading'),
        ('installing', 'Installing'),
        ('installed', 'Installed'),
        ('failed', 'Failed')
    ], string='Status', default='pending', required=True, index=True)

    # =========================================================================
    # TIMESTAMPS
    # =========================================================================
    deployed_by = fields.Many2one(
        'res.users',
        string='Deployed By',
        default=lambda self: self.env.user,
        readonly=True,
        help='User who initiated the deployment'
    )
    deployed_date = fields.Datetime(
        string='Deployment Created',
        default=fields.Datetime.now,
        required=True,
        readonly=True
    )
    started_date = fields.Datetime(
        string='Started Processing',
        readonly=True,
        help='When the agent started downloading/installing'
    )
    completed_date = fields.Datetime(
        string='Completed',
        readonly=True,
        help='When installation completed (success or failure)'
    )

    # =========================================================================
    # LOGS AND ERRORS
    # =========================================================================
    error_message = fields.Text(
        string='Error Message',
        readonly=True,
        help='Error details if deployment failed'
    )
    agent_log = fields.Text(
        string='Agent Log',
        readonly=True,
        help='Log output from the agent during installation'
    )
    retry_count = fields.Integer(
        string='Retry Count',
        default=0,
        readonly=True,
        help='Number of retry attempts'
    )

    # =========================================================================
    # RELATED FIELDS (for easy filtering/display)
    # =========================================================================
    device_name = fields.Char(
        string='Device Name',
        related='device_id.hostname',
        store=True,
        readonly=True
    )
    device_serial = fields.Char(
        string='Serial Number',
        related='device_id.agent_id',
        store=True,
        readonly=True
    )
    software_name = fields.Char(
        string='Software Name',
        related='software_id.name',
        store=True,
        readonly=True
    )
    software_version = fields.Char(
        string='Version',
        related='software_id.version',
        store=True,
        readonly=True
    )

    # =========================================================================
    # COLOR CODING FOR TREE VIEW
    # =========================================================================
    status_color = fields.Integer(
        string='Status Color',
        compute='_compute_status_color',
        help='Color code for kanban/tree view visualization'
    )

    @api.depends('status')
    def _compute_status_color(self):
        """Color code for kanban/tree view"""
        color_map = {
            'pending': 1,       # Gray
            'downloading': 4,   # Blue
            'installing': 3,    # Yellow
            'installed': 10,    # Green
            'failed': 1,        # Red
        }
        for record in self:
            record.status_color = color_map.get(record.status, 0)

    # =========================================================================
    # ACTION METHODS
    # =========================================================================
    def action_retry_deployment(self):
        """Reset status to pending for retry"""
        for record in self:
            record.write({
                'status': 'pending',
                'error_message': False,
                'retry_count': record.retry_count + 1,
                'started_date': False,
                'completed_date': False
            })
        return True

    def action_view_device(self):
        """View device details"""
        self.ensure_one()
        return {
            'name': f'Device: {self.device_name}',
            'type': 'ir.actions.act_window',
            'res_model': 'asset.agent',
            'view_mode': 'form',
            'res_id': self.device_id.id,
            'target': 'new',
        }

    def action_view_software(self):
        """View software details"""
        self.ensure_one()
        return {
            'name': f'Software: {self.software_name}',
            'type': 'ir.actions.act_window',
            'res_model': 'asset.software.catalog',
            'view_mode': 'form',
            'res_id': self.software_id.id,
            'target': 'new',
        }

    # =========================================================================
    # CRON METHODS
    # =========================================================================
    @api.model
    def _cron_cleanup_old_deployments(self):
        """Archive old completed/failed deployments (older than 90 days)"""
        cutoff_date = datetime.now() - timedelta(days=90)
        old_deployments = self.search([
            ('status', 'in', ['installed', 'failed']),
            ('completed_date', '<', cutoff_date)
        ])
        old_deployments.write({'active': False})
        _logger.info(
            f"Archived {len(old_deployments)} old software deployments"
        )
