from odoo import models, fields, api
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)


class AssetLiveMonitoring(models.Model):
    _name = "asset.live.monitoring"
    _description = "Asset Live Monitoring (Cached Real-Time Metrics)"
    _rec_name = "asset_id"
    _order = "last_heartbeat desc"

    asset_id = fields.Many2one(
        "asset.asset",
        string="Asset",
        required=True,
        ondelete="cascade",
        index=True
    )

    serial_number = fields.Char(
        string="Serial Number",
        index=True,
        required=True
    )

    last_heartbeat = fields.Datetime(
        string="Last Heartbeat",
        default=fields.Datetime.now,
        required=True,
        index=True,
        help="Timestamp of last agent ping"
    )

    heartbeat = fields.Datetime(
        string="Heartbeat",
        default=fields.Datetime.now,
        store=True,
        index=True,
        help="Alias for last_heartbeat (required field)"
    )

    is_online = fields.Boolean(
        string="Online",
        compute="_compute_is_online",
        store=True,
        help="Computed from heartbeat timeout"
    )

    cpu_usage = fields.Float(
        string="CPU Usage (GB)",
        digits=(10, 2),
        store=True
    )

    cpu_usage_percent = fields.Float(
        string="CPU Usage (%)",
        digits=(5, 2),
        store=True
    )

    memory_usage = fields.Float(
        string="Memory Usage (GB)",
        digits=(10, 2),
        store=True
    )

    ram_usage_percent = fields.Float(
        string="RAM Usage (%)",
        digits=(5, 2),
        store=True
    )

    storage_usage = fields.Float(
        string="Storage Usage (GB)",
        digits=(10, 2),
        store=True
    )

    disk_usage_percent = fields.Float(
        string="Disk Usage (%)",
        digits=(5, 2),
        store=True
    )

    battery_level = fields.Float(
        string="Battery Level (%)",
        digits=(5, 2),
        store=True
    )

    battery_percentage = fields.Float(
        string="Battery Percentage (%)",
        digits=(5, 2),
        store=True
    )

    network_bandwidth = fields.Float(
        string="Network Bandwidth (Mbps)",
        digits=(10, 2),
        store=True
    )

    network_upload_mbps = fields.Float(
        string="Network Upload (Mbps)",
        digits=(10, 2),
        store=True
    )

    network_download_mbps = fields.Float(
        string="Network Download (Mbps)",
        digits=(10, 2),
        store=True
    )

    battery_health = fields.Float(
        string="Battery Health (%)",
        digits=(5, 2),
        store=True
    )

    ip_address = fields.Char(
        string="IP Address",
        store=True
    )

    # Ubuntu specific
    uptime = fields.Char(
        string="Uptime",
        help="System uptime for Linux agents"
    )

    # CCTV specific
    recording_status = fields.Selection([
        ('recording', 'Recording'),
        ('stopped', 'Stopped'),
        ('paused', 'Paused'),
        ('error', 'Error'),
        ('unknown', 'Unknown')
    ], string="Recording Status", default='unknown')

    motion_detected = fields.Boolean(
        string="Motion Detected",
        default=False
    )

    last_motion_time = fields.Datetime(
        string="Last Motion Detected"
    )

    # =====================
    # LOCATION (from device agent)
    # =====================
    latitude = fields.Float(
        string="Latitude",
        digits=(10, 6),
        store=True,
        help="Latitude coordinate from device"
    )
    longitude = fields.Float(
        string="Longitude",
        digits=(10, 6),
        store=True,
        help="Longitude coordinate from device"
    )
    location_source = fields.Selection([
        ('gps', 'GPS'),
        ('ip', 'IP-based'),
        ('unavailable', 'Unavailable'),
    ], string="Location Source", default='unavailable')

    # =====================
    # REVERSE GEOCODING FIELDS
    # =====================
    location_address = fields.Char(
        string="Location Address",
        help="Full address from reverse geocoding"
    )
    location_city = fields.Char(
        string="City",
        help="City/town from reverse geocoding"
    )
    location_state = fields.Char(
        string="State/Region",
        help="State or region from reverse geocoding"
    )
    location_country = fields.Char(
        string="Country",
        help="Country from reverse geocoding"
    )
    location_area = fields.Char(
        string="Area/Neighborhood",
        help="Suburb or area from reverse geocoding"
    )

    @api.depends("heartbeat", "last_heartbeat")
    def _compute_is_online(self):
        """
        Compute is_online status based on heartbeat timeout.
        Note: This is called automatically when heartbeat/last_heartbeat changes.
        For time-based updates, use cron_update_is_online() instead.
        """
        heartbeat_timeout = int(
            self.env['ir.config_parameter'].sudo().get_param(
                'asset_management.agent_heartbeat_timeout',
                default='180'
            )
        )
        now_utc = fields.Datetime.now()

        for record in self:
            heartbeat_time = record.heartbeat or record.last_heartbeat
            if not heartbeat_time:
                record.is_online = False
            else:
                time_diff = now_utc - heartbeat_time
                record.is_online = time_diff < timedelta(seconds=heartbeat_timeout)

    @api.model
    def update_live_metrics(self, serial_number, metrics):
        existing = self.search([("serial_number", "=", serial_number)], limit=1)

        now = fields.Datetime.now()
        values = {
            "serial_number": serial_number,
            "last_heartbeat": now,
            "heartbeat": now,
            "cpu_usage": metrics.get("cpu_usage", 0.0),
            "cpu_usage_percent": metrics.get("cpu_usage_percent", metrics.get("cpu_usage", 0.0)),
            "memory_usage": metrics.get("memory_usage", 0.0),
            "ram_usage_percent": metrics.get("ram_usage_percent", 0.0),
            "storage_usage": metrics.get("storage_usage", 0.0),
            "disk_usage_percent": metrics.get("disk_usage_percent", 0.0),
            "battery_level": metrics.get("battery_level", 0.0),
            "battery_percentage": metrics.get("battery_percentage", metrics.get("battery_level", 0.0)),
            "network_bandwidth": metrics.get("network_bandwidth", 0.0),
            "network_upload_mbps": metrics.get("network_upload_mbps", 0.0),
            "network_download_mbps": metrics.get("network_download_mbps", 0.0),
            "battery_health": metrics.get("battery_health", 100.0),
            "ip_address": metrics.get("ip_address"),
            "uptime": metrics.get("uptime"),
            "recording_status": metrics.get("recording_status", "unknown"),
            "motion_detected": metrics.get("motion_detected", False),
            "last_motion_time": metrics.get("last_motion_time"),
            # Location fields
            "latitude": metrics.get("latitude", 0.0),
            "longitude": metrics.get("longitude", 0.0),
            "location_source": metrics.get("location_source", "unavailable"),
            "location_address": metrics.get("location_address", ""),
            "location_city": metrics.get("location_city", ""),
            "location_state": metrics.get("location_state", ""),
            "location_country": metrics.get("location_country", ""),
            "location_area": metrics.get("location_area", ""),
        }

        if existing:
            # Throttle: a heartbeat that lands within the throttle window of
            # the last one is dropped rather than written.
            #
            # The logs showed the same row (id=6) being UPDATEd several times
            # per second by concurrent requests from one machine, which is what
            # produced "could not serialize access due to concurrent update".
            # Postgres cannot serialise concurrent writes to one row, and each
            # failure made Odoo retry the whole request - so the harder the
            # agents pushed, the worse it got.
            #
            # Live metrics are a dashboard readout. Storing every sample from
            # every agent gains nothing a 15-second resolution does not already
            # give, and costs a write conflict each time.
            throttle = int(self.env["ir.config_parameter"].sudo().get_param(
                "asset_management.live_metrics_throttle_seconds", default="15"))
            last = existing.last_heartbeat
            if throttle > 0 and last and (now - last).total_seconds() < throttle:
                return existing

            # Only write what actually changed. Writing an identical value
            # still dirties the row and still takes a lock.
            changed = {
                key: value for key, value in values.items()
                if existing[key] != value
            }
            if changed:
                existing.write(changed)
            return existing
        else:
            asset = self.env["asset.asset"].search([("serial_number", "=", serial_number)], limit=1)
            if asset:
                values["asset_id"] = asset.id
                record = self.create(values)
                return record
            else:
                _logger.warning(f"Asset not found for serial number: {serial_number}")
                return False

    @api.model
    def get_live_metrics(self, asset_id):
        record = self.search([("asset_id", "=", asset_id)], limit=1)
        if not record:
            return {
                "online": False,
                "last_heartbeat": False,
                "heartbeat": False,
                "cpu_usage": 0.0,
                "cpu_usage_percent": 0.0,
                "memory_usage": 0.0,
                "ram_usage_percent": 0.0,
                "storage_usage": 0.0,
                "disk_usage_percent": 0.0,
                "battery_level": 0.0,
                "battery_percentage": 0.0,
                "network_bandwidth": 0.0,
                "network_upload_mbps": 0.0,
                "network_download_mbps": 0.0,
                "battery_health": 0.0,
                "uptime": False,
                "recording_status": "unknown",
                "motion_detected": False,
                "last_motion_time": False,
            }

        return {
            "online": record.is_online,
            "last_heartbeat": record.last_heartbeat,
            "heartbeat": record.heartbeat,
            "cpu_usage": record.cpu_usage,
            "cpu_usage_percent": record.cpu_usage_percent,
            "memory_usage": record.memory_usage,
            "ram_usage_percent": record.ram_usage_percent,
            "storage_usage": record.storage_usage,
            "disk_usage_percent": record.disk_usage_percent,
            "battery_level": record.battery_level,
            "battery_percentage": record.battery_percentage,
            "network_bandwidth": record.network_bandwidth,
            "network_upload_mbps": record.network_upload_mbps,
            "network_download_mbps": record.network_download_mbps,
            "battery_health": record.battery_health,
            "ip_address": record.ip_address,
            "uptime": record.uptime,
            "recording_status": record.recording_status,
            "motion_detected": record.motion_detected,
            "last_motion_time": record.last_motion_time,
        }

    @api.model
    def get_all_live_metrics(self):
        records = self.search([])
        result = {}
        for record in records:
            result[record.asset_id.id] = {
                "online": record.is_online,
                "last_heartbeat": record.last_heartbeat.isoformat() if record.last_heartbeat else False,
                "heartbeat": record.heartbeat.isoformat() if record.heartbeat else False,
                "cpu_usage": record.cpu_usage,
                "cpu_usage_percent": record.cpu_usage_percent,
                "memory_usage": record.memory_usage,
                "ram_usage_percent": record.ram_usage_percent,
                "storage_usage": record.storage_usage,
                "disk_usage_percent": record.disk_usage_percent,
                "battery_level": record.battery_level,
                "battery_percentage": record.battery_percentage,
                "network_bandwidth": record.network_bandwidth,
                "network_upload_mbps": record.network_upload_mbps,
                "network_download_mbps": record.network_download_mbps,
                "battery_health": record.battery_health,
                "ip_address": record.ip_address,
                "uptime": record.uptime,
                "recording_status": record.recording_status,
                "motion_detected": record.motion_detected,
                "last_motion_time": record.last_motion_time.isoformat() if record.last_motion_time else False,
            }
        return result

    def write(self, vals):
        result = super().write(vals)
        # =====================================================================
        # FIX: Do NOT call asset._compute_live_metrics() here directly.
        #
        # The original code called asset._compute_live_metrics() inside write(),
        # which caused a circular ORM call:
        #   live_monitoring.write()
        #     → asset._compute_live_metrics()
        #       → LiveMonitoring.search(...)   ← ORM re-enters live_monitoring
        #         → transaction lock / recursion → HTTP 500
        #
        # Since cpu_usage, memory_usage, etc. on asset.asset are store=False
        # computed fields, Odoo recomputes them automatically on next read.
        # We only need to invalidate the cache so stale values are discarded.
        # =====================================================================
        for record in self:
            if record.asset_id:
                try:
                    record.asset_id.invalidate_recordset([
                        'cpu_usage',
                        'memory_usage',
                        'storage_usage',
                        'battery_level',
                        'network_bandwidth',
                        'battery_health',
                        'cpu_usage_percent',
                        'ram_usage_percent',
                        'disk_usage_percent',
                        'network_upload_mbps',
                        'network_download_mbps',
                        'battery_percentage',
                        'heartbeat',
                        'is_online',
                    ])
                except Exception as e:
                    _logger.warning(
                        f"[LiveMonitoring] Cache invalidation failed for asset "
                        f"{record.asset_id.id} (serial: {record.serial_number}): {e}"
                    )
        return result

    @api.model
    def cron_update_is_online(self):
        """
        Cron job to update is_online field for all monitoring records.
        Called every 5 minutes to ensure status is current.

        Since is_online depends on current time (not just field values),
        we need to explicitly recompute it.
        """
        records = self.search([])
        heartbeat_timeout = int(
            self.env['ir.config_parameter'].sudo().get_param(
                'asset_management.agent_heartbeat_timeout',
                default='180'
            )
        )
        now_utc = fields.Datetime.now()

        for record in records:
            heartbeat_time = record.heartbeat or record.last_heartbeat
            if not heartbeat_time:
                record.is_online = False
            else:
                time_diff = now_utc - heartbeat_time
                record.is_online = time_diff < timedelta(seconds=heartbeat_timeout)

        _logger.info(f"[LiveMonitoring] Updated is_online status for {len(records)} records")
        return len(records)

    @api.model
    def cleanup_stale_records(self, days=30):
        cutoff = fields.Datetime.now() - timedelta(days=days)
        stale = self.search([("last_heartbeat", "<", cutoff)])
        count = len(stale)
        stale.unlink()
        _logger.info(f"Cleaned up {count} stale live monitoring records")
        return count