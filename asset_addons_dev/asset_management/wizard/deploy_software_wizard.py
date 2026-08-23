# -*- coding: utf-8 -*-
"""
Deploy Software Wizard

Wizard for deploying software to multiple devices at once.
Supports selected devices, all online devices, or by department.
"""

from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class DeploySoftwareWizard(models.TransientModel):
    """
    Deploy Software to Devices Wizard

    Provides a user-friendly interface for deploying software
    to multiple devices with various selection options.
    """
    _name = 'deploy.software.wizard'
    _description = 'Deploy Software to Devices'

    # =========================================================================
    # SOFTWARE AND DEVICE SELECTION
    # =========================================================================
    software_ids = fields.Many2many(
        'asset.software.catalog',
        string='Software to Deploy',
        required=True,
        help='Select one or more software packages to deploy'
    )
    device_ids = fields.Many2many(
        'asset.agent',
        string='Target Devices',
        help='Select target devices for deployment',
        domain="[('platform', '=', 'windows')]"
    )

    # =========================================================================
    # DEPLOYMENT SCOPE - VISUAL RADIO OPTIONS
    # =========================================================================
    deployment_scope = fields.Selection([
        ('selected', '📋 Selected Devices (Choose specific devices)'),
        ('all', '🌐 All Devices (Deploy to all registered devices)'),
        ('department', '🏢 By Department (Deploy to specific department)')
    ], string='Deployment Target', default='selected', required=True)

    department_id = fields.Many2one(
        'hr.department',
        string='Department',
        help='Select department to deploy to all devices in that department'
    )

    # =========================================================================
    # OPTIONS
    # =========================================================================
    skip_installed = fields.Boolean(
        string='Skip Already Installed',
        default=True,
        help='Skip devices that already have this software installed'
    )

    # =========================================================================
    # SUMMARY FIELDS (COMPUTED)
    # =========================================================================
    device_count = fields.Integer(
        string='Device Count',
        compute='_compute_device_count',
        help='Number of devices that will receive the deployment'
    )
    software_count = fields.Integer(
        string='Software Selected',
        compute='_compute_software_count',
        help='Number of software packages to deploy'
    )
    total_device_count = fields.Integer(
        string='Total Devices',
        compute='_compute_total_device_count',
        help='Total number of Windows devices in the system'
    )

    # =========================================================================
    # COMPUTE METHODS
    # =========================================================================
    @api.depends('device_ids', 'deployment_scope', 'department_id')
    def _compute_device_count(self):
        """Compute number of selected devices based on scope"""
        for wizard in self:
            if wizard.deployment_scope == 'selected':
                wizard.device_count = len(wizard.device_ids)
            elif wizard.deployment_scope == 'all':
                wizard.device_count = wizard.total_device_count
            elif wizard.deployment_scope == 'department' and wizard.department_id:
                wizard.device_count = wizard.env['asset.agent'].search_count([
                    ('platform', '=', 'windows'),
                    ('department_id', '=', wizard.department_id.id)
                ])
            else:
                wizard.device_count = 0

    @api.depends('software_ids')
    def _compute_software_count(self):
        """Compute number of selected software packages"""
        for wizard in self:
            wizard.software_count = len(wizard.software_ids)

    @api.depends('deployment_scope')
    def _compute_total_device_count(self):
        """Compute total number of Windows devices"""
        for wizard in self:
            wizard.total_device_count = wizard.env['asset.agent'].search_count([
                ('platform', '=', 'windows')
            ])

    # =========================================================================
    # ONCHANGE METHODS
    # =========================================================================
    @api.onchange('deployment_scope')
    def _onchange_deployment_scope(self):
        """Auto-populate devices based on scope"""
        if self.deployment_scope == 'all':
            self.device_ids = self.env['asset.agent'].search([
                ('platform', '=', 'windows')
            ])
        elif self.deployment_scope == 'department' and self.department_id:
            self.device_ids = self.env['asset.agent'].search([
                ('platform', '=', 'windows'),
                ('department_id', '=', self.department_id.id)
            ])
        elif self.deployment_scope == 'selected':
            self.device_ids = False

    @api.onchange('department_id')
    def _onchange_department_id(self):
        """Update device selection when department changes"""
        if self.deployment_scope == 'department' and self.department_id:
            self.device_ids = self.env['asset.agent'].search([
                ('platform', '=', 'windows'),
                ('department_id', '=', self.department_id.id)
            ])

    # =========================================================================
    # DEPLOYMENT ACTION
    # =========================================================================
    def action_deploy(self):
        """Create deployment records for each device-software combination"""
        self.ensure_one()
        
        # Validation
        if not self.software_ids:
            raise UserError(_('Please select at least one software package to deploy.'))
        
        # Determine target devices based on scope
        if self.deployment_scope == 'selected':
            if not self.device_ids:
                raise UserError(_('Please select at least one device for deployment.'))
            target_devices = self.device_ids
        elif self.deployment_scope == 'all':
            target_devices = self.env['asset.agent'].search([
                ('platform', '=', 'windows')
            ])
            if not target_devices:
                raise UserError(_('No Windows devices found in the system.'))
        elif self.deployment_scope == 'department':
            if not self.department_id:
                raise UserError(_('Please select a department for deployment.'))
            target_devices = self.env['asset.agent'].search([
                ('platform', '=', 'windows'),
                ('department_id', '=', self.department_id.id)
            ])
            if not target_devices:
                raise UserError(_('No Windows devices found in the selected department.'))
        else:
            target_devices = self.env['asset.agent']

        # Create deployments for each software x device combination
        Deployment = self.env['asset.software.deployment']
        deployments_created = 0
        deployments_skipped = 0

        for software in self.software_ids:
            for device in target_devices:
                # Check if already deployed (if skip_installed is enabled)
                if self.skip_installed:
                    existing = Deployment.search([
                        ('device_id', '=', device.id),
                        ('software_id', '=', software.id),
                        ('status', '=', 'installed')
                    ], limit=1)
                    if existing:
                        _logger.info(
                            f"Skipping {software.name} on {device.hostname} - "
                            f"already installed"
                        )
                        deployments_skipped += 1
                        continue

                # Create deployment record
                try:
                    Deployment.create({
                        'device_id': device.id,
                        'software_id': software.id,
                        'status': 'pending',
                        'deployed_by': self.env.user.id,
                        'deployed_date': fields.Datetime.now(),
                    })
                    deployments_created += 1
                except Exception as e:
                    _logger.error(
                        f"Failed to create deployment for {software.name} "
                        f"on {device.hostname}: {e}"
                    )

        # Build notification message
        message_parts = [f"✅ Created {deployments_created} deployment task(s)"]
        if deployments_skipped > 0:
            message_parts.append(f"⏭️ Skipped {deployments_skipped} (already installed)")
        message_parts.append(f"for {len(target_devices)} device(s)")
        
        message = ' '.join(message_parts)

        # Show success notification
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': '🚀 Deployment Started',
                'message': message,
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'}
            }
        }
