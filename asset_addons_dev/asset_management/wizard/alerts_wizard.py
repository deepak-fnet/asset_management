# -*- coding: utf-8 -*-

from odoo import models, fields, api


class AlertsWizard(models.TransientModel):
    """
    Wizard to display all alerts across asset types and allow navigation to specific alert views.
    """
    _name = 'asset.alerts.wizard'
    _description = 'Alerts Selection Wizard'

    # Display counts (read-only, set from context)
    windows_alerts = fields.Integer(string="Windows Alerts", readonly=True)
    linux_alerts = fields.Integer(string="Linux Alerts", readonly=True)
    mac_alerts = fields.Integer(string="macOS Alerts", readonly=True)
    cctv_alerts = fields.Integer(string="CCTV Alerts", readonly=True)
    network_alerts = fields.Integer(string="Network Alerts", readonly=True)
    total_alerts = fields.Integer(string="Total Alerts", readonly=True)
    critical_alerts = fields.Integer(string="Critical Alerts", readonly=True)
    warning_alerts = fields.Integer(string="Warning Alerts", readonly=True)
    info_alerts = fields.Integer(string="Info Alerts", readonly=True)

    def action_view_windows_alerts(self):
        """View Windows alerts"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Windows Alerts',
            'res_model': 'asset.asset',
            'view_mode': 'kanban,list,form',
            'domain': [
                ('os_platform', '=', 'windows'),
                ('has_changes', '=', True)
            ],
            'target': 'current',
        }

    def action_view_linux_alerts(self):
        """View Linux alerts"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Linux Alerts',
            'res_model': 'asset.asset',
            'view_mode': 'kanban,list,form',
            'domain': [
                ('os_platform', '=', 'linux'),
                ('has_changes', '=', True)
            ],
            'target': 'current',
        }

    def action_view_mac_alerts(self):
        """View macOS alerts"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'macOS Alerts',
            'res_model': 'asset.asset',
            'view_mode': 'kanban,list,form',
            'domain': [
                ('os_platform', '=', 'macos'),
                ('has_changes', '=', True)
            ],
            'target': 'current',
        }

    def action_view_cctv_alerts(self):
        """View CCTV alerts"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'CCTV Alerts',
            'res_model': 'asset.camera',
            'view_mode': 'kanban,list,form',
            'domain': [('has_changes', '=', True)],
            'target': 'current',
        }

    def action_view_network_alerts(self):
        """View Network alerts"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Network Alerts',
            'res_model': 'asset.network.device',
            'view_mode': 'kanban,list,form',
            'domain': [('has_changes', '=', True)],
            'target': 'current',
        }

    def action_view_critical_alerts(self):
        """View all critical alerts"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Critical Alerts',
            'res_model': 'asset.asset',
            'view_mode': 'kanban,list,form',
            'domain': [
                ('has_changes', '=', True),
                ('alert_severity', '=', 'critical')
            ],
            'target': 'current',
        }

    def action_view_warning_alerts(self):
        """View all warning alerts"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Warning Alerts',
            'res_model': 'asset.asset',
            'view_mode': 'kanban,list,form',
            'domain': [
                ('has_changes', '=', True),
                ('alert_severity', '=', 'warning')
            ],
            'target': 'current',
        }

    def action_view_info_alerts(self):
        """View all info alerts"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Info Alerts',
            'res_model': 'asset.asset',
            'view_mode': 'kanban,list,form',
            'domain': [
                ('has_changes', '=', True),
                ('alert_severity', '=', 'info')
            ],
            'target': 'current',
        }
