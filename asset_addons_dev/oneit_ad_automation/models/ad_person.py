# -*- coding: utf-8 -*-
"""Directory-side mirror of who exists in AD and what they belong to.

This is deliberately SEPARATE from oneit.request. A request records what
Odoo decided; these models record what Active Directory actually contains.
Merging them would destroy the only signal that matters here - the
difference between the two.

Nothing in this file writes to AD. It is a read-only mirror refreshed by
the sync.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


def dn_leaf(dn):
    """'CN=vicky.r,OU=Engineering,...' -> 'vicky.r'."""
    if not dn:
        return ''
    first = dn.split(',')[0]
    return first.split('=', 1)[1] if '=' in first else first


def dn_parent(dn):
    """The container a DN sits in: everything after the leading component."""
    if not dn:
        return ''
    return ','.join(dn.split(',')[1:])


class OneitAdPerson(models.Model):
    """A user account discovered in Active Directory.

    One row per AD account, whether or not Odoo has ever seen the person.
    Vicky - created by hand in AD, never onboarded through Odoo - gets a
    row here and that is the whole point.
    """
    _name = 'oneit.ad.person'
    _description = 'AD Account'
    _order = 'name'
    _rec_name = 'username'

    username = fields.Char(
        string='AD Username', required=True, index=True,
        help="sAMAccountName - the identifier used to log in to Windows.")
    name = fields.Char(string='Full Name')
    # display_name is built in from Odoo 17 onward - overriding its
    # compute is the supported way to change how a record labels itself.
    # Declaring a separate Char of the same name would shadow the base
    # field and break name_search.
    email = fields.Char(string='Email')
    distinguished_name = fields.Char(
        string='Distinguished Name', required=True, index=True)
    ou_dn = fields.Char(
        string='Container (OU)', compute='_compute_ou', store=True,
        help="The OU the account object lives in.")
    ou_group_id = fields.Many2one(
        'oneit.ad.group', string='AD Folder', ondelete='set null',
        help="The matching AD Folder record, when the OU has been synced.")
    disabled_in_ad = fields.Boolean(string='Disabled in AD', default=False)

    membership_ids = fields.One2many(
        'oneit.ad.membership', 'person_id', string='Memberships')
    membership_count = fields.Integer(
        compute='_compute_membership_count', string='Groups')

    # --- reconciliation against Odoo's own records -------------------
    request_ids = fields.One2many(
        'oneit.request', 'ad_person_id', string='OneIT Requests')
    known_to_odoo = fields.Boolean(
        string='Managed by OneIT', compute='_compute_known_to_odoo',
        store=True, help="False means this account exists in Active "
                         "Directory but was never created or touched "
                         "through OneIT - it predates the tool, or was "
                         "made by hand.")

    first_seen_on = fields.Datetime(string='First Seen', readonly=True)
    last_synced_on = fields.Datetime(string='Last Seen in AD', readonly=True)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('dn_uniq', 'unique(distinguished_name)',
         'An AD account with this distinguished name already exists.'),
    ]

    @api.depends('username', 'name')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = ('%s (%s)' % (rec.name, rec.username)
                                if rec.name else rec.username or '')

    @api.depends('distinguished_name')
    def _compute_ou(self):
        for rec in self:
            rec.ou_dn = dn_parent(rec.distinguished_name)

    @api.depends('membership_ids')
    def _compute_membership_count(self):
        for rec in self:
            rec.membership_count = len(rec.membership_ids)

    @api.depends('username')
    def _compute_known_to_odoo(self):
        Request = self.env['oneit.request']
        for rec in self:
            rec.known_to_odoo = bool(rec.username and Request.search_count([
                ('employee_ref', '=ilike', rec.username)]))

    def action_view_memberships(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Memberships: %s') % self.display_name,
            'res_model': 'oneit.ad.membership',
            'view_mode': 'list',
            'domain': [('person_id', '=', self.id)],
        }


class OneitAdMembership(models.Model):
    """One person's membership of one AD group, as found in the directory.

    A join model rather than a many2many so each row can carry its own
    provenance - when it was first seen, and whether Odoo ever approved
    it. That is what makes "granted outside OneIT" answerable.
    """
    _name = 'oneit.ad.membership'
    _description = 'AD Group Membership'
    _order = 'group_id, person_id'

    group_id = fields.Many2one(
        'oneit.ad.group', string='AD Group', required=True,
        ondelete='cascade', index=True)
    person_id = fields.Many2one(
        'oneit.ad.person', string='Account', required=True,
        ondelete='cascade', index=True)

    group_type = fields.Selection(
        related='group_id.group_type', string='Type', store=True)
    member_dn = fields.Char(
        string='Member DN', required=True,
        help="The DN exactly as AD returned it in the group's member "
             "attribute.")

    approved_in_odoo = fields.Boolean(
        string='Approved in OneIT', compute='_compute_approved_in_odoo',
        store=True,
        help="True when a completed OneIT request granted this group to "
             "this person. False means it was granted directly in AD.")

    first_seen_on = fields.Datetime(string='First Seen', readonly=True)
    last_synced_on = fields.Datetime(string='Last Seen in AD', readonly=True)

    _sql_constraints = [
        ('person_group_uniq', 'unique(person_id, group_id)',
         'This account is already recorded as a member of that group.'),
    ]

    @api.depends('person_id.username', 'group_id')
    def _compute_approved_in_odoo(self):
        Request = self.env['oneit.request']
        for rec in self:
            if not rec.person_id.username or not rec.group_id:
                rec.approved_in_odoo = False
                continue
            rec.approved_in_odoo = bool(Request.search_count([
                ('employee_ref', '=ilike', rec.person_id.username),
                ('state', '=', 'done'),
                ('ad_group_ids', 'in', rec.group_id.id),
            ]))
