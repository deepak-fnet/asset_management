# -*- coding: utf-8 -*-
"""Enrolment tokens.

A manager generates a token, the agent scans it as a QR code, and exchanges it
once for a permanent per-device token. Enrolment tokens are single-use and
expire, so a screenshot of a QR code left in a chat is not a lasting hole.
"""

import json
import logging
import secrets
from urllib.parse import quote

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MobileEnrollment(models.Model):
    _name = "mobile.enrollment"
    _description = "Mobile Device Enrolment Token"
    _order = "create_date desc"
    _rec_name = "token"

    token = fields.Char(
        required=True, readonly=True, copy=False, index=True,
        default=lambda self: secrets.token_urlsafe(24))
    employee_id = fields.Many2one("hr.employee", string="Assign To")
    ownership = fields.Selection(
        [("company", "Company Owned"), ("byod", "Personal (BYOD)")],
        default="company", required=True)
    expires_on = fields.Datetime(
        required=True,
        default=lambda self: fields.Datetime.add(fields.Datetime.now(), days=7))
    state = fields.Selection(
        [("open", "Open"), ("used", "Used"), ("expired", "Expired"),
         ("revoked", "Revoked")],
        default="open", required=True, index=True)
    device_id = fields.Many2one("mobile.device", readonly=True)
    used_on = fields.Datetime(readonly=True)
    note = fields.Char()

    enrol_url = fields.Char(compute="_compute_enrol_url", string="Enrolment URL")
    qr_payload = fields.Char(compute="_compute_enrol_url", string="QR Payload")
    qr_code_html = fields.Html(
        compute="_compute_enrol_url", sanitize=False, string="QR Code",
        help="Rendered by Odoo's /report/barcode controller. There is no "
             "barcode field widget in Odoo 19, so this is an img tag.")

    _token_uniq = models.Constraint("UNIQUE(token)", "Token must be unique.")

    @api.depends("token")
    def _compute_enrol_url(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for rec in self:
            rec.enrol_url = f"{base}/api/mdm/enrol" if base else "/api/mdm/enrol"
            # What the agent's QR scanner expects: server + token in one blob.
            payload = json.dumps({"server": base, "enrol_token": rec.token or ""},
                                 separators=(",", ":"))
            rec.qr_payload = payload
            if rec.token:
                src = ("/report/barcode/?barcode_type=QR"
                       f"&value={quote(payload, safe='')}"
                       "&width=256&height=256")
                rec.qr_code_html = (
                    f'<img src="{src}" alt="Enrolment QR code" '
                    'style="width:220px;height:220px;image-rendering:pixelated;"/>')
            else:
                rec.qr_code_html = False

    @api.model
    def consume(self, token):
        """Return a valid open enrolment for `token`, or an empty recordset."""
        if not token:
            return self.browse()
        enrolment = self.sudo().search([("token", "=", token)], limit=1)
        if not enrolment or enrolment.state != "open":
            return self.browse()
        if enrolment.expires_on and enrolment.expires_on < fields.Datetime.now():
            enrolment.sudo().write({"state": "expired"})
            return self.browse()
        return enrolment

    def action_revoke(self):
        return self.write({"state": "revoked"})

    @api.model
    def cron_expire_tokens(self):
        stale = self.sudo().search([
            ("state", "=", "open"),
            ("expires_on", "<", fields.Datetime.now()),
        ])
        stale.write({"state": "expired"})
        if stale:
            _logger.info("[MDM] Expired %s enrolment token(s)", len(stale))
