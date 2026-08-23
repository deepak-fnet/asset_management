# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AssetHelpdeskTeam(models.Model):
    _name = 'asset.helpdesk.team'
    _description = 'Asset Helpdesk Team'
    _order = 'name'

    name = fields.Char(string='Team Name', required=True)
    active = fields.Boolean(string='Active', default=True)
    leader_id = fields.Many2one(
        'res.users',
        string='Team Leader',
    )
    member_ids = fields.Many2many(
        'res.users',
        string='Members',
    )
    description = fields.Text(string='Description')
    color = fields.Integer(string='Color Index')
    member_count = fields.Integer(
        string='Number of Members',
        compute='_compute_member_count',
        store=True,
    )

    @api.depends('member_ids')
    def _compute_member_count(self):
        for team in self:
            team.member_count = len(team.member_ids)
