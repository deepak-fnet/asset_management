# -*- coding: utf-8 -*-
"""Time-series of live device metrics.

One row per check-in. Kept so battery and memory can be charted over time
rather than only showing the latest value, which is what makes a degrading
battery or a memory-pressured handset visible.

Auto-purged by cron; this table grows fastest of anything in the module.
"""

import logging
from datetime import timedelta

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class MobileMetric(models.Model):
    _name = "mobile.metric"
    _description = "Mobile Device Metric Sample"
    _order = "recorded_at desc, id desc"
    _rec_name = "recorded_at"

    device_id = fields.Many2one(
        "mobile.device", required=True, ondelete="cascade", index=True)
    recorded_at = fields.Datetime(required=True, index=True,
                                  default=fields.Datetime.now)

    battery_level = fields.Integer(string="Battery %")
    battery_temp_c = fields.Float(string="Battery Temp (C)")
    is_charging = fields.Boolean()

    ram_total_gb = fields.Float(string="RAM Total (GB)", digits=(10, 2))
    ram_available_gb = fields.Float(string="RAM Free (GB)", digits=(10, 2))
    ram_used_pct = fields.Float(string="RAM Used %",
                                compute="_compute_ram_used", store=True)
    is_low_memory = fields.Boolean(string="Low Memory")

    storage_free_gb = fields.Float(string="Storage Free (GB)", digits=(10, 2))
    storage_used_pct = fields.Float(string="Storage Used %")

    @api.depends("ram_total_gb", "ram_available_gb")
    def _compute_ram_used(self):
        for rec in self:
            if rec.ram_total_gb:
                used = rec.ram_total_gb - rec.ram_available_gb
                rec.ram_used_pct = round(100.0 * used / rec.ram_total_gb, 1)
            else:
                rec.ram_used_pct = 0.0

    @api.model
    def _retention_days(self):
        return int(self.env["ir.config_parameter"].sudo().get_param(
            "mobile_device_management.metric_retention_days", default="30"))

    @api.model
    def cron_purge_old_metrics(self):
        days = self._retention_days()
        if days <= 0:
            return
        cutoff = fields.Datetime.now() - timedelta(days=days)
        stale = self.sudo().search([("recorded_at", "<", cutoff)])
        count = len(stale)
        stale.unlink()
        _logger.info("[MDM] Purged %s metric sample(s)", count)
