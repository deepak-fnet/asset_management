# -*- coding: utf-8 -*-
"""Read-only aggregation layer for the OneIT dashboard.

Everything here is exposed to the JS client action. No writes, and no AD
calls except the optional live-verify in employee_access_lookup.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

STATE_LABELS = [
    ('draft', 'Draft'),
    ('pending', 'Pending Team Lead'),
    ('tl_approved', 'Pending BU Head'),
    ('approved', 'Approved'),
    ('ready_to_delete', 'Ready to Delete'),
    ('done', 'Done'),
    ('rejected', 'Rejected'),
]

TYPE_LABELS = [
    ('onboarding', 'Onboarding'),
    ('offboarding', 'Offboarding'),
    ('team_update', 'Existing User Access'),
]

GROUP_TYPE_LABELS = [
    ('ad_folder', 'AD Folder'),
    ('citrix', 'Citrix'),
    ('file_server', 'File Server'),
]


class OneitDashboard(models.AbstractModel):
    _name = 'oneit.dashboard'
    _description = 'OneIT Dashboard Data'

    # ------------------------------------------------------------------
    # Headline figures
    # ------------------------------------------------------------------
    @api.model
    def get_dashboard_data(self):
        """Everything the dashboard renders on load.

        Counts respect record rules, so an HR user sees figures for their
        own requests while IT sees the whole picture. That is deliberate:
        a dashboard that reports numbers the viewer cannot drill into is
        misleading.
        """
        Request = self.env['oneit.request']
        AdGroup = self.env['oneit.ad.group']
        Team = self.env['oneit.team']
        Person = self.env['oneit.ad.person']

        # --- KPI tiles -------------------------------------------------
        pending_states = ('pending', 'tl_approved')
        kpis = {
            'total_requests': Request.search_count([]),
            'awaiting_approval': Request.search_count(
                [('state', 'in', pending_states)]),
            'awaiting_me': Request.search_count([
                ('state', 'in', pending_states),
                ('approval_ids.approver_id', '=', self.env.uid),
                ('approval_ids.state', '=', 'pending'),
            ]),
            'completed': Request.search_count([('state', '=', 'done')]),
            'rejected': Request.search_count([('state', '=', 'rejected')]),
            'stuck': Request.search_count([
                ('state', 'in', ('approved', 'ready_to_delete'))]),
            'teams': Team.search_count([]),
            'ad_groups': AdGroup.search_count([]),
            'ad_groups_archived': AdGroup.with_context(
                active_test=False).search_count([('active', '=', False)]),
            # Directory side: accounts the sync found, and how many of
            # them OneIT has no record of. The second number is the
            # migration backlog for an existing directory.
            'ad_accounts': Person.search_count([]),
            'ad_unmanaged': Person.search_count([
                ('known_to_odoo', '=', False)]),
            'ad_disabled': Person.search_count([
                ('disabled_in_ad', '=', True)]),
        }

        # --- Requests by state ----------------------------------------
        # Odoo 17 replaced read_group() with _read_group(), which returns
        # a list of tuples instead of a list of dicts: the grouped values
        # first, then the aggregates, in the order requested. No more
        # '<field>_count' keys and no more (id, name) pairs to unpack by
        # position - a many2one group value comes back as a recordset.
        by_state = Request._read_group([], ['state'], ['__count'])
        state_counts = {state: count for state, count in by_state}
        states = [{'key': k, 'label': label,
                   'value': state_counts.get(k, 0)}
                  for k, label in STATE_LABELS]

        # --- Requests by type -----------------------------------------
        by_type = Request._read_group([], ['request_type'], ['__count'])
        type_counts = {rtype: count for rtype, count in by_type}
        types = [{'key': k, 'label': label,
                  'value': type_counts.get(k, 0)}
                 for k, label in TYPE_LABELS]

        # --- AD groups by type ----------------------------------------
        by_group_type = AdGroup._read_group([], ['group_type'], ['__count'])
        gt_counts = {gtype: count for gtype, count in by_group_type}
        group_types = [{'key': k, 'label': label,
                        'value': gt_counts.get(k, 0)}
                       for k, label in GROUP_TYPE_LABELS]

        # --- Requests per team ----------------------------------------
        by_team = Request._read_group([], ['team_id'], ['__count'])
        teams = sorted(
            [{'label': team.display_name if team else _('No team'),
              'value': count} for team, count in by_team],
            key=lambda t: t['value'], reverse=True)[:10]

        # --- Recent activity ------------------------------------------
        recent = Request.search([], order='write_date desc', limit=8)
        recent_rows = [{
            'id': r.id,
            'name': r.name or '',
            'employee': r.employee_name or '',
            'type': dict(TYPE_LABELS).get(r.request_type, r.request_type),
            'state': dict(STATE_LABELS).get(r.state, r.state),
            'state_key': r.state,
            'team': r.team_id.display_name or '',
            'date': fields.Datetime.to_string(r.write_date) or '',
        } for r in recent]

        return {
            'kpis': kpis,
            'states': states,
            'types': types,
            'group_types': group_types,
            'teams': teams,
            'recent': recent_rows,
            'simulate': self.env['oneit.ad.connector']._simulate(),
        }

    # ------------------------------------------------------------------
    # Per-employee access lookup
    # ------------------------------------------------------------------
    @api.model
    def employee_access_lookup(self, employee_code, verify_live=False):
        """Everything known about one person's AD access, from both sides.

        Searches oneit.ad.person FIRST, so an account that exists in AD but
        has never been through OneIT - created by hand before the tool
        existed - is still found. Searching only oneit.request would make
        exactly those people invisible, which defeats the purpose.

        Three sources are reported separately and never merged:

          directory - what the sync found in AD. Authoritative for
                      "what is true now".
          approved  - what OneIT requests granted. Authoritative for
                      "what did we decide".
          live      - a fresh read at lookup time, when asked for.

        Merging them would hide the drift this screen exists to surface.
        """
        code = (employee_code or '').strip()
        if not code:
            return {'found': False,
                    'message': _("Enter a username, name or employee code.")}

        Person = self.env['oneit.ad.person'].with_context(active_test=False)
        Request = self.env['oneit.request']

        person = Person.search(['|', '|',
                                ('username', '=ilike', code),
                                ('name', 'ilike', code),
                                ('email', '=ilike', code)], limit=1)

        requests = Request.search(['|',
                                   ('employee_ref', '=ilike', code),
                                   ('employee_name', 'ilike', code)],
                                  order='create_date asc')
        if person and not requests:
            requests = Request.search(
                [('employee_ref', '=ilike', person.username)],
                order='create_date asc')

        if not person and not requests:
            return {
                'found': False,
                'message': _(
                    "Nothing found for '%s'. No AD account and no OneIT "
                    "request match. If the person exists in Active "
                    "Directory, run the AD sync first."
                ) % code,
            }

        def serialise_groups(groups):
            return [{
                'id': g.id,
                'name': g.name,
                'type': dict(GROUP_TYPE_LABELS).get(g.group_type,
                                                    g.group_type),
                'type_key': g.group_type,
                'dn': g.distinguished_name or '',
                'description': g.description or '',
                'active': g.active,
            } for g in groups.sorted(lambda r: (r.group_type, r.name))]

        # --- what AD actually says --------------------------------------
        directory = []
        directory_groups = self.env['oneit.ad.group'].browse()
        if person:
            for m in person.membership_ids.sorted(
                    lambda r: (r.group_id.group_type, r.group_id.name)):
                directory.append({
                    'id': m.group_id.id,
                    'name': m.group_id.name,
                    'type': dict(GROUP_TYPE_LABELS).get(
                        m.group_id.group_type, m.group_id.group_type),
                    'type_key': m.group_id.group_type,
                    'dn': m.group_id.distinguished_name or '',
                    'description': m.group_id.description or '',
                    'approved_in_odoo': m.approved_in_odoo,
                    'first_seen': fields.Datetime.to_string(
                        m.first_seen_on) or '',
                })
                directory_groups |= m.group_id
            if person.ou_group_id:
                directory.insert(0, {
                    'id': person.ou_group_id.id,
                    'name': person.ou_group_id.name,
                    'type': _('AD Folder'),
                    'type_key': 'ad_folder',
                    'dn': person.ou_group_id.distinguished_name or '',
                    'description': _('Container the account lives in'),
                    'approved_in_odoo': True,
                    'first_seen': '',
                })
                directory_groups |= person.ou_group_id

        # --- what OneIT approved ----------------------------------------
        approved = self.env['oneit.ad.group'].browse()
        history = []
        for req in requests:
            executed = req.state == 'done'
            if executed and req.request_type in ('onboarding', 'team_update'):
                approved = req.ad_group_ids
            elif executed and req.request_type == 'offboarding':
                approved = self.env['oneit.ad.group'].browse()
            history.append({
                'id': req.id,
                'name': req.name or '',
                'type': dict(TYPE_LABELS).get(req.request_type,
                                              req.request_type),
                'type_key': req.request_type,
                'state': dict(STATE_LABELS).get(req.state, req.state),
                'state_key': req.state,
                'executed': executed,
                'date': fields.Datetime.to_string(
                    req.executed_on or req.create_date) or '',
                'groups': [g.name for g in req.ad_group_ids],
                'group_count': len(req.ad_group_ids),
            })

        # --- drift, computed from the mirror (no AD call needed) --------
        dir_names = {d['name'] for d in directory
                     if d['type_key'] != 'ad_folder'}
        app_names = {g.name for g in approved
                     if g.group_type != 'ad_folder'}

        # Counts for the chart come from the DIRECTORY, since that is what
        # the person actually has.
        source = directory if person else [
            {'type_key': g.group_type} for g in approved]
        by_type = {k: sum(1 for d in source if d['type_key'] == k)
                   for k, _l in GROUP_TYPE_LABELS}

        result = {
            'found': True,
            'in_directory': bool(person),
            'employee': {
                'name': (person.name if person
                         else (requests[0].employee_name or '')),
                'username': (person.username if person
                             else (requests[0].employee_ref or '')),
                'email': (person.email if person
                          else (requests[0].employee_email or '')),
                'team': (requests[0].team_id.display_name
                         if requests else ''),
                'ou': (person.ou_dn if person else ''),
                'status': (dict(STATE_LABELS).get(requests[-1].state, '')
                           if requests else _('Not in OneIT')),
                'managed_by_oneit': bool(requests),
                'disabled_in_ad': person.disabled_in_ad if person else False,
                'offboarded': any(
                    r.request_type == 'offboarding' and r.state == 'done'
                    for r in requests),
                'last_synced': (fields.Datetime.to_string(
                    person.last_synced_on) if person else ''),
            },
            'directory': directory,
            'approved': serialise_groups(approved),
            'by_type': [{'key': k, 'label': label, 'value': by_type.get(k, 0)}
                        for k, label in GROUP_TYPE_LABELS],
            'drift': {
                'in_ad_not_approved': sorted(dir_names - app_names),
                'approved_not_in_ad': sorted(app_names - dir_names),
            },
            'history': history,
            'request_count': len(requests),
            'live': None,
            'live_error': None,
        }

        # --- optional fresh read ----------------------------------------
        username = result['employee']['username']
        if verify_live and username:
            connector = self.env['oneit.ad.connector']
            if connector._simulate():
                result['live_error'] = _(
                    "Simulation mode is on, so Active Directory was not "
                    "contacted. The Directory section above still reflects "
                    "the last sync.")
            else:
                try:
                    member_of = connector.get_user_groups(username)
                    AdGroup = self.env['oneit.ad.group'].with_context(
                        active_test=False)
                    matched, unmapped = [], []
                    for dn in member_of:
                        group = AdGroup.search(
                            [('distinguished_name', '=ilike', dn)], limit=1)
                        if group:
                            matched.append({
                                'name': group.name,
                                'type': dict(GROUP_TYPE_LABELS).get(
                                    group.group_type, group.group_type),
                            })
                        else:
                            unmapped.append(dn)
                    live_names = {m['name'] for m in matched}
                    result['live'] = {
                        'matched': matched,
                        'unmapped': unmapped,
                        'stale_mirror': sorted(
                            live_names.symmetric_difference(dir_names)),
                    }
                except Exception as exc:
                    _logger.exception("Live access lookup failed")
                    result['live_error'] = _(
                        "Could not read Active Directory: %s") % exc

        return result

    @api.model
    def group_member_lookup(self, group_name):
        """The reverse view: who is in this group.

        Answers 'show me everyone in ENG-Citrix-DevTools' directly from
        the mirror, and flags which members OneIT never approved.
        """
        term = (group_name or '').strip()
        if not term:
            return {'found': False, 'message': _("Enter a group name.")}

        AdGroup = self.env['oneit.ad.group'].with_context(active_test=False)
        groups = AdGroup.search(['|',
                                 ('name', 'ilike', term),
                                 ('distinguished_name', 'ilike', term)],
                                limit=25)
        if not groups:
            return {'found': False,
                    'message': _("No AD group matches '%s'.") % term}

        out = []
        for group in groups:
            members = []
            for m in group.membership_ids.sorted(
                    lambda r: r.person_id.name or r.person_id.username or ''):
                members.append({
                    'username': m.person_id.username or '',
                    'name': m.person_id.name or '',
                    'approved_in_odoo': m.approved_in_odoo,
                    'known_to_odoo': m.person_id.known_to_odoo,
                    'disabled': m.person_id.disabled_in_ad,
                })
            out.append({
                'id': group.id,
                'name': group.name,
                'type': dict(GROUP_TYPE_LABELS).get(group.group_type,
                                                    group.group_type),
                'type_key': group.group_type,
                'dn': group.distinguished_name or '',
                'active': group.active,
                'members': members,
                'member_count': len(members),
                'unmanaged_count': sum(1 for m in members
                                       if not m['known_to_odoo']),
            })
        return {'found': True, 'groups': out, 'match_count': len(out)}

    # ------------------------------------------------------------------
    # Drill-down
    # ------------------------------------------------------------------
    @api.model
    def action_open_requests(self, domain=None):
        """Open the request list filtered to whatever tile was clicked."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Requests'),
            'res_model': 'oneit.request',
            'view_mode': 'list,form',
            'domain': domain or [],
            'target': 'current',
        }
