"""Numbers for the DMS dashboards: one for every member, one for the Document Controller.

Unusual activity is worked out from the audit log each time the dashboard opens, so there
is nothing to schedule. Thresholds are system parameters (DMS settings).
"""
import json
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models, _

DEFAULT_TZ = 'Asia/Kolkata'

# audit-log actions folded into the groups the charts colour (fixed order = fixed colour)
ACTION_GROUPS = [
    ('view', "Views", ('view',)),
    ('download', "Downloads", ('download',)),
    ('upload', "Uploads", ('upload', 'checkin', 'checkout', 'unlock', 'force_unlock')),
    ('approval', "Approvals", ('submit', 'approve', 'refuse', 'publish', 'cutoff', 'archive')),
    ('access', "Access", ('access_grant', 'access_revoke', 'login')),
    ('ai', "AI", ('ai_check', 'ai_search')),
]
GROUP_OF = {a: g for g, _l, acts in ACTION_GROUPS for a in acts}


class DmsDashboard(models.AbstractModel):
    _name = 'dms.dashboard'
    _description = 'DMS Dashboard'

    # ------------------------------------------------------------------ helpers
    def _param(self, key, default):
        try:
            return int(self.env['ir.config_parameter'].sudo().get_param(key) or default)
        except ValueError:
            return default

    def _tz(self, user=None):
        try:
            return pytz.timezone((user or self.env.user).tz or DEFAULT_TZ)
        except pytz.UnknownTimeZoneError:
            return pytz.timezone(DEFAULT_TZ)

    def _days(self, n):
        """The last n local days, oldest first, as (date, utc_start, utc_end)."""
        tz = self._tz()
        today = datetime.now(tz).date()
        out = []
        for i in range(n - 1, -1, -1):
            d = today - timedelta(days=i)
            start = tz.localize(datetime.combine(d, time.min)).astimezone(pytz.utc).replace(tzinfo=None)
            out.append((d, start, start + timedelta(days=1)))
        return out

    def _series(self, domain, n=30, actions=('upload', 'view', 'download')):
        """Daily counts of log actions over the last n days."""
        days = self._days(n)
        Log = self.env['dms.log'].sudo()
        logs = Log.search_read(domain + [('date', '>=', days[0][1]), ('action', 'in', list(actions))], ['date', 'action'])
        tz = self._tz()
        idx = {d: i for i, (d, _s, _e) in enumerate(days)}
        series = {a: [0] * n for a in actions}
        for l in logs:
            d = pytz.utc.localize(l['date']).astimezone(tz).date()
            if d in idx:
                series[l['action']][idx[d]] += 1
        return {'labels': [d.strftime('%d %b') for d, _s, _e in days], 'series': series}

    def _heatmap(self, domain, n=7):
        """Activity per local hour for the last n days: rows = days (oldest first), cols = 0..23."""
        days = self._days(n)
        tz = self._tz()
        idx = {d: i for i, (d, _s, _e) in enumerate(days)}
        grid = [[0] * 24 for _d in days]
        for l in self.env['dms.log'].sudo().search_read(domain + [('date', '>=', days[0][1])], ['date']):
            loc = pytz.utc.localize(l['date']).astimezone(tz)
            if loc.date() in idx:
                grid[idx[loc.date()]][loc.hour] += 1
        return {'days': [d.strftime('%a %d') for d, _s, _e in days], 'sunday': [d.weekday() == 6 for d, _s, _e in days],
                'grid': grid, 'max': max([max(r) for r in grid] + [1]),
                'work_from': self._param('dms.alert_work_from', 8), 'work_to': self._param('dms.alert_work_to', 20)}

    def _mix(self, domain, n=30):
        """How many of each kind of action in the last n days."""
        since = fields.Datetime.now() - timedelta(days=n)
        counts = Counter()
        for action, cnt in self.env['dms.log'].sudo()._read_group(domain + [('date', '>=', since)], ['action'], ['__count']):
            counts[GROUP_OF.get(action, 'other')] += cnt
        out = [{'key': g, 'label': label, 'count': counts.get(g, 0)} for g, label, _a in ACTION_GROUPS]
        out.append({'key': 'other', 'label': "Other", 'count': counts.get('other', 0)})
        return out

    def _people(self, n=30, limit=7):
        """Busiest people, each with their mix of actions."""
        since = fields.Datetime.now() - timedelta(days=n)
        per = defaultdict(Counter)
        for user, action, cnt in self.env['dms.log'].sudo()._read_group(
                [('date', '>=', since)], ['user_id', 'action'], ['__count']):
            per[user][GROUP_OF.get(action, 'other')] += cnt
        rows = sorted(per.items(), key=lambda kv: -sum(kv[1].values()))[:limit]
        keys = [g for g, _l, _a in ACTION_GROUPS] + ['other']
        return [{'user_id': u.id, 'name': u.name or '?', 'total': sum(c.values()),
                 'parts': [{'key': k, 'count': c.get(k, 0)} for k in keys]} for u, c in rows]

    def _doc_row(self, d, extra=None):
        row = {'id': d.id, 'code': d.code or '', 'name': d.name, 'folder': d.folder_id.complete_name or '',
               'state': d.state, 'owner': d.owner_id.name or ''}
        row.update(extra or {})
        return row

    def _rule_failures(self, revisions, limit=6):
        fails, checked = Counter(), 0
        for r in revisions:
            try:
                results = json.loads(r.ai_results or '[]')
            except ValueError:
                continue
            checked += 1
            for x in results:
                if not x.get('passed'):
                    fails[x.get('rule') or '?'] += 1
        return checked, [{'rule': k, 'count': v} for k, v in fails.most_common(limit)]

    # ------------------------------------------------------------------ member
    @api.model
    def user_data(self):
        user = self.env.user
        Doc = self.env['dms.document'].sudo()
        Rev = self.env['dms.revision'].sudo()
        Approval = self.env['dms.approval'].sudo()
        now = fields.Datetime.now()
        today = fields.Date.context_today(self)

        mine = Doc.search([('owner_id', '=', user.id), ('state', '!=', 'discarded')])
        waiting = Approval.search([('state', '=', 'waiting'), ('revision_id.state', '=', 'in_review')])
        to_approve = waiting.filtered(
            lambda a: a.user_id == user and a == a.revision_id.approval_ids.filtered(
                lambda x: x.state == 'waiting').sorted('sequence')[:1])
        my_revs = Rev.search([('document_id.owner_id', '=', user.id), ('ai_state', '=', 'done'),
                              ('create_date', '>=', now - timedelta(days=90))])
        ai_fixes = my_revs.filtered(lambda r: r.ai_blocked and r.state in ('draft', 'in_review'))
        _checked, my_fails = self._rule_failures(my_revs, 5)
        cutoff_soon = Rev.search([('document_id.owner_id', '=', user.id), ('state', '=', 'superseded'),
                                  ('cutoff_date', '<=', today + timedelta(days=14))], order='cutoff_date')
        requests = self.env['dms.access.request'].sudo().search([('user_id', '=', user.id)], limit=6)
        folders = self.env['dms.folder']._visible_folders(user)
        fresh = Doc.search([('folder_id', 'in', folders.ids), ('state', '=', 'published'),
                            ('owner_id', '!=', user.id)], order='write_date desc', limit=6)
        fresh = fresh.filtered(lambda d: d._access_for_user(user))
        activity = self.env['dms.log'].sudo().search([('user_id', '=', user.id)], limit=40)

        exp_days = self._param('dms.expiry_alert_days', 30)
        expiring = mine.filtered(lambda d: d.expiry_date and d.expiry_date <= today + timedelta(days=exp_days)
                                 and d.state != 'archived').sorted('expiry_date')
        return {
            'expiring': [self._doc_row(d, {'days': (d.expiry_date - today).days, 'what': d.expiry_label or ''})
                         for d in expiring[:8]],
            'kpis': {
                'my_docs': len(mine),
                'published': len(mine.filtered(lambda d: d.state == 'published')),
                'in_review': len(mine.filtered(lambda d: d.state == 'in_review')),
                'rejected': len(mine.filtered(lambda d: d.state == 'rejected')),
                'to_approve': len(to_approve),
                'checked_out': Doc.search_count([('locked_by_id', '=', user.id)]),
                'starred': Doc.search_count([('favorite_user_ids', 'in', user.id)]),
            },
            'trend': self._series([('user_id', '=', user.id)], 14),
            'to_approve': [self._doc_row(a.revision_id.document_id, {
                'rev': a.revision_id.rev_label, 'level': (a.level or '').upper(),
                'since': fields.Datetime.to_string(a.revision_id.submitted_date) if a.revision_id.submitted_date else False,
                'days': (now - a.revision_id.submitted_date).days if a.revision_id.submitted_date else 0,
            }) for a in to_approve.sorted(lambda a: a.revision_id.submitted_date or now)[:8]],
            'ai_fixes': [self._doc_row(r.document_id, {
                'failed': [x['rule'] for x in json.loads(r.ai_results or '[]') if not x.get('passed') and x.get('blocking')][:3],
            }) for r in ai_fixes[:6]],
            'my_fails': my_fails,
            'cutoff_soon': [self._doc_row(r.document_id, {'rev': r.rev_label, 'cutoff': fields.Date.to_string(r.cutoff_date)})
                            for r in cutoff_soon[:6]],
            'requests': [{'id': q.id, 'doc': q.document_id.display_name, 'doc_id': q.document_id.id,
                          'level': q.level, 'state': q.state, 'date': fields.Datetime.to_string(q.create_date)}
                         for q in requests],
            'fresh': [self._doc_row(d, {'date': fields.Datetime.to_string(d.write_date)}) for d in fresh],
            'activity': self._log_rows(activity),
            'by_state': self._count_by(mine, 'state'),
            'heatmap': self._heatmap([('user_id', '=', user.id)]),
            'mix': self._mix([('user_id', '=', user.id)]),
            'expiry_window': exp_days,
        }

    def _count_by(self, docs, field):
        labels = dict(docs._fields[field].selection)
        c = Counter(docs.mapped(field))
        return [{'key': k, 'label': labels.get(k, k), 'count': v} for k, v in c.most_common()]

    def _log_rows(self, logs):
        labels = dict(self.env['dms.log']._fields['action']._description_selection(self.env))
        return [{'id': l.id, 'date': fields.Datetime.to_string(l.date), 'user': l.user_id.name or '',
                 'action': l.action, 'label': labels.get(l.action, l.action),
                 'doc': l.document_ref or (l.folder_id.complete_name or ''), 'doc_id': l.document_id.id or False,
                 'details': l.details or ''} for l in logs]

    # ------------------------------------------------------------------ controller
    @api.model
    def admin_data(self):
        Doc = self.env['dms.document'].sudo()
        Rev = self.env['dms.revision'].sudo()
        Log = self.env['dms.log'].sudo()
        now = fields.Datetime.now()
        today = fields.Date.context_today(self)
        month_start = self._days(today.day)[0][1]

        live = Doc.search([('state', 'not in', ('discarded',))])
        stored = Rev.search([('storage_path', '!=', False), ('state', 'not in', ('disposed', 'dropped'))])

        # approval speed: submitted → approved over the last 90 days
        approved = Rev.search([('approved_date', '>=', today - timedelta(days=90)), ('submitted_date', '!=', False)])
        waits = [max((r.approved_date - r.submitted_date.date()).days, 0) for r in approved]
        in_review = Rev.search([('state', '=', 'in_review')])
        overdue_days = self._param('dms.alert_approval_days', 3)
        overdue = in_review.filtered(lambda r: r.submitted_date and (now - r.submitted_date).days >= overdue_days)

        # departments = top folders
        dept = defaultdict(int)
        for d in live:
            f = d.folder_id
            while f.parent_id:
                f = f.parent_id
            dept[f.name] += 1

        checked, top_fails = self._rule_failures(Rev.search([('ai_state', '=', 'done'),
                                                              ('create_date', '>=', now - timedelta(days=90))]))
        blocked = Rev.search_count([('ai_blocked', '=', True), ('create_date', '>=', now - timedelta(days=90))])

        week = now - timedelta(days=7)
        users = [{'name': u.name or '?', 'count': n} for u, n in Log._read_group(
            [('date', '>=', now - timedelta(days=30))], ['user_id'], ['__count'], order='__count desc', limit=6)]

        stale_days = self._param('dms.alert_checkout_days', 7)
        stale = Doc.search([('locked_by_id', '!=', False), ('locked_date', '<=', now - timedelta(days=stale_days))])
        retention = Rev.search_count([('state', '=', 'archived'), ('retention_end', '<=', today)])

        exp_days = self._param('dms.expiry_alert_days', 30)
        expiring = live.filtered(lambda d: d.expiry_date and d.expiry_date <= today + timedelta(days=exp_days)
                                 and d.state != 'archived').sorted('expiry_date')
        sens_revs = Rev.search([('has_sensitive', '=', True), ('document_id', 'in', live.ids),
                                ('state', 'not in', ('dropped', 'disposed', 'superseded', 'archived'))], order='id desc')
        dup_pairs, seen = [], set()
        for r in Rev.search([('duplicates_json', 'not in', (False, '[]')), ('document_id', 'in', live.ids)], order='id desc'):
            for x in json.loads(r.duplicates_json or '[]'):
                key = tuple(sorted((r.document_id.id, x['doc_id'])))
                if key in seen or x['doc_id'] not in live.ids:
                    continue
                seen.add(key)
                dup_pairs.append(self._doc_row(r.document_id, {
                    'also': '%s %s' % (x['code'], x['name']), 'pct': int(x['score'] * 100), 'same': x['same']}))
        return {
            'expiring': [self._doc_row(d, {'days': (d.expiry_date - today).days, 'what': d.expiry_label or ''})
                         for d in expiring[:8]],
            'sensitive': [self._doc_row(r.document_id, {
                'found': ', '.join('%s ×%s' % (x['label'], x['count']) for x in json.loads(r.sensitive_json or '[]'))})
                for r in sens_revs[:8]],
            'dups': dup_pairs[:8],
            'kpis': {
                'docs': len(live),
                'published': len(live.filtered(lambda d: d.state == 'published')),
                'in_review': len(in_review),
                'uploads_month': Log.search_count([('action', '=', 'upload'), ('date', '>=', month_start)]),
                'storage': sum(stored.mapped('file_size')),
                'active_users': len(set(Log.search([('date', '>=', week)]).user_id.ids)),
                'avg_approval': round(sum(waits) / len(waits), 1) if waits else 0,
                'access_requests': self.env['dms.access.request'].sudo().search_count([('state', '=', 'pending')]),
                'disposals': self.env['dms.disposal'].sudo().search_count([('state', '=', 'submitted')]),
                'retention_due': retention,
                'expiring': len(expiring), 'sensitive': len(sens_revs.document_id), 'dups': len(dup_pairs),
                'ai_checked': checked, 'ai_blocked': blocked,
            },
            'trend': self._series([], 30),
            'by_dept': sorted([{'label': k, 'count': v} for k, v in dept.items()], key=lambda x: -x['count']),
            'by_type': [{'label': t.name, 'count': n} for t, n in
                        Doc._read_group([('id', 'in', live.ids)], ['type_id'], ['__count'], order='__count desc')],
            'by_state': self._count_by(live, 'state'),
            'top_fails': top_fails,
            'overdue': [self._doc_row(r.document_id, {
                'rev': r.rev_label, 'days': (now - r.submitted_date).days,
                'with': ', '.join(r.approval_ids.filtered(lambda a: a.state == 'waiting').sorted('sequence')[:1].user_id.mapped('name')),
            }) for r in overdue.sorted('submitted_date')[:8]],
            'stale': [self._doc_row(d, {'by': d.locked_by_id.name, 'days': (now - d.locked_date).days}) for d in stale[:6]],
            'users': users,
            'alerts': self.alerts(),
            'activity': self._log_rows(Log.search([], limit=60)),
            'heatmap': self._heatmap([]),
            'mix': self._mix([]),
            'people': self._people(),
            'expiry_window': exp_days,
        }

    # ------------------------------------------------------------------ unusual activity
    @api.model
    def alerts(self, days=7):
        """Patterns in the audit log worth a second look. Newest first."""
        Log = self.env['dms.log'].sudo()
        now = fields.Datetime.now()
        since = now - timedelta(days=days)
        logs = Log.search([('date', '>=', since)], order='date')
        bulk_dl = self._param('dms.alert_bulk_downloads', 20)
        bulk_view = self._param('dms.alert_bulk_views', 60)
        day_from = self._param('dms.alert_work_from', 8)
        day_to = self._param('dms.alert_work_to', 20)
        out = []

        per_day = defaultdict(lambda: defaultdict(list))   # (user, local day) -> action -> logs
        for l in logs:
            tz = self._tz(l.user_id)
            local = pytz.utc.localize(l.date).astimezone(tz)
            per_day[(l.user_id, local.date())][l.action].append((l, local))

        for (user, day), acts in per_day.items():
            name = user.name or '?'
            when = day.strftime('%d %b')
            last = max(l.date for ls in acts.values() for l, _loc in ls)

            def add(kind, level, title, detail, count, ref_logs, limit=0):
                docs = {l.document_ref for l, _loc in ref_logs if l.document_ref}
                out.append({'kind': kind, 'level': level, 'title': title, 'detail': detail, 'user': name,
                            'user_id': user.id, 'count': count, 'limit': limit, 'day': when,
                            'date': fields.Datetime.to_string(last),
                            'hours': sorted(round(loc.hour + loc.minute / 60, 2) for _l, loc in ref_logs),
                            'work_from': day_from, 'work_to': day_to,
                            'docs': sorted(docs)[:5], 'more': max(len(docs) - 5, 0)})

            dl = acts.get('download', [])
            if len(dl) >= bulk_dl:
                add('bulk_download', 'high', _("Bulk download"),
                    _("%(u)s downloaded %(n)s files on %(d)s", u=name, n=len(dl), d=when), len(dl), dl, bulk_dl)
            vw = acts.get('view', [])
            if len(vw) >= bulk_view:
                add('bulk_view', 'medium', _("Many files opened"),
                    _("%(u)s opened %(n)s files on %(d)s", u=name, n=len(vw), d=when), len(vw), vw, bulk_view)
            odd = [(l, loc) for a in ('view', 'download', 'upload') for l, loc in acts.get(a, [])
                   if loc.hour < day_from or loc.hour >= day_to or loc.weekday() == 6]
            if odd:
                times = sorted({loc.strftime('%H:%M') for _l, loc in odd})
                sunday = odd[0][1].weekday() == 6
                add('off_hours', 'high' if any(a == 'download' for a in (l.action for l, _ in odd)) else 'low',
                    _("Sunday access") if sunday else _("Access outside working hours"),
                    _("%(u)s used %(n)s file(s) on %(d)s at %(t)s", u=name, n=len(odd), d=when,
                      t=', '.join(times[:4]) + ('…' if len(times) > 4 else '')), len(odd), odd)
            gone = acts.get('discard', []) + [(l, loc) for l, loc in acts.get('checkin', [])
                                             if (l.details or '').startswith(('Retired', 'Moved'))]
            if len(gone) >= 5:
                add('mass_change', 'high', _("Many documents moved or deleted"),
                    _("%(u)s moved, retired or deleted %(n)s documents on %(d)s", u=name, n=len(gone), d=when),
                    len(gone), gone, 5)
            perm = acts.get('access_grant', []) + acts.get('access_revoke', [])
            perm = [(l, loc) for l, loc in perm if not (l.details or '').startswith('Access requested')]
            if len(perm) >= 10:
                add('permission_spike', 'medium', _("Many permission changes"),
                    _("%(u)s changed access %(n)s times on %(d)s", u=name, n=len(perm), d=when), len(perm), perm, 10)
            asks = [(l, loc) for l, loc in acts.get('access_grant', []) if (l.details or '').startswith('Access requested')]
            if len(asks) >= 5:
                add('access_fishing', 'medium', _("Asking for access to many files"),
                    _("%(u)s asked for access to %(n)s documents on %(d)s", u=name, n=len(asks), d=when), len(asks), asks, 5)
            unlocks = acts.get('force_unlock', [])
            if unlocks:
                add('force_unlock', 'low', _("Forced unlock"),
                    _("%(u)s force-unlocked %(n)s document(s) on %(d)s", u=name, n=len(unlocks), d=when),
                    len(unlocks), unlocks)

        refused = self.env['dms.access.request'].sudo().search([('state', '=', 'refused'), ('decided_on', '>=', since)])
        again = Counter((r.user_id, r.document_id) for r in self.env['dms.access.request'].sudo().search(
            [('create_date', '>=', since), ('document_id', 'in', refused.document_id.ids)]))
        for (user, doc), n in again.items():
            if n >= 2 and doc in refused.filtered(lambda r: r.user_id == user).document_id:
                out.append({'kind': 'asked_again', 'level': 'medium', 'title': _("Asked again after refusal"),
                            'detail': _("%(u)s asked again for %(d)s after it was refused", u=user.name, d=doc.display_name),
                            'user': user.name, 'user_id': user.id, 'count': n, 'limit': 1, 'hours': [],
                            'date': fields.Datetime.to_string(now), 'docs': [doc.display_name], 'more': 0})

        rank = {'high': 0, 'medium': 1, 'low': 2}
        out.sort(key=lambda a: (a['date']), reverse=True)
        out.sort(key=lambda a: rank[a['level']])
        return out[:30]
