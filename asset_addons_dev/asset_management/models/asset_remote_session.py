# -*- coding: utf-8 -*-
# Remote Assistance for managed assets — session model.
import logging
import secrets
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

STATE_SELECTION = [
    ("requested", "Requested"),
    ("accepted", "User Accepted"),
    ("agent_connected", "Agent Connected"),
    ("connected", "Connected"),
    ("ended", "Session Ended"),
    ("rejected", "Rejected"),
    ("expired", "Expired"),
]
OPEN_STATES = ("agent_connected", "connected")
CLOSED_STATES = ("ended", "expired", "rejected")


class AssetRemoteSession(models.Model):
    _name = "asset.remote.session"
    _description = "Asset Remote Assistance Session"
    _inherit = ["mail.thread"]
    _order = "create_date desc"

    name = fields.Char(required=True, copy=False, readonly=True,
                       default=lambda self: _("New"))
    asset_id = fields.Many2one("asset.asset", string="Asset", required=True,
                               ondelete="cascade", tracking=True)
    serial_number = fields.Char(related="asset_id.serial_number", store=True,
                                string="Serial Number")
    state = fields.Selection(STATE_SELECTION, default="requested",
                             required=True, tracking=True, copy=False)
    session_token = fields.Char(copy=False, readonly=True, index=True)
    relay_url = fields.Char(compute="_compute_relay_url")
    duration_minutes = fields.Integer(string="Duration (Minutes)",
                                       default=30, required=True)
    admin_user_id = fields.Many2one("res.users", string="Administrator",
                                    required=True,
                                    default=lambda self: self.env.user)
    company_id = fields.Many2one("res.company", string="Company",
                                 default=lambda self: self.env.company)

    requested_on = fields.Datetime(copy=False)
    consent_on = fields.Datetime(copy=False)
    agent_connected_on = fields.Datetime(copy=False)
    start_time = fields.Datetime(copy=False)
    end_time = fields.Datetime(copy=False)
    actual_duration = fields.Float(string="Actual Duration (min)",
                                   compute="_compute_actual_duration",
                                   store=True)
    agent_os = fields.Char(readonly=True, copy=False)
    note = fields.Char(readonly=True, copy=False)
    expiry = fields.Datetime(copy=False)
    history_ids = fields.One2many("asset.remote.history", "session_id",
                                  string="History")
    state_help = fields.Char(compute="_compute_state_help")

    def _compute_relay_url(self):
        relay = self.env["ir.config_parameter"].sudo().get_param(
            "asset_remote.relay_url", "ws://localhost:8766")
        for rec in self:
            rec.relay_url = relay

    @api.depends("start_time", "end_time")
    def _compute_actual_duration(self):
        for rec in self:
            if rec.start_time and rec.end_time:
                rec.actual_duration = round(
                    (rec.end_time - rec.start_time).total_seconds() / 60.0, 1)
            else:
                rec.actual_duration = 0.0

    def _compute_state_help(self):
        texts = {
            "requested": _("Waiting for the user on the machine to accept…"),
            "accepted": _("User accepted. The agent is connecting…"),
            "agent_connected": _("Agent connected. You can Start the Remote "
                                 "Session."),
            "connected": _("Live session in progress."),
            "ended": _("Session ended."),
            "rejected": _("The user rejected the request."),
            "expired": _("The request expired."),
        }
        for rec in self:
            rec.state_help = texts.get(rec.state, "")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "asset.remote.session") or _("New")
        return super().create(vals_list)

    def _log(self, action, note=False, actor=False):
        self.ensure_one()
        self.env["asset.remote.history"].sudo().create({
            "session_id": self.id, "action": action,
            "actor": actor or self.env.user.name, "note": note or ""})

    def _base_url(self):
        return self.env["ir.config_parameter"].sudo().get_param(
            "web.base.url", "http://localhost:8069")

    # ---- consent + status (from the agent worker) --------------------- #
    def _consent(self, decision):
        self.ensure_one()
        if self.state != "requested":
            return
        if decision == "accept":
            self.write({"state": "accepted",
                        "consent_on": fields.Datetime.now()})
            self._log("accepted", note=_("User accepted on the machine"),
                      actor=self.asset_id.display_name)
            self.message_post(body=_("User accepted the remote request."))
        else:
            self.write({"state": "rejected",
                        "end_time": fields.Datetime.now()})
            self._log("rejected", note=_("User rejected on the machine"),
                      actor=self.asset_id.display_name)
            self.message_post(body=_("User rejected the remote request."))

    def _agent_report(self, status, os_name=False, note=False):
        self.ensure_one()
        vals = {}
        if os_name:
            vals["agent_os"] = os_name
        if note:
            vals["note"] = note
        if status == "connected":
            if self.state in ("accepted", "requested"):
                vals["state"] = "agent_connected"
                vals["agent_connected_on"] = fields.Datetime.now()
                self._log("agent_connected", note=_("Agent connected to relay"))
        elif status == "streaming":
            if self.state == "agent_connected":
                vals["state"] = "connected"
                if not self.start_time:
                    vals["start_time"] = fields.Datetime.now()
                self._log("connected", note=_("Screen sharing started"))
        elif status in ("ended", "stopped"):
            if self.state not in CLOSED_STATES:
                vals["state"] = "ended"
                vals["end_time"] = fields.Datetime.now()
                self._log("ended", note=note or _("Agent disconnected"),
                          actor=self.asset_id.display_name)
        elif status == "error":
            self._log("error", note=note or _("Agent error"))
        if vals:
            self.write(vals)
        return True

    # ---- admin actions ------------------------------------------------ #
    def action_open_viewer(self):
        self.ensure_one()
        if self.state not in ("agent_connected", "connected"):
            raise UserError(_("The agent is not connected yet."))
        return {"type": "ir.actions.act_url",
                "url": "/asset_remote/viewer/%s" % self.session_token,
                "target": "new"}

    def action_end_session(self):
        self.ensure_one()
        if self.state in CLOSED_STATES:
            return True
        self.write({"state": "ended", "end_time": fields.Datetime.now()})
        self._log("ended", note=_("Ended by administrator"))
        return True

    @api.model
    def _cron_expire_stale(self):
        now = fields.Datetime.now()
        for rec in self.search([("state", "=", "requested"),
                                 ("expiry", "!=", False),
                                 ("expiry", "<", now)]):
            rec.write({"state": "expired"})
            rec._log("expired", note=_("Request expired unanswered"))
        for rec in self.search([("state", "in", OPEN_STATES),
                                 ("start_time", "!=", False)]):
            if now > rec.start_time + timedelta(
                    minutes=(rec.duration_minutes or 30) + 5):
                rec.write({"state": "ended", "end_time": now})
                rec._log("ended", note=_("Duration limit reached"))
        return True

    @api.model
    def _for_token(self, token):
        if not token:
            return self.browse()
        return self.sudo().search([("session_token", "=", token)], limit=1)

    def _token_is_live(self):
        self.ensure_one()
        return self.state in OPEN_STATES
