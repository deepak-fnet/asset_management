# -*- coding: utf-8 -*-
"""The enrolled phone or tablet.

Mirrors asset.agent: a device enrols once, gets a token, and heartbeats. The
difference is that a mobile OS will not tell an ordinary app whether an update
exists, so `os_update_state` is derived server-side by comparing the reported
version and security patch level against mobile.os.release.
"""

import logging
import secrets
import uuid
from datetime import timedelta

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Seconds without a check-in before a device counts as offline. Mobile devices
# sleep aggressively, so this is much longer than the desktop agent's 180s.
DEFAULT_HEARTBEAT_TIMEOUT = 3600


class MobileDevice(models.Model):
    _name = "mobile.device"
    _description = "Managed Mobile Device"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "last_seen desc, id desc"
    _rec_name = "display_label"

    # ── Identity ──────────────────────────────────────────────────────────
    name = fields.Char(
        string="Device Name", required=True, tracking=True,
        help="User-visible name, e.g. 'Priya - Galaxy S23'")
    device_uid = fields.Char(
        string="Device UID", readonly=True, copy=False, index=True,
        help="Server-issued identifier the agent sends on every call")
    token = fields.Char(
        string="Auth Token", readonly=True, copy=False, groups="base.group_system",
        help="Per-device secret. Revoking it blocks the device immediately.")
    display_label = fields.Char(
        compute="_compute_display_label", store=True, string="Device")

    active = fields.Boolean(default=True)

    # ── Hardware ──────────────────────────────────────────────────────────
    platform = fields.Selection(
        [("android", "Android"), ("ios", "iOS")],
        required=True, default="android", tracking=True, index=True)
    manufacturer = fields.Char(tracking=True, help="vivo, HONOR, Samsung, Apple, ...")
    model_name = fields.Char(string="Model", tracking=True)
    serial_number = fields.Char(tracking=True, index=True)
    imei = fields.Char(
        string="IMEI", tracking=True,
        help="Only populated where the platform and enrolment mode permit it. "
             "Android 10+ blocks this for non Device Owner apps.")
    total_storage_gb = fields.Float(string="Storage (GB)", digits=(10, 2))
    free_storage_gb = fields.Float(string="Free Storage (GB)", digits=(10, 2))
    storage_used_pct = fields.Float(
        string="Storage Used %", compute="_compute_storage_used_pct", store=True)
    total_ram_gb = fields.Float(string="RAM (GB)", digits=(10, 2))
    battery_level = fields.Integer(string="Battery %")
    battery_health = fields.Char()
    is_rooted = fields.Boolean(
        string="Rooted / Jailbroken", tracking=True,
        help="Best-effort detection. Treat as a signal, not proof.")

    # ── OS ────────────────────────────────────────────────────────────────
    os_version = fields.Char(string="OS Version", tracking=True,
                             help="e.g. '14' on Android, '17.4.1' on iOS")
    os_build = fields.Char(string="Build")
    security_patch = fields.Char(
        string="Security Patch", tracking=True,
        help="Android security patch level, e.g. 2026-05-01")
    sdk_int = fields.Integer(string="Android SDK", help="Build.VERSION.SDK_INT")

    os_update_state = fields.Selection(
        [("unknown", "Unknown"),
         ("current", "Up to date"),
         ("minor_behind", "Minor Update Available"),
         ("major_behind", "Major Update Available"),
         ("unsupported", "Unsupported / End of Life")],
        default="unknown", tracking=True, index=True,
        compute="_compute_os_update_state", store=True,
        string="OS Update Status")
    os_update_detail = fields.Char(
        compute="_compute_os_update_state", store=True,
        string="Update Detail")
    latest_known_version = fields.Char(
        compute="_compute_os_update_state", store=True,
        string="Latest Available")
    patch_age_days = fields.Integer(
        compute="_compute_os_update_state", store=True,
        string="Patch Age (days)",
        help="Days since the reported Android security patch level")

    # ── Network ───────────────────────────────────────────────────────────
    carrier = fields.Char()
    connection_type = fields.Selection(
        [("wifi", "Wi-Fi"), ("cellular", "Cellular"),
         ("ethernet", "Ethernet"), ("offline", "Offline")])
    ip_address = fields.Char(string="Last IP")
    wifi_ssid = fields.Char(string="Wi-Fi SSID")

    # ── Assignment ────────────────────────────────────────────────────────
    employee_id = fields.Many2one("hr.employee", string="Assigned To", tracking=True)
    department_id = fields.Many2one("hr.department", string="Department", tracking=True)
    ownership = fields.Selection(
        [("company", "Company Owned"), ("byod", "Personal (BYOD)")],
        default="company", tracking=True)

    # ── Agent / status ────────────────────────────────────────────────────
    agent_version = fields.Char()
    enrolled_on = fields.Datetime(readonly=True)
    last_seen = fields.Datetime(string="Last Check-in", readonly=True, index=True)

    # Non-stored but searchable, matching asset.asset.agent_status so the two
    # fleets can be reported on the same way.
    status = fields.Selection(
        [("never", "Never Checked In"), ("online", "Online"), ("offline", "Offline")],
        compute="_compute_status", search="_search_status",
        store=False, string="Status")

    # ── Related collections ───────────────────────────────────────────────
    application_ids = fields.One2many(
        "mobile.application", "device_id", string="Installed Apps")
    app_count = fields.Integer(compute="_compute_counts", string="Apps")
    location_ids = fields.One2many(
        "mobile.location", "device_id", string="Location History")
    command_ids = fields.One2many(
        "mobile.command", "device_id", string="Commands")
    volume_ids = fields.One2many(
        "mobile.storage.volume", "device_id", string="Storage Volumes")
    metric_ids = fields.One2many(
        "mobile.metric", "device_id", string="Metrics")
    app_change_ids = fields.One2many(
        "mobile.app.change", "device_id", string="App Changes")
    app_change_count = fields.Integer(
        compute="_compute_counts", string="Recent Changes")

    # Latest live figures, denormalised onto the device for list/kanban display.
    ram_available_gb = fields.Float(string="RAM Free (GB)", digits=(10, 2))
    ram_used_pct = fields.Float(string="RAM Used %")
    is_low_memory = fields.Boolean(string="Low Memory")
    battery_temp_c = fields.Float(string="Battery Temp (C)")
    is_charging = fields.Boolean()
    pending_command_count = fields.Integer(
        compute="_compute_counts", string="Pending Commands")

    last_latitude = fields.Float(digits=(10, 7), readonly=True)
    last_longitude = fields.Float(digits=(10, 7), readonly=True)

    # asset_management's `asset_map` field widget reads record.data.latitude /
    # .longitude by name. Aliasing rather than renaming keeps last_* meaningful
    # and lets the existing Leaflet widget be reused unchanged.
    latitude = fields.Float(
        related="last_latitude", store=True, digits=(10, 7), string="Latitude")
    longitude = fields.Float(
        related="last_longitude", store=True, digits=(10, 7), string="Longitude")
    location_source = fields.Char(readonly=True)
    # asset_map's info panel reads city / region / country by these names.
    city = fields.Char(readonly=True)
    region = fields.Char(string="State / Region", readonly=True)
    country = fields.Char(readonly=True)
    full_address = fields.Char(readonly=True)
    location_accuracy_m = fields.Float(string="Accuracy (m)", readonly=True)

    # Update Center rollup
    os_updates_pending = fields.Integer(
        compute="_compute_update_counts", store=True, string="OS Updates Pending")
    blocklisted_app_count = fields.Integer(
        compute="_compute_update_counts", store=True, string="Blocklisted Apps")
    system_app_count = fields.Integer(
        compute="_compute_update_counts", store=True, string="System Apps")
    user_app_count = fields.Integer(
        compute="_compute_update_counts", store=True, string="User Apps")
    last_location_at = fields.Datetime(readonly=True)
    last_location_address = fields.Char(readonly=True)

    _device_uid_uniq = models.Constraint(
        "UNIQUE(device_uid)", "Device UID must be unique.")

    # ══════════════════════════════════════════════════════════════════════
    # Computes
    # ══════════════════════════════════════════════════════════════════════
    @api.depends("name", "manufacturer", "model_name")
    def _compute_display_label(self):
        for rec in self:
            hardware = " ".join(filter(None, [rec.manufacturer, rec.model_name]))
            rec.display_label = f"{rec.name} ({hardware})" if hardware else (rec.name or "New Device")

    @api.depends("total_storage_gb", "free_storage_gb")
    def _compute_storage_used_pct(self):
        for rec in self:
            if rec.total_storage_gb:
                used = rec.total_storage_gb - rec.free_storage_gb
                rec.storage_used_pct = round(100.0 * used / rec.total_storage_gb, 1)
            else:
                rec.storage_used_pct = 0.0

    @api.depends("application_ids", "command_ids.state", "app_change_ids")
    def _compute_counts(self):
        for rec in self:
            rec.app_count = len(rec.application_ids)
            rec.pending_command_count = len(
                rec.command_ids.filtered(lambda c: c.state == "pending"))
            rec.app_change_count = len(rec.app_change_ids)

    def _heartbeat_timeout(self):
        return int(self.env["ir.config_parameter"].sudo().get_param(
            "mobile_device_management.heartbeat_timeout",
            default=str(DEFAULT_HEARTBEAT_TIMEOUT)))

    @api.depends("application_ids.is_system", "application_ids.is_blocklisted",
                 "os_update_state")
    def _compute_update_counts(self):
        Update = self.env["mobile.os.update"].sudo()
        for rec in self:
            apps = rec.application_ids
            rec.system_app_count = len(apps.filtered("is_system"))
            rec.user_app_count = len(apps) - rec.system_app_count
            rec.blocklisted_app_count = len(apps.filtered("is_blocklisted"))
            rec.os_updates_pending = Update.search_count([
                ("device_id", "=", rec.id),
                ("state", "in", ("available", "notified")),
            ])

    @api.depends("last_seen")
    def _compute_status(self):
        cutoff = fields.Datetime.now() - timedelta(seconds=self._heartbeat_timeout())
        for rec in self:
            if not rec.last_seen:
                rec.status = "never"
            elif rec.last_seen >= cutoff:
                rec.status = "online"
            else:
                rec.status = "offline"

    def _search_status(self, operator, value):
        cutoff = fields.Datetime.now() - timedelta(seconds=self._heartbeat_timeout())
        mapping = {
            "online": [("last_seen", "!=", False), ("last_seen", ">=", cutoff)],
            "offline": ["&", ("last_seen", "!=", False), ("last_seen", "<", cutoff)],
            "never": [("last_seen", "=", False)],
        }
        if operator not in ("=", "!="):
            return []
        domain = mapping.get(value)
        if domain is None:
            return []
        if operator == "!=":
            return ["!"] + domain
        return domain

    @api.depends("platform", "os_version", "security_patch")
    def _compute_os_update_state(self):
        """Compare the reported OS against the release catalogue.

        This is the server-side substitute for an on-device update check,
        which mobile platforms do not expose to ordinary apps.
        """
        Release = self.env["mobile.os.release"].sudo()
        for rec in self:
            rec.os_update_state = "unknown"
            rec.os_update_detail = False
            rec.latest_known_version = False
            rec.patch_age_days = 0

            if not rec.platform or not rec.os_version:
                continue

            verdict = Release.evaluate(
                platform=rec.platform,
                os_version=rec.os_version,
                security_patch=rec.security_patch,
            )
            rec.os_update_state = verdict["state"]
            rec.os_update_detail = verdict["detail"]
            rec.latest_known_version = verdict["latest"]
            rec.patch_age_days = verdict["patch_age_days"]

    # ══════════════════════════════════════════════════════════════════════
    # Enrolment / token
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def enrol(self, enrolment_token, payload):
        """Consume an enrolment token and return device credentials.

        Called by the controller. Returns a plain dict; the controller is
        responsible for shaping the HTTP response.
        """
        Enrolment = self.env["mobile.enrollment"].sudo()
        enrolment = Enrolment.consume(enrolment_token)
        if not enrolment:
            return {"success": False, "error": "invalid_or_expired_enrolment_token"}

        serial = (payload.get("serial_number") or "").strip()
        platform = payload.get("platform") or "android"
        if platform not in ("android", "ios"):
            platform = "android"

        device = False
        if serial:
            device = self.sudo().search(
                [("serial_number", "=", serial), ("platform", "=", platform)], limit=1)

        vals = {
            "name": payload.get("name") or payload.get("model_name") or "Mobile Device",
            "platform": platform,
            "manufacturer": payload.get("manufacturer"),
            "model_name": payload.get("model_name"),
            "serial_number": serial or False,
            "agent_version": payload.get("agent_version"),
            "enrolled_on": fields.Datetime.now(),
            "last_seen": fields.Datetime.now(),
        }
        if enrolment.employee_id:
            vals["employee_id"] = enrolment.employee_id.id
            vals["department_id"] = enrolment.employee_id.department_id.id
        if enrolment.ownership:
            vals["ownership"] = enrolment.ownership

        if device:
            # Re-enrolment: rotate the token so an old install loses access.
            vals["token"] = secrets.token_urlsafe(32)
            device.sudo().write(vals)
        else:
            vals["device_uid"] = str(uuid.uuid4())
            vals["token"] = secrets.token_urlsafe(32)
            device = self.sudo().create(vals)

        enrolment.sudo().write({
            "state": "used",
            "device_id": device.id,
            "used_on": fields.Datetime.now(),
        })

        _logger.info("[MDM] Enrolled %s (%s)", device.display_label, device.device_uid)
        return {
            "success": True,
            "device_uid": device.device_uid,
            "token": device.token,
            "heartbeat_interval": self._heartbeat_timeout(),
        }

    @api.model
    def authenticate(self, device_uid, token):
        """Return the device for a uid/token pair, or an empty recordset."""
        if not device_uid or not token:
            return self.browse()
        device = self.sudo().search(
            [("device_uid", "=", device_uid), ("active", "=", True)], limit=1)
        if not device or not device.token:
            return self.browse()
        # Constant-time comparison so a token cannot be guessed by timing.
        if not secrets.compare_digest(str(device.token), str(token)):
            return self.browse()
        return device

    def action_revoke_token(self):
        """Block the device. It must be re-enrolled to report again."""
        for rec in self:
            rec.sudo().write({"token": False})
            rec.message_post(body="Access token revoked. Device must re-enrol.")
        return True

    def action_view_apps(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Apps on {self.display_label}",
            "res_model": "mobile.application",
            "view_mode": "list,form",
            "domain": [("device_id", "=", self.id)],
            "context": {"default_device_id": self.id},
        }

    def action_view_locations(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Location History - {self.display_label}",
            "res_model": "mobile.location",
            "view_mode": "list,form",
            "domain": [("device_id", "=", self.id)],
        }

    def action_view_changes(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"App Changes - {self.display_label}",
            "res_model": "mobile.app.change",
            "view_mode": "list,form",
            "domain": [("device_id", "=", self.id)],
        }

    def action_view_metrics(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Metrics - {self.display_label}",
            "res_model": "mobile.metric",
            "view_mode": "graph,list",
            "domain": [("device_id", "=", self.id)],
        }

    def action_send_command(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Send Command",
            "res_model": "mobile.command",
            "view_mode": "form",
            "target": "new",
            "context": {"default_device_id": self.id},
        }

    def _sync_os_updates(self):
        """Create or close mobile.os.update rows to match the current verdict.

        Called on every check-in. The agent cannot tell us an update exists, so
        this is where the catalogue verdict becomes a visible record.
        """
        Update = self.env["mobile.os.update"].sudo()
        severity_map = {
            "minor_behind": "minor",
            "major_behind": "major",
            "unsupported": "eol",
        }
        for rec in self:
            if rec.os_update_state in ("current", "unknown") or not rec.latest_known_version:
                # Nothing outstanding: close anything still marked available.
                stale = Update.search([
                    ("device_id", "=", rec.id),
                    ("state", "in", ("available", "notified")),
                ])
                stale.write({"state": "installed",
                             "resolved_on": fields.Datetime.now()})
                continue

            severity = severity_map.get(rec.os_update_state, "minor")
            if rec.patch_age_days and rec.patch_age_days > 0 and severity == "minor":
                severity = "security"

            existing = Update.search([
                ("device_id", "=", rec.id),
                ("available_version", "=", rec.latest_known_version),
            ], limit=1)
            if existing:
                if existing.state in ("available", "notified"):
                    existing.write({
                        "current_version": rec.os_version or "",
                        "severity": severity,
                        "detail": rec.os_update_detail or "",
                    })
                continue

            Update.create({
                "device_id": rec.id,
                "current_version": rec.os_version or "",
                "available_version": rec.latest_known_version,
                "severity": severity,
                "detail": rec.os_update_detail or "",
                "state": "available",
            })

    def _record_metric(self, body):
        """Append one time-series sample per check-in."""
        Metric = self.env["mobile.metric"].sudo()
        for rec in self:
            Metric.create({
                "device_id": rec.id,
                "recorded_at": fields.Datetime.now(),
                "battery_level": body.get("battery_level") or 0,
                "battery_temp_c": body.get("battery_temp_c") or 0.0,
                "is_charging": bool(body.get("is_charging")),
                "ram_total_gb": body.get("total_ram_gb") or rec.total_ram_gb,
                "ram_available_gb": body.get("ram_available_gb") or 0.0,
                "is_low_memory": bool(body.get("is_low_memory")),
                "storage_free_gb": body.get("free_storage_gb") or rec.free_storage_gb,
                "storage_used_pct": rec.storage_used_pct,
            })

    def _sync_volumes(self, volumes):
        """Replace the per-volume storage breakdown.

        Volumes come and go (SD card removed), so anything absent from the
        report is deleted rather than left showing stale figures.
        """
        if not volumes:
            return
        Volume = self.env["mobile.storage.volume"].sudo()
        for rec in self:
            existing = {v.name: v for v in rec.volume_ids}
            seen = set()
            for entry in volumes:
                name = (entry.get("name") or "").strip()
                if not name:
                    continue
                seen.add(name)
                vals = {
                    "device_id": rec.id,
                    "name": name,
                    "uuid": entry.get("uuid"),
                    "is_primary": bool(entry.get("is_primary")),
                    "is_removable": bool(entry.get("is_removable")),
                    "state": entry.get("state"),
                    "total_gb": entry.get("total_gb") or 0.0,
                    "free_gb": entry.get("free_gb") or 0.0,
                    "last_reported": fields.Datetime.now(),
                }
                if name in existing:
                    existing[name].write(vals)
                else:
                    Volume.create(vals)
            for name, volume in existing.items():
                if name not in seen:
                    volume.unlink()

    def _record_app_changes(self, previous, current):
        """Diff two app inventories and log installs, removals and updates.

        `previous` and `current` are {package: (name, version)}. On the very
        first inventory `previous` is empty, and logging every pre-installed app
        as a fresh install would be noise, so that case is skipped.
        """
        Change = self.env["mobile.app.change"].sudo()
        blocked = self.env["ir.config_parameter"].sudo().get_param(
            "mobile_device_management.blocked_packages", default="")
        blocked_set = {p.strip() for p in blocked.split(",") if p.strip()}

        for rec in self:
            if not previous:
                continue

            rows = []
            for pkg, (name, version) in current.items():
                if pkg not in previous:
                    rows.append({
                        "device_id": rec.id, "change_type": "installed",
                        "app_name": name, "package_name": pkg,
                        "new_version": version,
                        "is_blocklisted": pkg in blocked_set,
                    })
                else:
                    old_version = previous[pkg][1]
                    if old_version and version and old_version != version:
                        rows.append({
                            "device_id": rec.id, "change_type": "updated",
                            "app_name": name, "package_name": pkg,
                            "old_version": old_version, "new_version": version,
                            "is_blocklisted": pkg in blocked_set,
                        })

            for pkg, (name, version) in previous.items():
                if pkg not in current:
                    rows.append({
                        "device_id": rec.id, "change_type": "removed",
                        "app_name": name, "package_name": pkg,
                        "old_version": version,
                        "is_blocklisted": pkg in blocked_set,
                    })

            if rows:
                Change.create(rows)
                flagged = [r for r in rows
                           if r["is_blocklisted"] and r["change_type"] == "installed"]
                if flagged:
                    rec.message_post(
                        body="Blocklisted app installed: "
                             + ", ".join(r["package_name"] for r in flagged))

    # ══════════════════════════════════════════════════════════════════════
    # Maintenance
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def cron_refresh_update_state(self):
        """Re-evaluate update state for the whole fleet.

        os_update_state depends on the release catalogue, which changes without
        any device reporting in, so a periodic recompute is needed.
        """
        devices = self.sudo().search([("os_version", "!=", False)])
        devices._compute_os_update_state()
        _logger.info("[MDM] Re-evaluated OS update state for %s device(s)", len(devices))
