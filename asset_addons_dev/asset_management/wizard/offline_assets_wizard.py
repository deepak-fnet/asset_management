# -*- coding: utf-8 -*-

from datetime import timedelta
from odoo import models, fields, api



class OfflineAssetsWizard(models.TransientModel):
    """
    Wizard to display all offline asset types and allow navigation to specific offline asset views.
    """
    _name = 'asset.offline.assets.wizard'
    _description = 'Offline Assets Selection Wizard'

    # Display counts (read-only, set from context)
    windows_offline = fields.Integer(string="Windows Offline", readonly=True)
    linux_offline = fields.Integer(string="Linux Offline", readonly=True)
    mac_offline = fields.Integer(string="macOS Offline", readonly=True)
    cctv_offline = fields.Integer(string="CCTV Offline", readonly=True)
    network_offline = fields.Integer(string="Network Offline", readonly=True)
    total_offline = fields.Integer(string="Total Offline", readonly=True)

    def action_view_windows_offline(self):
        """View offline Windows assets"""
        heartbeat_timeout = int(self.env['ir.config_parameter'].sudo().get_param('asset_management.agent_heartbeat_timeout', default='180'))
        cutoff_time = fields.Datetime.now() - timedelta(seconds=heartbeat_timeout)
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Windows Assets (Offline)',
            'res_model': 'asset.asset',
            'view_mode': 'kanban,list,form',
            'domain': [
                ('os_platform', '=', 'windows'),
                '|',
                ('last_sync_time', '=', False),
                ('last_sync_time', '<', cutoff_time)
            ],
            'target': 'current',
        }

    def action_view_linux_offline(self):
        """View offline Linux assets"""
        heartbeat_timeout = int(self.env['ir.config_parameter'].sudo().get_param('asset_management.agent_heartbeat_timeout', default='180'))
        cutoff_time = fields.Datetime.now() - timedelta(seconds=heartbeat_timeout)
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Linux Assets (Offline)',
            'res_model': 'asset.asset',
            'view_mode': 'kanban,list,form',
            'domain': [
                ('os_platform', '=', 'linux'),
                '|',
                ('last_sync_time', '=', False),
                ('last_sync_time', '<', cutoff_time)
            ],
            'target': 'current',
        }

    def action_view_mac_offline(self):
        """View offline macOS assets"""
        heartbeat_timeout = int(self.env['ir.config_parameter'].sudo().get_param('asset_management.agent_heartbeat_timeout', default='180'))
        cutoff_time = fields.Datetime.now() - timedelta(seconds=heartbeat_timeout)
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'macOS Assets (Offline)',
            'res_model': 'asset.asset',
            'view_mode': 'kanban,list,form',
            'domain': [
                ('os_platform', '=', 'macos'),
                '|',
                ('last_sync_time', '=', False),
                ('last_sync_time', '<', cutoff_time)
            ],
            'target': 'current',
        }

    def action_view_cctv_offline(self):
        """View offline CCTV cameras"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'CCTV Cameras (Offline)',
            'res_model': 'asset.camera',
            'view_mode': 'kanban,list,form',
            'domain': [('status', '!=', 'online')],
            'target': 'current',
        }

    def action_view_network_offline(self):
        """View offline Network devices"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Network Devices (Offline)',
            'res_model': 'asset.network.device',
            'view_mode': 'kanban,list,form',
            'domain': [('connection_status', '!=', 'online')],
            'target': 'current',
        }
