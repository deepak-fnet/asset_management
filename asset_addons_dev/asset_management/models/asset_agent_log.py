from odoo import models, fields


class AssetAgentLog(models.Model):
    _name = "asset.agent.log"
    _description = "Asset Agent Sync Log"
    _order = "sync_time desc"

    asset_id = fields.Many2one(
        "asset.asset",
        string="Asset",
        required=True,
        ondelete="cascade",
        index=True
    )

    sync_time = fields.Datetime(
        string="Sync Time",
        default=fields.Datetime.now,
        required=True,
        index=True
    )

    log_type = fields.Selection(
        [
            ("created", "Asset Created"),
            ("updated", "Asset Updated"),
            ("no_change", "No Change"),
        ],
        string="Log Type",
        default="updated",
        required=True
    )

    snapshot_data = fields.Text(
        string="Snapshot Data",
        help="JSON snapshot of hardware/software state at sync time"
    )

    changes_detected = fields.Text(
        string="Changes Detected",
        help="List of changes detected in this sync"
    )

    ip_address = fields.Char(
        string="IP Address"
    )

    status = fields.Selection(
        [
            ("success", "Success"),
            ("failed", "Failed"),
        ],
        default="success",
        required=True
    )

    error_message = fields.Text(
        string="Error Message"
    )

    # Related fields for easy filtering
    serial_number = fields.Char(
        related="asset_id.serial_number",
        string="Serial Number",
        store=True,
        readonly=True
    )

    asset_name = fields.Char(
        related="asset_id.asset_name",
        string="Asset Name",
        store=True,
        readonly=True
    )