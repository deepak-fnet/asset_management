# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class OneitTeam(models.Model):
    """A Team is the unit of access.

    Each team maps to a set of AD groups (one AD Folder plus any number of
    Citrix / File Server groups) and carries its own approvers: the Team
    Lead(s) who approve first and select the access, and the BU Head(s) who
    give final approval.
    """
    _name = 'oneit.team'
    _description = 'OneIT Team'
    _order = 'name'

    name = fields.Char(string='Team', required=True)
    code = fields.Char(string='Code')
    description = fields.Char(string='Description')
    active = fields.Boolean(default=True)

    ad_group_ids = fields.Many2many(
        'oneit.ad.group', 'oneit_team_ad_group_rel', 'team_id', 'ad_group_id',
        string='AD Groups',
        help="The full access footprint available to this team. A Team Lead "
             "picks from this list when approving a request.")
    team_lead_ids = fields.Many2many(
        'res.users', 'oneit_team_lead_rel', 'team_id', 'user_id',
        string='Team Leads',
        help="First approvers. They select the exact AD access to grant.")
    bu_head_ids = fields.Many2many(
        'res.users', 'oneit_team_bu_head_rel', 'team_id', 'user_id',
        string='BU Heads',
        help="Final approvers. Their approval triggers execution.")

    ad_folder_group_id = fields.Many2one(
        'oneit.ad.group', string='AD Folder (OU)',
        compute='_compute_ad_folder_group_id', store=False,
        help="The OU new users of this team are created in.")
    request_ids = fields.One2many(
        'oneit.request', 'team_id', string='Requests')
    request_count = fields.Integer(
        string='Requests', compute='_compute_request_count')

    _sql_constraints = [
        ('name_uniq', 'unique(name)', 'A team with this name already exists.'),
    ]

    @api.depends('ad_group_ids', 'ad_group_ids.group_type')
    def _compute_ad_folder_group_id(self):
        for team in self:
            folders = team.ad_group_ids.filtered(
                lambda g: g.group_type == 'ad_folder')
            team.ad_folder_group_id = folders[:1]

    @api.depends('request_ids')
    def _compute_request_count(self):
        # Odoo 17: _read_group returns tuples of (grouped values...,
        # aggregates...). A many2one group value arrives as a recordset,
        # so team.id replaces the old d['team_id'][0] unpacking.
        data = self.env['oneit.request']._read_group(
            [('team_id', 'in', self.ids)], ['team_id'], ['__count'])
        mapped = {team.id: count for team, count in data}
        for team in self:
            team.request_count = mapped.get(team.id, 0)

    @api.constrains('ad_group_ids')
    def _check_single_ad_folder(self):
        for team in self:
            folders = team.ad_group_ids.filtered(
                lambda g: g.group_type == 'ad_folder')
            if len(folders) > 1:
                raise ValidationError(_(
                    "Team '%s' has %s AD Folder groups. A team must have "
                    "exactly one AD Folder, because that is the single OU the "
                    "user object is created in."
                ) % (team.name, len(folders)))

    def action_view_requests(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Requests - %s') % self.name,
            'res_model': 'oneit.request',
            'view_mode': 'list,form',
            'domain': [('team_id', '=', self.id)],
            'context': {'default_team_id': self.id},
        }
