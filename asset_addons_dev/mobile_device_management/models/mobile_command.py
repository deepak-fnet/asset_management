# -*- coding: utf-8 -*-
"""Commands queued for a device to collect on its next check-in.

Deliberately pull-based. Push (FCM/APNs) needs per-customer credentials and a
reachable server; polling works everywhere and degrades gracefully. The cost is
latency bounded by the heartbeat interval.

Scope note: 'wipe' and 'lock' only do anything on Android when the agent holds
Device Admin or Device Owner. On an unmanaged install they will be reported
back as unsupported rather than silently appearing to succeed.
"""

from odoo import models, fields, api


class MobileCommand(models.Model):
    _name = "mobile.command"
    _description = "Mobile Device Command"
    _order = "create_date desc"
    _rec_name = "command_type"

    device_id = fields.Many2one(
        "mobile.device", required=True, ondelete="cascade", index=True)
    command_type = fields.Selection(
        [("sync", "Force Sync"),
         ("locate", "Locate Now"),
         ("app_inventory", "Refresh App Inventory"),
         ("ring", "Ring Device"),
         ("lock", "Lock Device"),
         ("wipe", "Factory Reset")],
        required=True, default="sync")
    payload = fields.Text(help="Optional JSON passed through to the agent.")
    state = fields.Selection(
        [("pending", "Pending"),
         ("sent", "Sent"),
         ("done", "Completed"),
         ("failed", "Failed"),
         ("unsupported", "Not Supported by Device")],
        default="pending", required=True, index=True)
    result = fields.Text(readonly=True)
    sent_on = fields.Datetime(readonly=True)
    completed_on = fields.Datetime(readonly=True)
    requested_by = fields.Many2one(
        "res.users", default=lambda self: self.env.user, readonly=True)

    requires_privilege = fields.Boolean(
        compute="_compute_requires_privilege", store=True,
        help="True for commands that need Device Admin / Device Owner.")

    @api.depends("command_type")
    def _compute_requires_privilege(self):
        for rec in self:
            rec.requires_privilege = rec.command_type in ("lock", "wipe")
