from odoo import models, api, fields
from collections import defaultdict
from datetime import timedelta, datetime, time
import pytz


class AssetDashboard(models.Model):
    """
    Service model for Asset Management dashboard KPIs.
    Includes agent monitoring statistics and change alerts.
    """
    _name = "asset.dashboard"
    _description = "Asset Management Dashboard KPIs"
    _auto = False

    @api.model
    def get_camera_stats(self):
        """
        Return CCTV camera statistics including:
        - Total cameras
        - Online cameras
        - Offline cameras
        - Offline percentage
        """
        Camera = self.env['asset.camera'].sudo()
        total = Camera.search_count([])
        online = Camera.search_count(['|', '|', ('status', 'in', ['online', 'recording']), ('is_online', '=', True), ('is_recording', '=', True)])
        offline = Camera.search_count(['&', '&', ('status', 'not in', ['online', 'recording']), ('is_online', '=', False), ('is_recording', '=', False)])
        recording = Camera.search_count(['|', ('status', '=', 'recording'), ('is_recording', '=', True)])
        
        return {
            'total': total,
            'online': online,
            'offline': offline,
            'recording': recording,
            'offline_percentage': round((offline / total * 100), 2) if total > 0 else 0
        }

    @api.model
    def get_kpis(self):
        """
        Return comprehensive KPI values including:
        - State counts (total, assigned, maintenance, scrapped)
        - Agent status (active, offline, never synced)
        - Change alerts (assets with unreviewed changes)
        - Asset value trend (Jan-Dec)
        - Recent sync activity
        """

        Asset = self.env["asset.asset"].sudo()
        AgentLog = self.env["asset.agent.log"].sudo()

        # Get platform from context, default to 'windows' for backward compatibility
        os_platform = self._context.get('os_platform', 'windows')

        # Domain for specified platform - os_platform now has fallback to platform field
        domain = [("os_platform", "=", os_platform)]

        # ==================================================
        # STATE COUNTS
        # ==================================================

        # Odoo 19: read_group() was removed; _read_group() returns a list of
        # tuples (groupby values..., aggregate values...) instead of dicts.
        grouped_data = Asset._read_group(
            domain,
            groupby=["state"],
            aggregates=["__count"],
        )

        result = {
            "total": 0,
            "assigned": 0,
            "maintenance": 0,
            "scrapped": 0,
            "draft": 0,
            "asset_value_trend": [],
            "agent_stats": {
                "active": 0,
                "offline": 0,
                "never": 0,
            },
            "change_alerts": 0,
            "recent_syncs": 0,
            "critical_alerts": 0,
            "warning_alerts": 0,
            "info_alerts": 0,
            "no_warranty": 0,
            "overdue_maintenance": 0,
            "idle_30_days": 0,
            "not_synced_7_days": 0,
            "camera_stats": self.get_camera_stats(),
        }

        for state, count in grouped_data:

            result["total"] += count
            if state in result:
                result[state] = count

        # ==================================================
        # NEW ENHANCED KPIS
        # ==================================================

        today = fields.Date.today()
        now_utc = fields.Datetime.now()
        thirty_days_ago = now_utc - timedelta(days=30)
        seven_days_ago = now_utc - timedelta(days=7)

        result["no_warranty"] = Asset.search_count(domain + [("warranty_status", "=", "none")])
        result["overdue_maintenance"] = Asset.search_count(domain + [
            ("next_maintenance_date", "!=", False),
            ("next_maintenance_date", "<", today)
        ])
        result["idle_30_days"] = Asset.search_count(domain + [
            ("last_sync_time", "!=", False),
            ("last_sync_time", "<", thirty_days_ago)
        ])
        result["not_synced_7_days"] = Asset.search_count(domain + [
            ("last_sync_time", "<", seven_days_ago)
        ])

        # ==================================================
        # AGENT STATUS STATISTICS (last_sync_time BASED)
        # ==================================================

        # Get heartbeat timeout configuration
        heartbeat_timeout = int(
            self.env['ir.config_parameter'].sudo().get_param(
                'asset_management.agent_heartbeat_timeout',
                default='180'
            )
        )
        
        cutoff_time = now_utc - timedelta(seconds=heartbeat_timeout)
        
        # Count based on last_sync_time field
        online_count = Asset.search_count(domain + [
            ('last_sync_time', '!=', False),
            ('last_sync_time', '>=', cutoff_time)
        ])
        
        # All assets not online are considered offline (including never synced)
        offline_count = result["total"] - online_count
        
        never_synced_count = Asset.search_count(domain + [
            ('last_sync_time', '=', False)
        ])
        
        result["agent_stats"]["active"] = online_count
        result["agent_stats"]["offline"] = offline_count
        result["agent_stats"]["never"] = never_synced_count

        # ==================================================
        # CHANGE ALERTS
        # ==================================================

        result["change_alerts"] = Asset.search_count(domain + [("has_changes", "=", True)])

        severity_grouped = Asset._read_group(
            domain + [("has_changes", "=", True)],
            groupby=["alert_severity"],
            aggregates=["__count"],
        )

        for severity, count in severity_grouped:
            if severity == "critical":
                result["critical_alerts"] = count
            elif severity == "warning":
                result["warning_alerts"] = count
            elif severity == "info":
                result["info_alerts"] = count

        # ==================================================
        # RECENT SYNCS (since start of today)
        # ==================================================

        # Get start of today in user's timezone and convert to UTC for search
        now_utc = fields.Datetime.now()
        now_user = fields.Datetime.context_timestamp(self, now_utc)
        start_of_day_user = now_user.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_day_utc = start_of_day_user.astimezone(pytz.UTC).replace(tzinfo=None)

        result["recent_syncs"] = AgentLog.search_count([
            ("asset_id.os_platform", "=", os_platform),
            ("sync_time", ">=", start_of_day_utc),
            ("status", "=", "success")
        ])

        # ==================================================
        # ASSET VALUE OVER TIME (JAN-DEC)
        # ==================================================

        month_totals = defaultdict(float)

        assets = Asset.search(domain + [
            ("purchase_date", "!=", False),
            ("purchase_value", "!=", False),
        ])

        for asset in assets:
            try:
                purchase_date = fields.Date.from_string(asset.purchase_date)
                month_number = purchase_date.month
                month_totals[month_number] += asset.purchase_value
            except Exception:
                continue

        month_labels = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ]

        for index, label in enumerate(month_labels, start=1):
            result["asset_value_trend"].append({
                "label": label,
                "value": round(month_totals.get(index, 0.0), 2),
            })

        return result

    @api.model
    def get_change_alerts(self):
        """
        Return list of assets with unreviewed changes.
        Supports Windows, Linux, Mac platformsbased on context.
        """
        Asset = self.env["asset.asset"].sudo()
        
        # Get platform from context, default to 'windows'
        os_platform = self._context.get('os_platform', 'windows')

        assets = Asset.search([
            ("os_platform", "=", os_platform),
            ("has_changes", "=", True)
        ], order="last_change_date desc", limit=10)

        alerts = []
        for asset in assets:
            alerts.append({
                "id": asset.id,
                "asset_code": asset.asset_code,
                "asset_name": asset.asset_name,
                "serial_number": asset.serial_number,
                "change_date": fields.Datetime.to_string(asset.last_change_date) if asset.last_change_date else "",
                "change_summary": asset.change_summary or "",
                "severity": asset.alert_severity,
                "asset_type": "camera" if asset.is_camera or (asset.category_id and any(kw in asset.category_id.name.lower() for kw in ['camera', 'cctv', 'cam'])) else "laptop",
                "has_image": bool(asset.image_1920),
            })

        return alerts

    @api.model
    def get_recent_logs(self, limit=20):
        """
        Return recent agent sync logs.
        Supports Windows, Linux, Mac platforms based on context.
        """
        AgentLog = self.env["asset.agent.log"].sudo()
        
        # Get platform from context, default to 'windows'
        os_platform = self._context.get('os_platform', 'windows')

        logs = AgentLog.search([
            ("asset_id.os_platform", "=", os_platform)
        ], order="sync_time desc", limit=limit)

        log_list = []
        for log in logs:
            log_list.append({
                "id": log.id,
                "asset_id": log.asset_id.id,
                "asset_name": log.asset_name,
                "serial_number": log.serial_number,
                "sync_time": fields.Datetime.to_string(log.sync_time) if log.sync_time else "",
                "log_type": log.log_type,
                "status": log.status,
                "changes_detected": log.changes_detected or "",
                "asset_type": "camera" if log.asset_id.is_camera or (log.asset_id.category_id and any(kw in log.asset_id.category_id.name.lower() for kw in ['camera', 'cctv', 'cam'])) else "laptop",
                "has_image": bool(log.asset_id.image_1920),
            })

        return log_list

    @api.model
    def get_recent_cctv_events(self):
        """
        Return recent CCTV events.
        Currently returns mock data to match UI requirements.
        """
        now = datetime.now()
        
        # Mock events to match the requested design
        events = [
            {
                "id": 1,
                "message": "Motion Detected - Lobby Camera",
                "time": (now - timedelta(minutes=5)).strftime("%d-%m-%Y %I:%M:%S %p"),
                "status": "success",
                "icon": "fa-check-circle",
            },
            {
                "id": 2,
                "message": "Motion Detected - Lobby Camera",
                "time": (now - timedelta(minutes=12)).strftime("%d-%m-%Y %I:%M:%S %p"),
                "status": "success",
                "icon": "fa-check-circle",
            },
            {
                "id": 3,
                "message": "Camera Offline - Warehouse",
                "time": (now - timedelta(hours=2)).strftime("%d-%m-%Y %I:%M:%S %p"),
                "status": "danger",
                "icon": "fa-times-circle",
            },
            {
                "id": 4,
                "message": "Camera Offline - Warehouse",
                "time": (now - timedelta(hours=2, minutes=5)).strftime("%d-%m-%Y %I:%M:%S %p"),
                "status": "danger",
                "icon": "fa-times-circle",
            },
        ]
        return events

    @api.model
    def get_ubuntu_dashboard_data(self):
        """
        Return statistics for Ubuntu/Linux agents only.
        """
        Asset = self.env["asset.asset"].sudo()
        AgentLog = self.env["asset.agent.log"].sudo()
        
        # Domain for Linux - os_platform now has fallback to platform field
        domain = [("os_platform", "=", "linux")]

        # State counts
        # Odoo 19: read_group() was removed; _read_group() returns a list of
        # tuples (groupby values..., aggregate values...) instead of dicts.
        grouped_data = Asset._read_group(
            domain,
            groupby=["state"],
            aggregates=["__count"],
        )

        result = {
            "total": 0,
            "assigned": 0,
            "maintenance": 0,
            "scrapped": 0,
            "draft": 0,
            "agent_stats": {
                "active": 0,
                "offline": 0,
                "never": 0,
            },
            "change_alerts": 0,
            "recent_syncs_count": 0,
            "critical_alerts": 0,
            "warning_alerts": 0,
            "info_alerts": 0,
            "no_warranty": 0,
            "overdue_maintenance": 0,
            "idle_30_days": 0,
            "not_synced_7_days": 0,
            "recent_syncs": [],
            "change_alerts_list": [],
        }

        for state, count in grouped_data:
            result["total"] += count
            if state in result:
                result[state] = count

        # Agent status
        heartbeat_timeout = int(self.env["ir.config_parameter"].sudo().get_param("asset_management.agent_heartbeat_timeout", default="180"))
        cutoff_time = fields.Datetime.now() - timedelta(seconds=heartbeat_timeout)
        
        active_count = Asset.search_count(domain + [("last_sync_time", "!=", False), ("last_sync_time", ">=", cutoff_time)])
        result["agent_stats"]["active"] = active_count
        result["agent_stats"]["offline"] = result["total"] - active_count
        result["agent_stats"]["never"] = Asset.search_count(domain + [("last_sync_time", "=", False)])

        # Change Alerts Count
        result["change_alerts"] = Asset.search_count(domain + [("has_changes", "=", True)])
        severity_grouped = Asset._read_group(
            domain + [("has_changes", "=", True)],
            groupby=["alert_severity"],
            aggregates=["__count"],
        )
        for severity, count in severity_grouped:
            if severity == "critical": result["critical_alerts"] = count
            elif severity == "warning": result["warning_alerts"] = count
            elif severity == "info": result["info_alerts"] = count

        # Risk & Compliance KPIs
        today = fields.Date.today()
        now_utc = fields.Datetime.now()
        thirty_days_ago = now_utc - timedelta(days=30)
        seven_days_ago = now_utc - timedelta(days=7)

        result["no_warranty"] = Asset.search_count(domain + [("warranty_status", "=", "none")])
        result["overdue_maintenance"] = Asset.search_count(domain + [
            ("next_maintenance_date", "!=", False),
            ("next_maintenance_date", "<", today)
        ])
        result["idle_30_days"] = Asset.search_count(domain + [
            ("last_sync_time", "!=", False),
            ("last_sync_time", "<", thirty_days_ago)
        ])
        result["not_synced_7_days"] = Asset.search_count(domain + [
            ("last_sync_time", "<", seven_days_ago)
        ])

        # Recent Syncs (last 5)
        logs = AgentLog.search([("asset_id.os_platform", "=", "linux")], order="sync_time desc", limit=5)
        for log in logs:
            result["recent_syncs"].append({
                "id": log.id,
                "asset_id": log.asset_id.id,
                "asset_name": log.asset_name,
                "serial_number": log.serial_number,
                "sync_time": fields.Datetime.to_string(log.sync_time) if log.sync_time else "",
                "status": log.status,
            })

        # Change Alerts List (last 5)
        assets = Asset.search(domain + [("has_changes", "=", True)], order="last_change_date desc", limit=5)
        for asset in assets:
            result["change_alerts_list"].append({
                "id": asset.id,
                "asset_name": asset.asset_name,
                "serial_number": asset.serial_number or asset.tag_number or "",
                "has_image": bool(asset.image_1920),
                "res_model": "asset.asset",
                "change_summary": asset.change_summary or "",
                "severity": asset.alert_severity,
                "change_date": fields.Datetime.to_string(asset.last_change_date) if asset.last_change_date else "",
            })

        return result

    @api.model
    def get_system_overview_data(self):
        """
        Return summary data for the System Overview (Home) page.
        """
        Asset = self.env["asset.asset"].sudo()
        Camera = self.env["asset.camera"].sudo()
        NetworkDevice = self.env["asset.network.device"].sudo()
        AgentLog = self.env["asset.agent.log"].sudo()

        # Heartbeat timeout for online/offline status
        heartbeat_timeout = int(
            self.env['ir.config_parameter'].sudo().get_param(
                'asset_management.agent_heartbeat_timeout',
                default='180'
            )
        )
        cutoff_time = fields.Datetime.now() - timedelta(seconds=heartbeat_timeout)

        # KPI Counts
        # CCTV Cameras - query directly from specialized model
        camera_count = Camera.search_count([])
        camera_online = Camera.search_count(['|', '|', ("status", "in", ["online", "recording"]), ("is_online", "=", True), ("is_recording", "=", True)])
        camera_offline = Camera.search_count(['&', '&', ("status", "not in", ["online", "recording"]), ("is_online", "=", False), ("is_recording", "=", False)])
        
        # Network Devices - query directly from specialized model
        network_count = NetworkDevice.search_count([])
        network_online = NetworkDevice.search_count([("connection_status", "=", "online")])
        network_offline = NetworkDevice.search_count([("connection_status", "!=", "online")])

        # Identify asset IDs that are cameras or network devices to avoid double counting
        camera_asset_ids = Camera.search([("asset_id", "!=", False)]).mapped("asset_id").ids
        network_asset_ids = NetworkDevice.search([("asset_id", "!=", False)]).mapped("asset_id").ids
        specialized_asset_ids = list(set(camera_asset_ids + network_asset_ids))

        # Laptop/Desktop/Server Assets (Exclude specialized devices)
        base_asset_domain = [("id", "not in", specialized_asset_ids)]

        # Count assets by platform - os_platform is now computed with fallback to platform field
        windows_count = Asset.search_count(base_asset_domain + [("os_platform", "=", "windows")])
        linux_count = Asset.search_count(base_asset_domain + [("os_platform", "=", "linux")])
        mac_count = Asset.search_count(base_asset_domain + [("os_platform", "=", "macos")])
        other_assets_count = Asset.search_count(base_asset_domain + [("os_platform", "not in", ["windows", "linux", "macos"])])

        # Windows Online/Offline
        windows_online = Asset.search_count(base_asset_domain + [
            ("os_platform", "=", "windows"),
            ("last_sync_time", "!=", False),
            ("last_sync_time", ">=", cutoff_time)
        ])
        windows_offline = windows_count - windows_online

        # Linux Online/Offline
        linux_online = Asset.search_count(base_asset_domain + [
            ("os_platform", "=", "linux"),
            ("last_sync_time", "!=", False),
            ("last_sync_time", ">=", cutoff_time)
        ])
        linux_offline = linux_count - linux_online

        # Mac Online/Offline
        mac_online = Asset.search_count(base_asset_domain + [
            ("os_platform", "=", "macos"),
            ("last_sync_time", "!=", False),
            ("last_sync_time", ">=", cutoff_time)
        ])
        mac_offline = mac_count - mac_online

        # Other Assets Online/Offline (Unknown platforms)
        other_online = Asset.search_count(base_asset_domain + [
            ("os_platform", "not in", ["windows", "linux", "macos"]),
            ("last_sync_time", "!=", False),
            ("last_sync_time", ">=", cutoff_time)
        ])
        other_offline = other_assets_count - other_online

        # Aggregated Totals (Excluding "Other" unknown platforms for UI consistency)
        total_assets = windows_count + linux_count + mac_count + camera_count + network_count
        online_agents = windows_online + linux_online + mac_online + camera_online + network_online
        offline_agents = windows_offline + linux_offline + mac_offline + camera_offline + network_offline
        
        critical_alerts_count = Asset.search_count([
            ("has_changes", "=", True),
            ("alert_severity", "=", "critical")
        ])

        warning_alerts_count = Asset.search_count([
            ("has_changes", "=", True),
            ("alert_severity", "=", "warning")
        ])

        info_alerts_count = Asset.search_count([
            ("has_changes", "=", True),
            ("alert_severity", "=", "info")
        ])

        # Total alerts count (all severities across all platforms)
        total_alerts_count = critical_alerts_count + warning_alerts_count + info_alerts_count

        # Platform-specific alerts counts - os_platform now has fallback to platform field
        windows_alerts_count = Asset.search_count(base_asset_domain + [
            ("os_platform", "=", "windows"),
            ("has_changes", "=", True)
        ])

        linux_alerts_count = Asset.search_count(base_asset_domain + [
            ("os_platform", "=", "linux"),
            ("has_changes", "=", True)
        ])

        mac_alerts_count = Asset.search_count(base_asset_domain + [
            ("os_platform", "=", "macos"),
            ("has_changes", "=", True)
        ])

        # CCTV alerts - from camera model
        cctv_alerts_count = Camera.search_count([
            ("has_changes", "=", True)
        ]) if hasattr(Camera, 'has_changes') else 0

        # Network alerts - from network device model
        network_alerts_count = NetworkDevice.search_count([
            ("has_changes", "=", True)
        ]) if hasattr(NetworkDevice, 'has_changes') else 0

        # System Health Status
        health_status = "healthy"
        if critical_alerts_count > 0:
            health_status = "critical"
        elif offline_agents > 0:
            health_status = "warning"

        # Recent Activity (Last 10)
        recent_logs = AgentLog.search([], order="sync_time desc", limit=10)
        recent_activity = []
        for log in recent_logs:
            recent_activity.append({
                "id": log.id,
                "asset_id": log.asset_id.id,
                "asset_name": log.asset_name,
                "time": fields.Datetime.to_string(log.sync_time) if log.sync_time else "",
                "type": log.log_type,
                "status": log.status,
                "description": log.changes_detected or f"Agent sync {log.status}",
            })

        # All Alerts Snapshot (Last 10 - All severities across all platforms)
        all_alert_assets = Asset.search([
            ("has_changes", "=", True),
        ], order="last_change_date desc", limit=10)

        all_alerts = []
        for asset in all_alert_assets:
            all_alerts.append({
                "id": asset.id,
                "asset_name": asset.asset_name,
                "asset_code": asset.asset_code or f"AST-{asset.id}",
                "severity": asset.alert_severity or "info",
                "summary": asset.change_summary or "Unreviewed changes detected",
                "date": fields.Datetime.to_string(asset.last_change_date) if asset.last_change_date else "",
                "has_image": bool(asset.image_1920),
                "image_1920": asset.image_1920 if asset.image_1920 else False,
            })

        return {
            "total_assets": total_assets,
            "windows_count": windows_count,
            "windows_online": windows_online,
            "windows_offline": windows_offline,
            "linux_count": linux_count,
            "linux_online": linux_online,
            "linux_offline": linux_offline,
            "mac_count": mac_count,
            "mac_online": mac_online,
            "mac_offline": mac_offline,
            "camera_count": camera_count,
            "camera_online": camera_online,
            "camera_offline": camera_offline,
            "network_count": network_count,
            "network_online": network_online,
            "network_offline": network_offline,
            "online_agents": online_agents,
            "offline_agents": offline_agents,
            "critical_alerts_count": critical_alerts_count,
            "warning_alerts_count": warning_alerts_count,
            "info_alerts_count": info_alerts_count,
            "total_alerts_count": total_alerts_count,
            "windows_alerts_count": windows_alerts_count,
            "linux_alerts_count": linux_alerts_count,
            "mac_alerts_count": mac_alerts_count,
            "cctv_alerts_count": cctv_alerts_count,
            "network_alerts_count": network_alerts_count,
            "health_status": health_status,
            "recent_activity": recent_activity,
            "all_alerts": all_alerts,
        }
