# -*- coding: utf-8 -*-
"""
App Deployment Model

New package-manager-based software deployment system.
Replaces the old software library upload system with winget/chocolatey/brew/apt commands.
"""

from odoo import models, fields, api
from datetime import datetime
import logging

_logger = logging.getLogger(__name__)


class AppDeployment(models.Model):
    """
    App Deployment Tracking

    Tracks deployment of applications via package managers (winget, chocolatey, brew, apt)
    to target devices across the organization.
    """
    _name = 'asset_management.app_deployment'
    _description = 'App Deployment'
    _order = 'deployment_created desc'
    _rec_name = 'name'

    # =========================================================================
    # SEQUENCE & REFERENCE
    # =========================================================================
    name = fields.Char(
        string='Deployment Reference',
        required=True,
        readonly=True,
        default='New',
        help='Auto-generated deployment reference (e.g., DEP-001)'
    )

    # =========================================================================
    # TARGET DEVICE
    # =========================================================================
    device_id = fields.Many2one(
        'asset.asset',
        string='Device',
        required=True,
        ondelete='cascade',
        index=True,
        help='Target device for application deployment'
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

    # =========================================================================
    # APPLICATION DETAILS
    # =========================================================================
    application_name = fields.Char(
        string='Application Name',
        required=True,
        help='Name of the application to deploy (e.g., "Google Chrome")'
    )

    # install method: package manager or direct URL download
    application_source = fields.Selection([
        ('preset', 'Package Manager'),
        ('url', 'URL Installer'),
    ], string='Source', required=True, default='preset', index=True)

    package_manager = fields.Selection([
        ('winget', 'winget (Windows)'),
        ('chocolatey', 'Chocolatey (Windows)'),
        ('homebrew', 'Homebrew (macOS)'),
        ('apt', 'apt (Linux)'),
        ('custom', 'Custom Command'),
        ('url', 'URL Installer'),
    ], string='Package Manager', required=True, index=True)

    action_type = fields.Selection([
        ('install', 'Install'),
        ('uninstall', 'Uninstall'),
    ], string='Action', required=True, default='install', index=True)

    install_command = fields.Text(
        string='Command',
        required=True,
        help='Full command to execute on the target device'
    )

    # URL Installer fields
    installer_url = fields.Char(
        string='Installer URL',
        help='Direct download URL to the installer (e.g., GitHub Releases)'
    )
    installer_type = fields.Selection([
        ('exe',      'Windows Executable (.exe)'),
        ('msi',      'Windows Installer (.msi)'),
        ('deb',      'Debian Package (.deb)'),
        ('rpm',      'RPM Package (.rpm)'),
        ('pkg',      'macOS Package (.pkg)'),
        ('dmg',      'macOS Disk Image (.dmg)'),
        ('appimage', 'Linux AppImage'),
        ('zip',      'ZIP Archive'),
    ], string='Installer Type')
    installer_args = fields.Char(
        string='Install Arguments',
        help='Silent install arguments (e.g., /S, /quiet, /qn)'
    )

    # =========================================================================
    # NOTES
    # =========================================================================
    notes = fields.Text(
        string='Notes',
        help='Optional deployment reason or additional notes'
    )

    # =========================================================================
    # STATUS TRACKING
    # =========================================================================
    status = fields.Selection([
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('success', 'Succeeded'),
        ('failed', 'Failed')
    ], string='Status', default='pending', required=True, index=True)

    # =========================================================================
    # TIMESTAMPS
    # =========================================================================
    deployment_created = fields.Datetime(
        string='Deployment Created',
        default=fields.Datetime.now,
        required=True,
        readonly=True
    )
    completed = fields.Datetime(
        string='Completed',
        readonly=True,
        help='When deployment finished (success or failure)'
    )
    error_message = fields.Text(
        string='Error Message',
        readonly=True,
        help='Error details if deployment failed'
    )

    # =========================================================================
    # AUDIT
    # =========================================================================
    created_by = fields.Many2one(
        'res.users',
        string='Created By',
        default=lambda self: self.env.user,
        readonly=True,
        help='User who created the deployment task'
    )

    # =========================================================================
    # SEQUENCE GENERATION
    # =========================================================================
    @api.model_create_multi
    def create(self, vals_list):
        """Generate sequence name for new deployments"""
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('app.deployment') or 'New'
        return super().create(vals_list)

    # =========================================================================
    # ACTION METHODS
    # =========================================================================
    def action_retry_deployment(self):
        """Reset status to pending for retry"""
        for record in self:
            record.write({
                'status': 'pending',
                'error_message': False,
                'completed': False
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
            'in_progress': 3,   # Yellow
            'success': 10,      # Green
            'failed': 1,        # Red
        }
        for record in self:
            record.status_color = color_map.get(record.status, 0)
