"""All Asset Dashboard - "needs attention" insights.

Each insight is a question the asset team should act on (stuck repairs,
repeat repairs, value lost, laptops whose agent is not linked or silent,
...). It returns how many assets match, one line of story, the three worst
assets, and open_insight() opens the full list.
"""
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models


class AssetHubDashboardInsights(models.AbstractModel):
    _inherit = 'asset.hub.dashboard'

    def _ins_money(self, v):
        v = v or 0.0
        if v >= 1e7:
            return '₹%.2f Cr' % (v / 1e7)
        if v >= 1e5:
            return '₹%.2f L' % (v / 1e5)
        return '₹{:,.0f}'.format(v)

    def _insight_sets(self, filters):
        """{key: (ids ordered worst first, notes {id: text}, extra {})}"""
        Asset = self.env['asset.asset'].sudo()
        f = Asset._fields
        base = self._analytics_domain(filters) + [('state', '!=', 'scrapped')]
        assets = Asset.search(base)
        ids = set(assets.ids)
        today = fields.Date.today()
        now = fields.Datetime.now()
        out = {}

        # repairs
        if 'repair.management' in self.env:
            R = self.env['repair.management'].sudo()
            reps = R.search([('asset_id', 'in', list(ids))])
            open_ = reps.filtered(lambda r: r.state in ('new', 'in_progress') and r.request_date)
            stuck = {}
            for r in open_:
                age = (today - r.request_date).days
                if age >= 7 and age > stuck.get(r.asset_id.id, -1):
                    stuck[r.asset_id.id] = age
            order = sorted(stuck, key=lambda i: -stuck[i])
            out['stuck_repair'] = (order, {i: _('%s days in repair') % stuck[i] for i in order},
                                   {'max': stuck[order[0]] if order else 0})

            year_ago = today - timedelta(days=365)
            per = defaultdict(int)
            cost = defaultdict(float)
            for r in reps:
                cost[r.asset_id.id] += r.repair_cost or 0.0
                if r.request_date and r.request_date >= year_ago:
                    per[r.asset_id.id] += 1
            rep = sorted([i for i, n in per.items() if n >= 2], key=lambda i: -per[i])
            out['repeat_repair'] = (rep, {i: _('%s repairs this year') % per[i] for i in rep}, {})

            by_id = {a.id: a for a in assets}
            pricey = [i for i, c in cost.items() if c and by_id.get(i) and by_id[i].purchase_cost
                      and c >= 0.4 * by_id[i].purchase_cost]
            pricey.sort(key=lambda i: -cost[i] / by_id[i].purchase_cost)
            out['repair_cost'] = (pricey, {i: _('repairs %s = %d%% of cost') % (
                self._ins_money(cost[i]), cost[i] * 100 / by_id[i].purchase_cost) for i in pricey}, {})

        # value
        valued = assets.filtered(lambda a: a.purchase_cost and 'current_value' in f and a.current_value
                                 and a.current_value < a.purchase_cost)
        drop = {a.id: (a.purchase_cost - a.current_value) * 100 / a.purchase_cost for a in valued}
        big = sorted([i for i, p in drop.items() if p >= 50], key=lambda i: -drop[i])
        lost = sum(a.purchase_cost - a.current_value for a in valued)
        cost_all = sum(a.purchase_cost or 0.0 for a in assets)
        book_all = sum((a.current_value if a.current_value else a.purchase_cost) or 0.0 for a in assets) \
            if 'current_value' in f else cost_all
        out['value_drop'] = (big, {i: _('lost %d%% of its value') % drop[i] for i in big},
                             {'lost': lost, 'cost': cost_all, 'book': book_all})

        # laptops / IT: fixed asset not linked to its agent record, or agent silent
        if 'it_asset_id' in f and 'is_it_asset' in f:
            agent_dom = [('monitoring_protocol', '=', 'agent')] if 'monitoring_protocol' in f else []
            agent_ids = set(Asset.search(agent_dom).ids) if agent_dom else set()
            fixed_it = assets.filtered(lambda a: a.is_it_asset and a.id not in agent_ids)
            unlinked = fixed_it.filtered(lambda a: not a.it_asset_id)
            out['it_unlinked'] = (unlinked.ids, {i: _('no agent linked') for i in unlinked.ids}, {})
            stale = {}
            for a in fixed_it.filtered('it_asset_id'):
                seen = a.it_asset_id.last_sync_time or (a.it_asset_id.last_agent_sync if 'last_agent_sync' in f else False)
                days = (now - seen).days if seen else None
                if days is None or days >= 3:
                    stale[a.id] = days
            st = sorted(stale, key=lambda i: -(stale[i] if stale[i] is not None else 10 ** 6))
            out['agent_silent'] = (st, {i: (_('agent silent %s days') % stale[i]) if stale[i] is not None
                                        else _('agent never synced') for i in st}, {})

        # warranty expired but still with someone
        wexp = assets.filtered(lambda a: a.state == 'assigned' and a.warranty_end_date and a.warranty_end_date < today)
        wexp = wexp.sorted(lambda a: a.warranty_end_date)
        out['warranty_gone'] = (wexp.ids, {a.id: _('expired %s days ago') % (today - a.warranty_end_date).days
                                           for a in wexp}, {})

        # maintenance overdue
        if 'next_maintenance_date' in f:
            mo = assets.filtered(lambda a: a.next_maintenance_date and a.next_maintenance_date < today)
            mo = mo.sorted(lambda a: a.next_maintenance_date)
            out['maint_overdue'] = (mo.ids, {a.id: _('%s days overdue') % (today - a.next_maintenance_date).days
                                             for a in mo}, {})

        # idle: never assigned, sitting for 90+ days
        idle = {}
        for a in assets.filtered(lambda a: a.state in ('draft', 'submit') and not a.assigned_employee_id):
            since = a.purchase_date or (a.create_date and a.create_date.date())
            if since and (today - since).days >= 90:
                idle[a.id] = (today - since).days
        il = sorted(idle, key=lambda i: -idle[i])
        out['idle'] = (il, {i: _('idle %s days') % idle[i] for i in il},
                       {'value': sum(a.purchase_cost or 0.0 for a in assets if a.id in idle)})

        # not submitted for 14+ days
        drafts = assets.filtered(lambda a: a.state == 'draft' and a.create_date and (now - a.create_date).days >= 14)
        drafts = drafts.sorted(lambda a: a.create_date)
        out['stale_draft'] = (drafts.ids, {a.id: _('draft for %s days') % (now - a.create_date).days for a in drafts}, {})
        return out

    INSIGHTS = [
        # key, label, icon, tone
        ('stuck_repair', 'Stuck in repair (7d+)', 'fa-wrench', 'bad'),
        ('repeat_repair', 'Repeated repairs (2+ / year)', 'fa-repeat', 'bad'),
        ('repair_cost', 'Repairs cost 40%+ of price', 'fa-money', 'warn'),
        ('value_drop', 'Lost half their value', 'fa-line-chart', 'warn'),
        ('it_unlinked', 'Laptops / IT not linked to agent', 'fa-chain-broken', 'bad'),
        ('agent_silent', 'Agent silent 3d+', 'fa-wifi', 'warn'),
        ('warranty_gone', 'In use, warranty expired', 'fa-shield', 'warn'),
        ('maint_overdue', 'Maintenance overdue', 'fa-calendar-times-o', 'warn'),
        ('idle', 'Idle in store 90d+', 'fa-archive', 'info'),
        ('stale_draft', 'Draft for 14d+', 'fa-pencil-square-o', 'info'),
    ]

    @api.model
    def get_asset_insights(self, filters=None):
        sets = self._insight_sets(filters)
        Asset = self.env['asset.asset'].sudo()
        cards = []
        for key, label, icon, tone in self.INSIGHTS:
            if key not in sets:
                continue
            ids, notes, extra = sets[key]
            top = Asset.browse(ids[:3])
            story = ''
            if key == 'stuck_repair' and ids:
                story = _('longest waiting %s days') % extra['max']
            elif key == 'value_drop':
                story = _('%s written off so far') % self._ins_money(extra['lost'])
            elif key == 'idle' and ids:
                story = _('%s sitting unused') % self._ins_money(extra['value'])
            elif key == 'it_unlinked' and ids:
                story = _('no live hardware / location data for these')
            elif key == 'agent_silent' and ids:
                story = _('offline or uninstalled?')
            cards.append({
                'key': key, 'label': label, 'icon': icon, 'tone': tone if ids else 'ok',
                'count': len(ids), 'story': story,
                'top': [{'id': a.id, 'name': a.display_name, 'note': notes.get(a.id, '')} for a in top],
            })
        cards.sort(key=lambda c: (c['count'] == 0, {'bad': 0, 'warn': 1, 'info': 2, 'ok': 3}[c['tone']]))
        v = sets.get('value_drop', ([], {}, {}))[2]
        cost, book = v.get('cost', 0.0), v.get('book', 0.0)
        return {
            'cards': cards,
            'value': {
                'cost': cost, 'book': book, 'lost': cost - book,
                'cost_txt': self._ins_money(cost), 'book_txt': self._ins_money(book),
                'lost_txt': self._ins_money(cost - book),
                'kept_pct': round(book * 100 / cost) if cost else 100,
            },
            'attention': sum(c['count'] for c in cards if c['tone'] in ('bad', 'warn')),
        }

    @api.model
    def open_insight(self, filters, key):
        ids = self._insight_sets(filters).get(key, ([], {}, {}))[0]
        label = dict((k, l) for k, l, _i, _t in self.INSIGHTS).get(key, _('Assets'))
        return self._general_asset_action(label, [('id', 'in', ids)])

    @api.model
    def open_asset_form(self, asset_id):
        action = self._general_asset_action(_('Asset'), [])
        action.update({'res_id': int(asset_id), 'view_mode': 'form',
                       'views': [(v, k) for v, k in action.get('views', []) if k == 'form'] or [(False, 'form')]})
        return action
