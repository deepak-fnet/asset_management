# -*- coding: utf-8 -*-
"""
Asset Software Catalog Model

Enterprise software deployment system - allows admins to upload installers
and automatically deploy them to Windows devices via the agent.
"""

from odoo import models, fields, api
import base64
import logging

_logger = logging.getLogger(__name__)


class AssetSoftwareCatalog(models.Model):
    """
    Software Installer Library

    Stores software installers (.exe/.msi) with metadata for deployment
    to Windows devices across the organization.
    """
    _name = 'asset.software.catalog'
    _description = 'Software Installer Library'
    _order = 'name, version desc'

    # =========================================================================
    # BASIC INFORMATION
    # =========================================================================
    name = fields.Char(
        string='Software Name',
        required=True,
        index=True,
        help='Name of the software application'
    )
    version = fields.Char(
        string='Version',
        required=True,
        help='Software version number'
    )
    publisher = fields.Char(
        string='Publisher',
        help='Software publisher/vendor name'
    )
    category = fields.Selection([
        ('browser', 'Browser'),
        ('office', 'Office'),
        ('development', 'Development'),
        ('security', 'Security'),
        ('utilities', 'Utilities'),
        ('other', 'Other')
    ], string='Category', default='other', required=True, index=True)

    # =========================================================================
    # INSTALLER FILE STORAGE
    # =========================================================================
    installer_file = fields.Binary(
        string='Installer File',
        required=True,
        attachment=True,
        help="Upload .exe or .msi installer file"
    )
    installer_filename = fields.Char(
        string='Filename',
        required=True,
        help='Original filename of the installer'
    )
    installer_size = fields.Float(
        string='File Size (MB)',
        compute='_compute_file_size',
        store=True,
        help='Size of the installer file in megabytes'
    )

    # =========================================================================
    # DOWNLOAD URL FOR AGENT
    # =========================================================================
    installer_url = fields.Char(
        string='Download URL',
        compute='_compute_installer_url',
        help='Generated URL for agent to download installer'
    )

    # =========================================================================
    # INSTALLATION SETTINGS
    # =========================================================================
    silent_flags = fields.Char(
        string='Silent Install Flags',
        compute='_compute_silent_flags',
        store=True,
        readonly=True,
        help='Auto-detected based on installer file type'
    )
    platform = fields.Selection([
        ('windows', 'Windows'),
        ('mac', 'Mac'),
        ('linux', 'Linux')
    ], string='Platform', default='windows', required=True)

    description = fields.Text(
        string='Description',
        help='Optional description of the software'
    )
    active = fields.Boolean(
        string='Active',
        default=True,
        help='Inactive software will not be available for deployment'
    )

    # =========================================================================
    # STATISTICS
    # =========================================================================
    deployment_count = fields.Integer(
        string='Total Deployments',
        compute='_compute_deployment_stats',
        help='Total number of deployment attempts'
    )
    success_count = fields.Integer(
        string='Successful',
        compute='_compute_deployment_stats',
        help='Number of successful installations'
    )
    failed_count = fields.Integer(
        string='Failed',
        compute='_compute_deployment_stats',
        help='Number of failed installations'
    )
    success_rate = fields.Float(
        string='Success Rate (%)',
        compute='_compute_deployment_stats',
        help='Percentage of successful deployments'
    )

    # =========================================================================
    # AUDIT FIELDS
    # =========================================================================
    create_uid = fields.Many2one('res.users', string='Uploaded By', readonly=True)
    create_date = fields.Datetime(string='Upload Date', readonly=True)

    # =========================================================================
    # COMPUTED METHODS
    # =========================================================================
    @api.depends('installer_file')
    def _compute_file_size(self):
        """Calculate file size from binary data"""
        for record in self:
            if record.installer_file:
                try:
                    decoded = base64.b64decode(record.installer_file)
                    record.installer_size = len(decoded) / (1024 * 1024)
                except Exception:
                    record.installer_size = 0.0
            else:
                record.installer_size = 0.0

    @api.depends('installer_filename')
    def _compute_installer_url(self):
        """Generate download URL for agent"""
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for record in self:
            if record.id and record.installer_filename:
                record.installer_url = (
                    f"{base_url}/web/content/asset.software.catalog/"
                    f"{record.id}/installer_file/{record.installer_filename}?download=true"
                )
            else:
                record.installer_url = False

    @api.depends('installer_filename')
    def _compute_silent_flags(self):
        """Auto-detect silent install flags based on file extension"""
        for record in self:
            if not record.installer_filename:
                record.silent_flags = '/S'  # Default
                continue
            
            filename_lower = record.installer_filename.lower()
            
            if filename_lower.endswith('.msi'):
                # MSI installers use msiexec
                record.silent_flags = '/quiet /norestart'
            elif filename_lower.endswith('.exe'):
                # Most EXE installers support /S (Inno Setup, NSIS)
                record.silent_flags = '/S'
            else:
                # Fallback
                record.silent_flags = '/S'

    def _compute_deployment_stats(self):
        """Calculate deployment statistics"""
        Deployment = self.env['asset.software.deployment']
        for record in self:
            deployments = Deployment.search([
                ('software_id', '=', record.id)
            ])
            record.deployment_count = len(deployments)
            record.success_count = len(deployments.filtered(
                lambda d: d.status == 'installed'
            ))
            record.failed_count = len(deployments.filtered(
                lambda d: d.status == 'failed'
            ))
            if record.deployment_count > 0:
                record.success_rate = (
                    record.success_count / record.deployment_count
                ) * 100
            else:
                record.success_rate = 0.0

    # =========================================================================
    # ACTION METHODS
    # =========================================================================
    def action_deploy_software(self):
        """Open deployment wizard"""
        self.ensure_one()
        return {
            'name': 'Deploy Software',
            'type': 'ir.actions.act_window',
            'res_model': 'deploy.software.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_software_ids': [(6, 0, self.ids)],
            }
        }

    def action_view_deployments(self):
        """View all deployments for this software"""
        self.ensure_one()
        return {
            'name': f'Deployments: {self.name} {self.version}',
            'type': 'ir.actions.act_window',
            'res_model': 'asset.software.deployment',
            'view_mode': 'list,form',
            'domain': [('software_id', '=', self.id)],
            'context': {'create': False},
        }

    def action_view_successful(self):
        """View successful deployments"""
        self.ensure_one()
        return {
            'name': f'Successful: {self.name} {self.version}',
            'type': 'ir.actions.act_window',
            'res_model': 'asset.software.deployment',
            'view_mode': 'list,form',
            'domain': [('software_id', '=', self.id), ('status', '=', 'installed')],
            'context': {'create': False},
        }

    def action_view_failed(self):
        """View failed deployments"""
        self.ensure_one()
        return {
            'name': f'Failed: {self.name} {self.version}',
            'type': 'ir.actions.act_window',
            'res_model': 'asset.software.deployment',
            'view_mode': 'list,form',
            'domain': [('software_id', '=', self.id), ('status', '=', 'failed')],
            'context': {'create': False},
        }
