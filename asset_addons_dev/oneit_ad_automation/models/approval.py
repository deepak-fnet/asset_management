# -*- coding: utf-8 -*-
from odoo import fields, models

APPROVER_ROLES = [
    ('team_lead', 'Team Lead'),
    ('bu_head', 'BU Head'),
]

APPROVAL_STATES = [
    ('pending', 'Pending'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('skipped', 'Skipped'),
]


class OneitApproval(models.Model):
    """One approval line per approver per request.

    Created when a request is submitted: one line for each Team Lead and each
    BU Head of the target team. A stage is cleared as soon as any one approver
    of that stage acts on it.
    """
    _name = 'oneit.approval'
    _description = 'OneIT Request Approval'
    _order = 'request_id, approver_role, id'
    _rec_name = 'approver_id'

    request_id = fields.Many2one(
        'oneit.request', string='Request', required=True,
        ondelete='cascade', index=True)
    approver_id = fields.Many2one(
        'res.users', string='Approver', required=True, index=True)
    approver_role = fields.Selection(
        APPROVER_ROLES, string='Role', required=True)
    state = fields.Selection(
        APPROVAL_STATES, string='Status', default='pending', required=True)
    comment = fields.Text(string='Comment')
    acted_on = fields.Datetime(string='Acted On', readonly=True)

    request_type = fields.Selection(
        related='request_id.request_type', string='Request Type', store=False)
    request_state = fields.Selection(
        related='request_id.state', string='Request Status', store=False)
    employee_name = fields.Char(
        related='request_id.employee_name', string='Employee', store=False)
    team_id = fields.Many2one(
        related='request_id.team_id', string='Team', store=False)
