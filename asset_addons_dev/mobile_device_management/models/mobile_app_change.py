# -*- coding: utf-8 -*-
"""Detected changes to the installed app set.

The agent cannot watch for installs in the background reliably (broadcast
receivers for PACKAGE_ADDED are restricted and OEM ROMs kill listeners), so
changes are derived server-side by diffing each inventory report against the
previous one. That means detection latency equals the inventory interval,
which is fine for an audit trail.
"""

from odoo import models, fields


class MobileAppChange(models.Model):
    _name = "mobile.app.change"
    _description = "Mobile App Change"
    _order = "detected_on desc, id desc"

    device_id = fields.Many2one(
        "mobile.device", required=True, ondelete="cascade", index=True)
    change_type = fields.Selection(
        [("installed", "Installed"),
         ("removed", "Removed"),
         ("updated", "Updated")],
        required=True, index=True)
    app_name = fields.Char(required=True)
    package_name = fields.Char(required=True, index=True)
    old_version = fields.Char()
    new_version = fields.Char()
    detected_on = fields.Datetime(default=fields.Datetime.now, index=True)
    is_blocklisted = fields.Boolean(index=True,
                                    help="Package was on the blocklist when detected.")

    employee_id = fields.Many2one(related="device_id.employee_id", store=True)
    platform = fields.Selection(related="device_id.platform", store=True)
