# -*- coding: utf-8 -*-

from odoo import models, fields, api


class AllAssetsWizard(models.TransientModel):
    """
    Wizard to display all asset types and allow navigation to specific asset views.
    This is needed because CCTV cameras and Network devices are stored in separate models.
    """
    _name = 'asset.all.assets.wizard'
    _description = 'All Assets Selection Wizard'

    # Display counts (read-only, set from context)
    windows_count = fields.Integer(string="Windows Assets", readonly=True)
    linux_count = fields.Integer(string="Linux Assets", readonly=True)
    mac_count = fields.Integer(string="macOS Assets", readonly=True)
    cctv_count = fields.Integer(string="CCTV Cameras", readonly=True)
    network_count = fields.Integer(string="Network Devices", readonly=True)
    total_count = fields.Integer(string="Total Assets", readonly=True)

    def action_view_all_computers(self):
        """View all Windows and Linux assets (from asset.asset model)"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'All Computers (Windows & Linux)',
            'res_model': 'asset.asset',
            'view_mode': 'kanban,list,form',
            'domain': [],
            'context': {'is_all_assets_view': True},
            'target': 'current',
        }

    def action_view_windows(self):
        """View Windows assets only"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Windows Assets',
            'res_model': 'asset.asset',
            'view_mode': 'kanban,list,form',
            'domain': [('os_platform', '=', 'windows')],
            'context': {'default_os_platform': 'windows'},
            'target': 'current',
        }

    def action_view_linux(self):
        """View Linux assets only"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Linux Assets',
            'res_model': 'asset.asset',
            'view_mode': 'kanban,list,form',
            'domain': [('os_platform', '=', 'linux')],
            'context': {'default_os_platform': 'linux'},
            'target': 'current',
        }

    def action_view_mac(self):
        """View macOS assets only"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'macOS Assets',
            'res_model': 'asset.asset',
            'view_mode': 'kanban,list,form',
            'domain': [('os_platform', '=', 'macos')],
            'context': {'default_os_platform': 'macos'},
            'target': 'current',
        }

    def action_view_cctv(self):
        """View CCTV cameras"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'CCTV Cameras',
            'res_model': 'asset.camera',
            'view_mode': 'kanban,list,form',
            'domain': [],
            'target': 'current',
        }

    def action_view_network(self):
        """View Network devices"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Network Devices',
            'res_model': 'asset.network.device',
            'view_mode': 'kanban,list,form',
            'domain': [],
            'target': 'current',
        }
