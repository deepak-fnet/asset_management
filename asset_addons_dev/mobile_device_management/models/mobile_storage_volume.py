# -*- coding: utf-8 -*-
"""Per-volume storage breakdown.

Android exposes each mounted volume through StorageManager: internal shared
storage, plus any SD card or USB OTG volume. Sizes come from StatFs per volume.
"""

from odoo import models, fields, api


class MobileStorageVolume(models.Model):
    _name = "mobile.storage.volume"
    _description = "Mobile Storage Volume"
    _order = "device_id, is_primary desc, name"

    device_id = fields.Many2one(
        "mobile.device", required=True, ondelete="cascade", index=True)
    name = fields.Char(required=True, help="Internal Storage, SD Card, ...")
    uuid = fields.Char(help="Volume UUID as reported by StorageManager")
    is_primary = fields.Boolean(string="Primary")
    is_removable = fields.Boolean(string="Removable")
    state = fields.Char(help="mounted, unmounted, ejecting, ...")

    total_gb = fields.Float(string="Total (GB)", digits=(10, 2))
    free_gb = fields.Float(string="Free (GB)", digits=(10, 2))
    used_gb = fields.Float(string="Used (GB)", digits=(10, 2),
                           compute="_compute_used", store=True)
    used_pct = fields.Float(string="Used %", compute="_compute_used", store=True)

    last_reported = fields.Datetime()

    _device_volume_uniq = models.Constraint(
        "UNIQUE(device_id, name)", "One row per volume per device.")

    @api.depends("total_gb", "free_gb")
    def _compute_used(self):
        for rec in self:
            rec.used_gb = max(rec.total_gb - rec.free_gb, 0.0)
            rec.used_pct = round(100.0 * rec.used_gb / rec.total_gb, 1) if rec.total_gb else 0.0
