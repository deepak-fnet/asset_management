# -*- coding: utf-8 -*-
"""OS update records surfaced per device.

One row per (device, available version). Created by the server when the
catalogue says a device is behind - not by the agent, which cannot see this.
"""

from odoo import models, fields, api


class MobileOsUpdate(models.Model):
    _name = "mobile.os.update"
    _description = "Mobile OS Update"
    _order = "detected_on desc, id desc"

    device_id = fields.Many2one(
        "mobile.device", required=True, ondelete="cascade", index=True)
    platform = fields.Selection(related="device_id.platform", store=True, index=True)
    current_version = fields.Char(required=True)
    available_version = fields.Char(required=True)
    severity = fields.Selection(
        [("minor", "Minor"), ("major", "Major"), ("security", "Security"),
         ("eol", "End of Life")],
        default="minor", index=True)
    detail = fields.Char()
    detected_on = fields.Datetime(default=fields.Datetime.now, index=True)
    state = fields.Selection(
        [("available", "Available"),
         ("notified", "User Notified"),
         ("installed", "Installed"),
         ("dismissed", "Dismissed")],
        default="available", index=True, required=True)
    resolved_on = fields.Datetime(readonly=True)

    employee_id = fields.Many2one(related="device_id.employee_id", store=True)

    _device_version_uniq = models.Constraint(
        "UNIQUE(device_id, available_version)",
        "That update is already recorded for this device.")

    def action_mark_notified(self):
        return self.write({"state": "notified"})

    def action_mark_installed(self):
        return self.write({"state": "installed",
                           "resolved_on": fields.Datetime.now()})

    def action_dismiss(self):
        return self.write({"state": "dismissed",
                           "resolved_on": fields.Datetime.now()})
