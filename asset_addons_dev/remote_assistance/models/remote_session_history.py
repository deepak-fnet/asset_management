# -*- coding: utf-8 -*-
from odoo import fields, models


class RemoteAssistanceHistory(models.Model):
    _name = "remote.assistance.history"
    _description = "Remote Assistance Session History"
    _order = "create_date desc, id desc"

    session_id = fields.Many2one(
        "remote.assistance.session", string="Session", required=True,
        ondelete="cascade", index=True)
    timestamp = fields.Datetime(
        string="Timestamp", default=fields.Datetime.now, required=True)
    action = fields.Char(string="Action", required=True)
    actor = fields.Char(string="Actor")
    note = fields.Char(string="Note")
