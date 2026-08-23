# -*- coding: utf-8 -*-
"""Server-side OS release catalogue.

Why this exists
---------------
Neither Android nor iOS lets an ordinary app answer "is there an update for
this device?".

* Android exposes SystemUpdateManager.getSystemUpdateInfo() only to Device
  Owner / Profile Owner apps.
* iOS exposes update state only through MDM, and only for supervised devices.

Asking the agent is therefore a dead end on unmanaged devices, and on Chinese
OEM skins (vivo/Funtouch, HONOR/MagicOS, Xiaomi/HyperOS) even the Device Owner
API is frequently unreliable.

The approach that does work everywhere: the agent reports what it can always
see - OS version and, on Android, the security patch level - and the server
compares that against this catalogue.

Maintaining the catalogue is the trade-off. It is a handful of rows per year,
editable in the UI, and can be refreshed by whatever source you trust.
"""

import logging
import re
from datetime import date

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


def _parse_version(raw):
    """'17.4.1' -> (17, 4, 1). Non-numeric junk is ignored.

    Vendor strings are messy: 'MagicOS 8.0', '14 QPR2', '17.4.1 (21E236)'.
    Grabbing the numeric groups is more robust than trying to parse the whole.
    """
    if not raw:
        return ()
    numbers = re.findall(r"\d+", str(raw))
    return tuple(int(n) for n in numbers[:4])


def _parse_patch_date(raw):
    """Android security patch level '2026-05-01' -> date."""
    if not raw:
        return None
    match = re.search(r"(\d{4})-(\d{2})(?:-(\d{2}))?", str(raw))
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)),
                    int(match.group(3) or 1))
    except ValueError:
        return None


class MobileOsRelease(models.Model):
    _name = "mobile.os.release"
    _description = "Mobile OS Release Catalogue"
    _order = "platform, sequence desc, id desc"

    name = fields.Char(
        string="Version", required=True,
        help="Major version as reported by the device, e.g. '14' or '17.4.1'")
    platform = fields.Selection(
        [("android", "Android"), ("ios", "iOS")],
        required=True, index=True)
    sequence = fields.Integer(
        required=True, default=0,
        help="Higher is newer. Used for ordering rather than string compare.")
    released_on = fields.Date()
    is_latest = fields.Boolean(
        string="Latest Release",
        help="Devices below this are considered behind.")
    is_supported = fields.Boolean(
        default=True,
        help="Untick when the version stops receiving security updates. "
             "Devices on it are flagged Unsupported.")
    notes = fields.Char()
    active = fields.Boolean(default=True)

    _platform_version_uniq = models.Constraint(
        "UNIQUE(platform, name)",
        "That version already exists for this platform.")

    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def _max_patch_age_days(self):
        return int(self.env["ir.config_parameter"].sudo().get_param(
            "mobile_device_management.max_patch_age_days", default="120"))

    @api.model
    def evaluate(self, platform, os_version, security_patch=None):
        """Decide whether a reported OS is current.

        Returns {'state', 'detail', 'latest', 'patch_age_days'} where state is
        one of unknown / current / minor_behind / major_behind / unsupported.
        """
        blank = {"state": "unknown", "detail": False,
                 "latest": False, "patch_age_days": 0}

        releases = self.sudo().search([("platform", "=", platform)])
        if not releases:
            return blank

        latest = releases.filtered("is_latest")[:1] or releases[:1]
        latest_version = latest.name

        device_parts = _parse_version(os_version)
        if not device_parts:
            return dict(blank, latest=latest_version)

        # Patch age is evaluated independently: a device can be on the newest
        # major version and still be months behind on security patches, which
        # is the common case on budget Android hardware.
        patch_age = 0
        patch_date = _parse_patch_date(security_patch)
        if patch_date:
            patch_age = (date.today() - patch_date).days

        # Is the reported version explicitly marked unsupported?
        matched = releases.filtered(
            lambda r: _parse_version(r.name)[:1] == device_parts[:1])
        if matched and not any(matched.mapped("is_supported")):
            return {
                "state": "unsupported",
                "detail": f"{platform.title()} {os_version} no longer receives "
                          f"security updates. Latest is {latest_version}.",
                "latest": latest_version,
                "patch_age_days": patch_age,
            }

        latest_parts = _parse_version(latest_version)
        state, detail = "current", None

        if latest_parts and device_parts[:1] < latest_parts[:1]:
            state = "major_behind"
            detail = f"On {os_version}, latest is {latest_version}."
        elif latest_parts and device_parts < latest_parts:
            state = "minor_behind"
            detail = f"On {os_version}, latest is {latest_version}."

        # A stale security patch outranks "current" but not a major gap.
        max_age = self._max_patch_age_days()
        if patch_age > max_age and state == "current":
            state = "minor_behind"
            detail = f"Security patch is {patch_age} days old ({security_patch})."
        elif patch_age > max_age and detail:
            detail += f" Security patch {patch_age} days old."

        return {
            "state": state,
            "detail": detail or f"Up to date ({os_version}).",
            "latest": latest_version,
            "patch_age_days": patch_age,
        }
