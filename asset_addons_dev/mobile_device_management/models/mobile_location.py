# -*- coding: utf-8 -*-
"""Device location history.

Only recorded when the user has granted location permission. On Android 10+
background location requires a separate grant that the user can revoke at any
time, so gaps in this history are expected and are not an agent fault.

Retention is enforced by a cron: location data is the most privacy-sensitive
thing this module stores, and keeping it indefinitely is rarely justifiable.
"""

import json
import logging
from datetime import timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class MobileLocation(models.Model):
    _name = "mobile.location"
    _description = "Mobile Device Location"
    _order = "recorded_at desc, id desc"
    _rec_name = "recorded_at"

    device_id = fields.Many2one(
        "mobile.device", required=True, ondelete="cascade", index=True)
    latitude = fields.Float(digits=(10, 7), required=True)
    longitude = fields.Float(digits=(10, 7), required=True)
    accuracy_m = fields.Float(string="Accuracy (m)")
    altitude_m = fields.Float(string="Altitude (m)")
    speed_kmh = fields.Float(string="Speed (km/h)")
    recorded_at = fields.Datetime(required=True, index=True)
    address = fields.Char(help="Reverse-geocoded, if a geocoder is configured.")
    source = fields.Selection(
        [("gps", "GPS"), ("network", "Network"), ("fused", "Fused"),
         ("manual", "Manual"), ("command", "Locate Command")],
        default="fused")
    is_mock = fields.Boolean(
        string="Mock Location",
        help="Device reported this as a simulated location.")

    employee_id = fields.Many2one(related="device_id.employee_id", store=True)

    map_url = fields.Char(compute="_compute_map_url", string="Map")

    @api.depends("latitude", "longitude")
    def _compute_map_url(self):
        for rec in self:
            if rec.latitude or rec.longitude:
                rec.map_url = (
                    f"https://www.openstreetmap.org/?mlat={rec.latitude}"
                    f"&mlon={rec.longitude}#map=16/{rec.latitude}/{rec.longitude}")
            else:
                rec.map_url = False

    @api.model
    def reverse_geocode(self, latitude, longitude):
        """Resolve coordinates to a postal address via OpenStreetMap Nominatim.

        Best-effort only. Nominatim rate-limits to roughly one request per
        second and requires a real User-Agent, so this is called once per
        device when its position moves materially - not for every point.
        Failure returns an empty dict; a missing address is never fatal.
        """
        enabled = self.env["ir.config_parameter"].sudo().get_param(
            "mobile_device_management.reverse_geocode", default="1")
        if enabled not in ("1", "True", "true"):
            return {}
        try:
            params = urlencode({
                "lat": latitude, "lon": longitude,
                "format": "json", "zoom": "18", "addressdetails": "1",
            })
            request = Request(
                f"https://nominatim.openstreetmap.org/reverse?{params}",
                headers={"User-Agent": "OdooMDM/1.0 (asset management)"},
            )
            with urlopen(request, timeout=8) as response:
                data = json.loads(response.read().decode())
            address = data.get("address") or {}
            return {
                "full_address": data.get("display_name"),
                "city": (address.get("city") or address.get("town")
                         or address.get("village") or address.get("suburb")),
                "region": address.get("state"),
                "country": address.get("country"),
            }
        except Exception as error:
            _logger.info("[MDM] Reverse geocode failed: %s", error)
            return {}

    @api.model
    def _retention_days(self):
        return int(self.env["ir.config_parameter"].sudo().get_param(
            "mobile_device_management.location_retention_days", default="90"))

    @api.model
    def cron_purge_old_locations(self):
        """Delete location points past the retention window."""
        days = self._retention_days()
        if days <= 0:
            return
        cutoff = fields.Datetime.now() - timedelta(days=days)
        stale = self.sudo().search([("recorded_at", "<", cutoff)])
        count = len(stale)
        stale.unlink()
        _logger.info("[MDM] Purged %s location point(s) older than %s days",
                     count, days)
