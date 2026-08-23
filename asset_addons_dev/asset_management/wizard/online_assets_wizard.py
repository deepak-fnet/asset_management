# -*- coding: utf-8 -*-

from datetime import timedelta
from odoo import models, fields, api



class OnlineAssetsWizard(models.TransientModel):
    """
    Wizard to display all online asset types and allow navigation to specific online asset views.
    """
    _name = 'asset.online.assets.wizard'
    _description = 'Online Assets Selection Wizard'

    # Display counts (read-only, set from context)
    windows_online = fields.Integer(string="Windows Online", readonly=True)
    linux_online = fields.Integer(string="Linux Online", readonly=True)
    mac_online = fields.Integer(string="macOS Online", readonly=True)
    cctv_online = fields.Integer(string="CCTV Online", readonly=True)
    network_online = fields.Integer(string="Network Online", readonly=True)
    total_online = fields.Integer(string="Total Online", readonly=True)

    def action_view_windows_online(self):
        """View online Windows assets"""
        heartbeat_timeout = int(self.env['ir.config_parameter'].sudo().get_param('asset_management.agent_heartbeat_timeout', default='180'))
        cutoff_time = fields.Datetime.now() - timedelta(seconds=heartbeat_timeout)
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Windows Assets (Online)',
            'res_model': 'asset.asset',
            'view_mode': 'kanban,list,form',
            'domain': [
                ('os_platform', '=', 'windows'),
                ('last_sync_time', '!=', False),
                ('last_sync_time', '>=', cutoff_time)
            ],
            'target': 'current',
        }

    def action_view_linux_online(self):
        """View online Linux assets"""
        heartbeat_timeout = int(self.env['ir.config_parameter'].sudo().get_param('asset_management.agent_heartbeat_timeout', default='180'))
        cutoff_time = fields.Datetime.now() - timedelta(seconds=heartbeat_timeout)
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'Linux Assets (Online)',
            'res_model': 'asset.asset',
            'view_mode': 'kanban,list,form',
            'domain': [
                ('os_platform', '=', 'linux'),
                ('last_sync_time', '!=', False),
                ('last_sync_time', '>=', cutoff_time)
            ],
            'target': 'current',
        }

    def action_view_mac_online(self):
        """View online macOS assets"""
        heartbeat_timeout = int(self.env['ir.config_parameter'].sudo().get_param('asset_management.agent_heartbeat_timeout', default='180'))
        cutoff_time = fields.Datetime.now() - timedelta(seconds=heartbeat_timeout)
        
        return {
            'type': 'ir.actions.act_window',
            'name': 'macOS Assets (Online)',
            'res_model': 'asset.asset',
            'view_mode': 'kanban,list,form',
            'domain': [
                ('os_platform', '=', 'macos'),
                ('last_sync_time', '!=', False),
                ('last_sync_time', '>=', cutoff_time)
            ],
            'target': 'current',
        }

    def action_view_cctv_online(self):
        """View online CCTV cameras"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'CCTV Cameras (Online)',
            'res_model': 'asset.camera',
            'view_mode': 'kanban,list,form',
            'domain': [('status', '=', 'online')],
            'target': 'current',
        }

    def action_view_network_online(self):
        """View online Network devices"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Network Devices (Online)',
            'res_model': 'asset.network.device',
            'view_mode': 'kanban,list,form',
            'domain': [('connection_status', '=', 'online')],
            'target': 'current',
        }
