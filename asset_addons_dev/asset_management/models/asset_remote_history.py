# -*- coding: utf-8 -*-
from odoo import fields, models


class AssetRemoteHistory(models.Model):
    _name = "asset.remote.history"
    _description = "Asset Remote Session History"
    _order = "create_date desc, id desc"

    session_id = fields.Many2one("asset.remote.session", required=True,
                                 ondelete="cascade", index=True)
    timestamp = fields.Datetime(default=fields.Datetime.now, required=True)
    action = fields.Char(required=True)
    actor = fields.Char()
    note = fields.Char()
