# -*- coding: utf-8 -*-
import logging
import secrets
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# The full lifecycle exactly as specified in the module brief.
STATE_SELECTION = [
    ("draft", "Draft"),
    ("sent", "Invitation Sent"),
    ("waiting_response", "Waiting for Customer Response"),
    ("accepted", "Customer Accepted"),
    ("waiting_agent", "Waiting for Agent Installation"),
    ("agent_connected", "Agent Connected"),
    ("preparing", "Preparing Screen Sharing"),
    ("connected", "Connected"),
    ("ended", "Session Ended"),
    ("expired", "Expired"),
    ("rejected", "Rejected"),
]

# States in which a live/negotiating session is considered "open" — the relay
# will accept the token and the agent should keep its connection.
OPEN_STATES = ("agent_connected", "preparing", "connected")
# States after the customer has consented but before the session is finished.
ACTIVE_WINDOW = ("accepted", "waiting_agent", "agent_connected",
                 "preparing", "connected")
CLOSED_STATES = ("ended", "expired", "rejected")


class RemoteAssistanceSession(models.Model):
    _name = "remote.assistance.session"
    _description = "Remote Assistance Session"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(
        string="Reference", required=True, copy=False, readonly=True,
        index=True, default=lambda self: _("New"))

    # -- Step 1 request fields -------------------------------------------
    partner_id = fields.Many2one(
        "res.partner", string="Customer", required=True, tracking=True)
    customer_email = fields.Char(
        string="Customer Email", required=True, tracking=True)
    reason = fields.Text(
        string="Reason for Connection", required=True, tracking=True)
    duration_minutes = fields.Integer(
        string="Session Duration (Minutes)", default=30, required=True,
        help="How long the customer is agreeing to share their screen for. "
             "The temporary agent enforces this limit locally.")
    machine_type = fields.Selection(
        [("ubuntu", "Ubuntu"), ("windows", "Windows")],
        string="Machine Type", required=True, default="windows",
        tracking=True)
    notes = fields.Text(string="Notes")

    admin_user_id = fields.Many2one(
        "res.users", string="Administrator", required=True, tracking=True,
        default=lambda self: self.env.user)
    company_id = fields.Many2one(
        "res.company", string="Company", required=True,
        default=lambda self: self.env.company)

    # -- lifecycle --------------------------------------------------------
    state = fields.Selection(
        STATE_SELECTION, string="Status", default="draft",
        required=True, tracking=True, copy=False)

    session_token = fields.Char(
        string="Session Token", copy=False, readonly=True, index=True,
        groups="remote_assistance.group_remote_assistance_user")
    relay_url = fields.Char(
        string="Relay URL", compute="_compute_relay_url")

    invite_expiry = fields.Datetime(
        string="Invitation Expires", copy=False,
        help="After this time an un-answered invitation is marked Expired.")
    invited_on = fields.Datetime(string="Invited On", copy=False)
    accepted_on = fields.Datetime(string="Accepted On", copy=False)
    agent_connected_on = fields.Datetime(string="Agent Connected On",
                                         copy=False)
    start_time = fields.Datetime(string="Session Start", copy=False)
    end_time = fields.Datetime(string="Session End", copy=False)
    actual_duration = fields.Float(
        string="Actual Duration (min)", compute="_compute_actual_duration",
        store=True)

    # -- reported by the temporary agent ---------------------------------
    agent_os = fields.Char(string="Reported OS", readonly=True, copy=False)
    agent_note = fields.Char(string="Agent Note", readonly=True, copy=False)
    recording_ids = fields.One2many(
        "remote.assistance.recording", "session_id", string="Recordings")
    recording_count = fields.Integer(compute="_compute_recording_count")

    history_ids = fields.One2many(
        "remote.assistance.history", "session_id", string="History")

    # helper for the form (banner text)
    state_help = fields.Char(compute="_compute_state_help")

    @api.depends("recording_ids")
    def _compute_recording_count(self):
        for rec in self:
            rec.recording_count = len(rec.recording_ids)

    def action_view_recordings(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Recordings"),
            "res_model": "remote.assistance.recording",
            "view_mode": "list,form",
            "domain": [("session_id", "=", self.id)],
            "context": {"default_session_id": self.id},
        }

    # ------------------------------------------------------------------ #
    # Compute
    # ------------------------------------------------------------------ #
    def _compute_relay_url(self):
        relay = self.env["ir.config_parameter"].sudo().get_param(
            "remote_assistance.relay_url", "wss://localhost:8765")
        for rec in self:
            rec.relay_url = relay

    @api.depends("start_time", "end_time")
    def _compute_actual_duration(self):
        for rec in self:
            if rec.start_time and rec.end_time:
                delta = rec.end_time - rec.start_time
                rec.actual_duration = round(delta.total_seconds() / 60.0, 1)
            else:
                rec.actual_duration = 0.0

    @api.depends("state")
    def _compute_state_help(self):
        texts = {
            "draft": _("Fill in the request and click Send Invitation."),
            "sent": _("Invitation email sent. Waiting for the customer."),
            "waiting_response": _("Waiting for the customer to Accept or "
                                  "Reject the invitation."),
            "accepted": _("Customer accepted. They are downloading the "
                          "temporary agent."),
            "waiting_agent": _("Waiting for the customer to run the "
                               "temporary agent…"),
            "agent_connected": _("Agent connected and ready. You can now "
                                 "Start the Remote Session."),
            "preparing": _("Preparing screen sharing…"),
            "connected": _("Live session in progress."),
            "ended": _("Session ended."),
            "expired": _("Invitation or session expired."),
            "rejected": _("Customer rejected the invitation."),
        }
        for rec in self:
            rec.state_help = texts.get(rec.state, "")

    # ------------------------------------------------------------------ #
    # Onchange / CRUD
    # ------------------------------------------------------------------ #
    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        if self.partner_id and not self.customer_email:
            self.customer_email = self.partner_id.email

    @api.constrains("duration_minutes")
    def _check_duration(self):
        for rec in self:
            if rec.duration_minutes <= 0:
                raise ValidationError(_("Session duration must be a positive "
                                        "number of minutes."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "remote.assistance.session") or _("New")
        return super().create(vals_list)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _log_history(self, action, note=False, actor=False):
        self.ensure_one()
        self.env["remote.assistance.history"].sudo().create({
            "session_id": self.id,
            "action": action,
            "actor": actor or self.env.user.name,
            "note": note or "",
        })

    def _base_url(self):
        return self.env["ir.config_parameter"].sudo().get_param(
            "web.base.url", "http://localhost:8069")

    def _agent_download_base(self):
        """Where the prebuilt RemoteAssist.exe / .deb binaries are hosted.
        If unset, the personalized portable package is offered instead."""
        return self.env["ir.config_parameter"].sudo().get_param(
            "remote_assistance.agent_download_base", "")

    # ------------------------------------------------------------------ #
    # Step 1 -> 2/3 : Send invitation
    # ------------------------------------------------------------------ #
    def action_send_invitation(self):
        self.ensure_one()
        if self.state not in ("draft", "expired", "rejected"):
            raise UserError(_("An invitation has already been sent for this "
                              "session."))
        if not self.customer_email:
            raise UserError(_("Please set the customer email before sending "
                              "the invitation."))
        # Mint a fresh capability token for this session.
        self.session_token = secrets.token_urlsafe(32)
        self.invited_on = fields.Datetime.now()
        self.invite_expiry = fields.Datetime.now() + timedelta(hours=24)
        # Reset any stale timestamps if this is a re-send.
        self.write({
            "accepted_on": False, "agent_connected_on": False,
            "start_time": False, "end_time": False,
            "agent_os": False, "agent_note": False,
            "state": "waiting_response",
        })
        template = self.env.ref(
            "remote_assistance.mail_template_remote_invitation",
            raise_if_not_found=False)
        if template:
            template.sudo().send_mail(self.id, force_send=True,
                                      email_values={"email_to":
                                                    self.customer_email})
        self._log_history("invitation_sent",
                          note=_("Invitation emailed to %s",
                                 self.customer_email))
        self.message_post(body=_("Invitation sent to %s.",
                                 self.customer_email))
        return True

    # ------------------------------------------------------------------ #
    # Step 3 : customer accept / reject  (called from public controller)
    # ------------------------------------------------------------------ #
    def _mark_accepted(self):
        self.ensure_one()
        self.write({"state": "accepted",
                    "accepted_on": fields.Datetime.now()})
        self._log_history("accepted", note=_("Customer accepted"),
                          actor=self.partner_id.name or self.customer_email)
        self.message_post(body=_("Customer accepted the invitation."))

    def _mark_rejected(self):
        self.ensure_one()
        self.write({"state": "rejected", "end_time": fields.Datetime.now()})
        self._log_history("rejected", note=_("Customer rejected"),
                          actor=self.partner_id.name or self.customer_email)
        self.message_post(body=_("Customer rejected the invitation."))
        # Notify the administrator.
        self.activity_schedule(
            "mail.mail_activity_data_todo",
            summary=_("Remote assistance rejected"),
            note=_("%s rejected the remote assistance invitation.",
                   self.partner_id.name or self.customer_email),
            user_id=self.admin_user_id.id)

    def _mark_agent_downloading(self):
        """Customer hit the download route -> waiting for them to run it."""
        self.ensure_one()
        if self.state == "accepted":
            self.write({"state": "waiting_agent"})
            self._log_history("agent_downloaded",
                              note=_("Customer downloaded the agent"))

    # ------------------------------------------------------------------ #
    # Steps 5/6 : agent status reports (called from agent controller)
    # ------------------------------------------------------------------ #
    def _agent_report(self, status, os_name=False, note=False):
        """status: connected | streaming | ended | error"""
        self.ensure_one()
        vals = {}
        if os_name:
            vals["agent_os"] = os_name
        if note:
            vals["agent_note"] = note
        if status == "connected":
            # Agent authenticated + connected to relay, waiting for admin.
            if self.state in ("accepted", "waiting_agent"):
                vals["state"] = "agent_connected"
                vals["agent_connected_on"] = fields.Datetime.now()
                self._log_history("agent_connected",
                                  note=_("Temporary agent connected"))
        elif status == "streaming":
            # A viewer joined and frames are flowing.
            if self.state in ("agent_connected", "preparing"):
                vals["state"] = "connected"
                if not self.start_time:
                    vals["start_time"] = fields.Datetime.now()
                self._log_history("connected", note=_("Screen sharing started"))
        elif status in ("ended", "stopped"):
            if self.state not in CLOSED_STATES:
                vals["state"] = "ended"
                vals["end_time"] = fields.Datetime.now()
                self._log_history("ended", note=note or _("Agent disconnected"),
                                  actor=self.partner_id.name or _("Customer"))
        elif status == "error":
            self._log_history("error", note=note or _("Agent reported error"))
        if vals:
            self.write(vals)
        return True

    # ------------------------------------------------------------------ #
    # Step 7 : admin opens the viewer
    # ------------------------------------------------------------------ #
    def action_open_viewer(self):
        self.ensure_one()
        if self.state not in ("agent_connected", "preparing", "connected"):
            raise UserError(_("The temporary agent is not connected yet. "
                              "Wait until the status is 'Agent Connected'."))
        if self.state == "agent_connected":
            self.write({"state": "preparing"})
            self._log_history("preparing", note=_("Administrator opened the "
                                                  "viewer"))
        url = "/remote_assistance/viewer/%s" % self.session_token
        return {
            "type": "ir.actions.act_url",
            "url": url,
            "target": "new",
        }

    # ------------------------------------------------------------------ #
    # End / reset
    # ------------------------------------------------------------------ #
    def action_end_session(self):
        self.ensure_one()
        if self.state in CLOSED_STATES:
            return True
        self.write({"state": "ended", "end_time": fields.Datetime.now()})
        self._log_history("ended", note=_("Ended by administrator"))
        self.message_post(body=_("Session ended by administrator."))
        return True

    def action_reset_to_draft(self):
        self.ensure_one()
        self.write({"state": "draft", "session_token": False,
                    "invite_expiry": False})
        self._log_history("reset", note=_("Reset to draft"))
        return True

    # ------------------------------------------------------------------ #
    # Cron : expire stale invitations / overrun sessions
    # ------------------------------------------------------------------ #
    @api.model
    def _cron_expire_stale(self):
        now = fields.Datetime.now()
        # Un-answered invitations past their expiry.
        stale = self.search([
            ("state", "in", ("sent", "waiting_response")),
            ("invite_expiry", "!=", False),
            ("invite_expiry", "<", now),
        ])
        for rec in stale:
            rec.write({"state": "expired"})
            rec._log_history("expired", note=_("Invitation expired unanswered"))
        # Live sessions that have run well past their agreed duration
        # (safety net; the agent also enforces the limit locally).
        for rec in self.search([("state", "in", OPEN_STATES),
                                 ("start_time", "!=", False)]):
            limit = rec.start_time + timedelta(
                minutes=(rec.duration_minutes or 30) + 5)
            if now > limit:
                rec.write({"state": "ended", "end_time": now})
                rec._log_history("ended", note=_("Duration limit reached"))
        return True

    # ------------------------------------------------------------------ #
    # Token validation used by the relay and public/agent routes
    # ------------------------------------------------------------------ #
    @api.model
    def _session_for_token(self, token):
        if not token:
            return self.browse()
        return self.sudo().search([("session_token", "=", token)], limit=1)

    def _token_is_live(self):
        """True while the relay should accept this token."""
        self.ensure_one()
        return self.state in OPEN_STATES
