# -*- coding: utf-8 -*-
"""Installed application inventory.

Platform reality
----------------
* Android: needs QUERY_ALL_PACKAGES (Play-restricted) or Device Owner. Without
  either, the agent can still enumerate apps that declare a launcher activity,
  which covers essentially every user-installed app - just not background-only
  system packages.
* iOS: an ordinary app cannot enumerate installed apps at all. These records
  are only populated on iOS via real MDM (InstalledApplicationList query).
"""

from odoo import models, fields, api


class MobileApplication(models.Model):
    _name = "mobile.application"
    _description = "Installed Mobile Application"
    _order = "device_id, name"

    device_id = fields.Many2one(
        "mobile.device", required=True, ondelete="cascade", index=True)
    name = fields.Char(string="App Name", required=True, index=True)
    package_name = fields.Char(
        string="Package / Bundle ID", index=True,
        help="com.whatsapp on Android, net.whatsapp.WhatsApp on iOS")
    version_name = fields.Char(string="Version")
    version_code = fields.Char(string="Build")
    is_system = fields.Boolean(
        string="System App",
        help="Pre-installed by the OEM. Usually not removable.")
    installed_on = fields.Datetime()
    updated_on = fields.Datetime()
    size_mb = fields.Float(string="Size (MB)", digits=(10, 2))
    permissions = fields.Text(
        help="Granted runtime permissions, one per line, as reported by the agent.")

    platform = fields.Selection(related="device_id.platform", store=True, index=True)
    employee_id = fields.Many2one(related="device_id.employee_id", store=True)

    is_blocklisted = fields.Boolean(
        compute="_compute_is_blocklisted", store=True, index=True,
        string="Blocklisted")

    _device_package_uniq = models.Constraint(
        "UNIQUE(device_id, package_name)",
        "An app is listed once per device.")

    @api.depends("package_name")
    def _compute_is_blocklisted(self):
        blocked = self.env["ir.config_parameter"].sudo().get_param(
            "mobile_device_management.blocked_packages", default="")
        blocked_set = {p.strip() for p in blocked.split(",") if p.strip()}
        for rec in self:
            rec.is_blocklisted = bool(
                rec.package_name and rec.package_name in blocked_set)
