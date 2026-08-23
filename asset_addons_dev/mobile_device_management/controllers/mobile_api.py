# -*- coding: utf-8 -*-
"""HTTP API for the mobile agent.

Conventions match asset_management's agent API: type='http', auth='public',
csrf=False, JSON in the request body, JSON out. Authentication is a device_uid
plus token issued at enrolment, sent in headers.

Every endpoint except /enrol requires a valid token. A revoked token yields 401
immediately, which is how a lost device is cut off.
"""

import json
import logging

from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)


def _json_response(payload, status=200):
    return request.make_response(
        json.dumps(payload, default=str),
        headers=[("Content-Type", "application/json")],
        status=status,
    )


def _read_body():
    try:
        return json.loads(request.httprequest.data or "{}")
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _authenticate():
    """Resolve the device from request headers.

    Returns (device, error_response). Exactly one is truthy.
    """
    headers = request.httprequest.headers
    device_uid = headers.get("X-Device-Uid")
    token = headers.get("X-Device-Token")

    # Fall back to the body so the agent can work through proxies that strip
    # custom headers, which is common on corporate mobile networks.
    if not device_uid or not token:
        body = _read_body()
        device_uid = device_uid or body.get("device_uid")
        token = token or body.get("token")

    device = request.env["mobile.device"].sudo().authenticate(device_uid, token)
    if not device:
        _logger.warning("[MDM] Rejected call for uid=%s", device_uid)
        return None, _json_response(
            {"success": False, "error": "unauthorised"}, status=401)
    return device, None


class MobileDeviceApi(http.Controller):

    # ══════════════════════════════════════════════════════════════════════
    # Enrolment
    # ══════════════════════════════════════════════════════════════════════
    @http.route("/api/mdm/enrol", type="http", auth="public",
                methods=["POST"], csrf=False)
    def enrol(self, **kwargs):
        """Exchange a one-time enrolment token for device credentials."""
        body = _read_body()
        enrol_token = body.get("enrol_token")
        if not enrol_token:
            return _json_response(
                {"success": False, "error": "missing_enrol_token"}, status=400)

        result = request.env["mobile.device"].sudo().enrol(enrol_token, body)
        status = 200 if result.get("success") else 403
        return _json_response(result, status=status)

    # ══════════════════════════════════════════════════════════════════════
    # Check-in
    # ══════════════════════════════════════════════════════════════════════
    @http.route("/api/mdm/checkin", type="http", auth="public",
                methods=["POST"], csrf=False)
    def checkin(self, **kwargs):
        """Heartbeat plus device state. The agent's main call.

        Returns any pending commands so a normal check-in doubles as command
        delivery - no separate poll needed.
        """
        device, error = _authenticate()
        if error:
            return error

        body = _read_body()
        vals = {
            "last_seen": fields.Datetime.now(),
            "ip_address": request.httprequest.environ.get("REMOTE_ADDR", ""),
        }

        # Only write keys the agent actually sent, so a partial report does not
        # blank out fields collected earlier.
        mapping = {
            "os_version": "os_version",
            "os_build": "os_build",
            "security_patch": "security_patch",
            "sdk_int": "sdk_int",
            "manufacturer": "manufacturer",
            "model_name": "model_name",
            "agent_version": "agent_version",
            "battery_level": "battery_level",
            "battery_health": "battery_health",
            "total_storage_gb": "total_storage_gb",
            "free_storage_gb": "free_storage_gb",
            "total_ram_gb": "total_ram_gb",
            "carrier": "carrier",
            "connection_type": "connection_type",
            "wifi_ssid": "wifi_ssid",
            "is_rooted": "is_rooted",
            "imei": "imei",
            "ram_available_gb": "ram_available_gb",
            "ram_used_pct": "ram_used_pct",
            "is_low_memory": "is_low_memory",
            "battery_temp_c": "battery_temp_c",
            "is_charging": "is_charging",
        }
        for key, field_name in mapping.items():
            if key in body and body[key] not in (None, ""):
                vals[field_name] = body[key]

        device.sudo().write(vals)
        device.sudo()._sync_os_updates()
        device.sudo()._record_metric(body)
        device.sudo()._sync_volumes(body.get("volumes") or [])

        commands = device.sudo().command_ids.filtered(
            lambda c: c.state == "pending")
        commands.sudo().write({
            "state": "sent",
            "sent_on": fields.Datetime.now(),
        })

        return _json_response({
            "success": True,
            "device_uid": device.device_uid,
            "os_update_state": device.os_update_state,
            "os_update_detail": device.os_update_detail,
            "latest_known_version": device.latest_known_version,
            "next_checkin_seconds": device._heartbeat_timeout(),
            "commands": [{
                "id": c.id,
                "type": c.command_type,
                "payload": c.payload or "",
            } for c in commands],
        })

    # ══════════════════════════════════════════════════════════════════════
    # Inventory
    # ══════════════════════════════════════════════════════════════════════
    @http.route("/api/mdm/apps", type="http", auth="public",
                methods=["POST"], csrf=False)
    def report_apps(self, **kwargs):
        """Full app inventory replace.

        The agent sends the complete list; anything missing is treated as
        uninstalled. A delta protocol would be lighter but needs the agent to
        track state reliably across reinstalls, which it cannot.
        """
        device, error = _authenticate()
        if error:
            return error

        body = _read_body()
        apps = body.get("apps") or []
        if not isinstance(apps, list):
            return _json_response(
                {"success": False, "error": "apps_must_be_a_list"}, status=400)

        App = request.env["mobile.application"].sudo()
        existing = {a.package_name: a for a in device.application_ids}
        # Snapshot before mutating, so the diff reflects the real previous state.
        previous = {pkg: (a.name, a.version_name) for pkg, a in existing.items()}
        seen = set()
        created = updated = 0

        for entry in apps:
            package = (entry.get("package_name") or "").strip()
            if not package:
                continue
            seen.add(package)
            vals = {
                "device_id": device.id,
                "name": entry.get("name") or package,
                "package_name": package,
                "version_name": entry.get("version_name"),
                "version_code": str(entry.get("version_code") or ""),
                "is_system": bool(entry.get("is_system")),
                "size_mb": entry.get("size_mb") or 0.0,
                "permissions": entry.get("permissions"),
            }
            if entry.get("installed_on"):
                vals["installed_on"] = entry["installed_on"]
            if entry.get("updated_on"):
                vals["updated_on"] = entry["updated_on"]

            record = existing.get(package)
            if record:
                record.write(vals)
                updated += 1
            else:
                App.create(vals)
                created += 1

        removed = [a for pkg, a in existing.items() if pkg not in seen]
        removed_count = len(removed)
        for record in removed:
            record.unlink()

        # Derive the change log. The agent cannot reliably watch for installs
        # in the background, so changes are computed here by diffing reports.
        current = {}
        for entry in apps:
            pkg = (entry.get("package_name") or "").strip()
            if pkg:
                current[pkg] = (entry.get("name") or pkg, entry.get("version_name"))
        device.sudo()._record_app_changes(previous, current)

        _logger.info("[MDM] %s apps: +%s ~%s -%s",
                     device.display_label, created, updated, removed_count)
        return _json_response({
            "success": True,
            "created": created,
            "updated": updated,
            "removed": removed_count,
        })

    @http.route("/api/mdm/location", type="http", auth="public",
                methods=["POST"], csrf=False)
    def report_location(self, **kwargs):
        """Record one or more location points."""
        device, error = _authenticate()
        if error:
            return error

        body = _read_body()
        points = body.get("points")
        if points is None:
            # Single-point form.
            points = [body] if body.get("latitude") is not None else []
        if not isinstance(points, list):
            return _json_response(
                {"success": False, "error": "points_must_be_a_list"}, status=400)

        Location = request.env["mobile.location"].sudo()
        stored = 0
        newest = None

        for point in points:
            latitude = point.get("latitude")
            longitude = point.get("longitude")
            if latitude is None or longitude is None:
                continue
            recorded_at = point.get("recorded_at") or fields.Datetime.now()
            Location.create({
                "device_id": device.id,
                "latitude": latitude,
                "longitude": longitude,
                "accuracy_m": point.get("accuracy_m") or 0.0,
                "altitude_m": point.get("altitude_m") or 0.0,
                "speed_kmh": point.get("speed_kmh") or 0.0,
                "recorded_at": recorded_at,
                "source": point.get("source") or "fused",
                "is_mock": bool(point.get("is_mock")),
            })
            stored += 1
            newest = (latitude, longitude, recorded_at)

        if newest:
            vals = {
                "last_latitude": newest[0],
                "last_longitude": newest[1],
                "last_location_at": newest[2],
                "location_source": (points[-1].get("source") or "fused"),
                "location_accuracy_m": points[-1].get("accuracy_m") or 0.0,
            }
            # Only re-geocode when the device has actually moved. Nominatim is
            # rate-limited, and a stationary phone reporting hourly would
            # otherwise hammer it for no new information.
            moved = (
                abs((device.last_latitude or 0.0) - float(newest[0])) > 0.002
                or abs((device.last_longitude or 0.0) - float(newest[1])) > 0.002
            )
            if moved or not device.full_address:
                vals.update(Location.reverse_geocode(newest[0], newest[1]))
            device.sudo().write(vals)

        return _json_response({"success": True, "stored": stored})

    # ══════════════════════════════════════════════════════════════════════
    # Commands
    # ══════════════════════════════════════════════════════════════════════
    @http.route("/api/mdm/command_result", type="http", auth="public",
                methods=["POST"], csrf=False)
    def command_result(self, **kwargs):
        """Agent reports the outcome of a command it was given."""
        device, error = _authenticate()
        if error:
            return error

        body = _read_body()
        command_id = body.get("command_id")
        if not command_id:
            return _json_response(
                {"success": False, "error": "missing_command_id"}, status=400)

        command = request.env["mobile.command"].sudo().browse(int(command_id))
        # Scope the lookup to this device so one device cannot close another's
        # commands by guessing ids.
        if not command.exists() or command.device_id != device:
            return _json_response(
                {"success": False, "error": "unknown_command"}, status=404)

        state = body.get("state") or "done"
        if state not in ("done", "failed", "unsupported"):
            state = "done"

        command.write({
            "state": state,
            "result": body.get("result") or "",
            "completed_on": fields.Datetime.now(),
        })
        return _json_response({"success": True})

    # ══════════════════════════════════════════════════════════════════════
    # Diagnostics
    # ══════════════════════════════════════════════════════════════════════
    @http.route("/api/mdm/ping", type="http", auth="public",
                methods=["GET", "POST"], csrf=False)
    def ping(self, **kwargs):
        """Unauthenticated reachability check, for agent setup screens."""
        return _json_response({
            "success": True,
            "service": "mobile_device_management",
            "server_time": fields.Datetime.now(),
        })
