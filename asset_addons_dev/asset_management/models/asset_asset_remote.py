# -*- coding: utf-8 -*-
from datetime import timedelta
import secrets

from odoo import fields, models, _


class AssetAssetRemote(models.Model):
    _inherit = "asset.asset"

    remote_session_ids = fields.One2many(
        "asset.remote.session", "asset_id", string="Remote Sessions")
    remote_session_count = fields.Integer(compute="_compute_remote_count")

    def _compute_remote_count(self):
        # Odoo 19: read_group() removed. _read_group() returns tuples where the
        # groupby value is a recordset (not an (id, name) pair).
        data = self.env["asset.remote.session"]._read_group(
            [("asset_id", "in", self.ids)],
            groupby=["asset_id"],
            aggregates=["__count"],
        )
        counts = {asset.id: count for asset, count in data}
        for rec in self:
            rec.remote_session_count = counts.get(rec.id, 0)

    def action_request_remote(self):
        """Create a remote-assistance request. The installed agent (polling by
        serial number) shows an Accept/Reject dialog on the machine."""
        Session = self.env["asset.remote.session"].sudo()
        duration = int(self.env["ir.config_parameter"].sudo().get_param(
            "asset_remote.default_minutes", "30") or 30)
        now = fields.Datetime.now()
        created = self.env["asset.remote.session"]
        skipped = self.env["asset.asset"]
        for rec in self:
            if not rec.serial_number:
                skipped |= rec
                continue
            open_sessions = Session.search([
                ("asset_id", "=", rec.id),
                ("state", "in", ("requested", "accepted", "agent_connected",
                                 "connected"))])
            # Reuse only a still-pending request the machine hasn't answered
            # yet (avoids duplicate popups on an accidental double-click).
            pending = open_sessions.filtered(
                lambda s: s.state == "requested"
                and (not s.expiry or s.expiry > now))
            if pending:
                created |= pending[0]
                # any other stale/stuck sessions get closed
                stale = open_sessions - pending[0]
            else:
                stale = open_sessions
            # Force-close stuck sessions (e.g. a half-open "agent_connected"
            # the user never actually accepted, or one the customer closed)
            for s in stale:
                s.write({"state": "ended", "end_time": now})
                s._log("ended", note=_("Superseded by a new remote request"))
            if pending:
                continue
            created |= Session.create({
                "asset_id": rec.id,
                "session_token": secrets.token_urlsafe(32),
                "admin_user_id": self.env.user.id,
                "duration_minutes": duration,
                "requested_on": now,
                "expiry": now + timedelta(minutes=15),
                "state": "requested",
            })
        for s in created:
            if s.requested_on and s.state == "requested":
                s._log("requested", note=_("Remote session requested"))
                s.message_post(body=_("Remote assistance requested by %s.")
                               % self.env.user.name)
        if skipped:
            msg = (_("%d asset(s) have no serial number and were skipped "
                     "(the agent identifies machines by serial).")
                   % len(skipped))
            mtype = "warning"
        else:
            msg = (_("Remote request sent to %d machine(s). The user will be "
                     "asked to accept on their screen.") % len(created))
            mtype = "success"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": _("Remote Assistance"), "message": msg,
                       "type": mtype, "sticky": False},
        }

    def action_view_remote_sessions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Remote Sessions"),
            "res_model": "asset.remote.session",
            "view_mode": "list,form",
            "domain": [("asset_id", "=", self.id)],
            "context": {"default_asset_id": self.id},
        }