from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)

class AssetStorageVolume(models.Model):
    _name = "asset.storage.volume"
    _description = "Asset Storage Volume"
    _order = "drive_letter"

    asset_id = fields.Many2one(
        "asset.asset",
        string="Asset",
        required=True,
        ondelete="cascade",
        index=True
    )
    drive_letter = fields.Char(
        string="Drive Letter",
        required=True,
        help="Drive letter (e.g., C:, D:)"
    )
    total_size = fields.Float(
        string="Total Size (GB)",
        digits=(10, 2),
        help="Total storage capacity"
    )
    free_space = fields.Float(
        string="Free Space (GB)",
        digits=(10, 2),
        help="Available free space"
    )
    used_space = fields.Float(
        string="Used Space (GB)",
        digits=(10, 2),
        help="Currently used space"
    )
    drive_label = fields.Selection(
        [
            ("system", "System"),
            ("recovery", "Recovery"),
            ("data", "Data"),
            ("root", "Root"),
            ("home", "Home"),
            ("boot", "Boot"),
            ("tmp", "Temporary"),
            ("var", "Variable"),
            ("unknown", "Unknown"),
        ],
        string="Drive Type",
        required=True,
        help="Type of drive partition (Windows or Linux)"
    )
    usage_percentage = fields.Float(
        string="Usage %",
        compute="_compute_usage_percentage",
        store=True,
        digits=(5, 2),
        help="Percentage of space used"
    )

    storage_status = fields.Selection(
        [
            ("healthy", "Healthy"),
            ("warning", "Low Space"),
            ("critical", "Full"),
        ],
        string="Status",
        compute="_compute_storage_status",
        store=True
    )

    @api.depends("usage_percentage")
    def _compute_storage_status(self):
        for volume in self:
            if volume.usage_percentage >= 95:
                volume.storage_status = "critical"
            elif volume.usage_percentage >= 85:
                volume.storage_status = "warning"
            else:
                volume.storage_status = "healthy"

    @api.depends("used_space", "total_size")
    def _compute_usage_percentage(self):
        for volume in self:
            if volume.total_size > 0:
                volume.usage_percentage = round((volume.used_space / volume.total_size) * 100, 2)
            else:
                volume.usage_percentage = 0.0

    @api.depends("drive_letter", "total_size", "drive_label")
    def _compute_display_name(self):
        # Odoo 19: name_get() was removed in favour of _compute_display_name().
        for volume in self:
            volume.display_name = (
                f"{volume.drive_letter} - {volume.total_size} GB ({volume.drive_label})"
            )
