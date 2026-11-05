"""Compact visuals for the fixed (general) asset form.

No cards: one thin "pulse" line under the title and a slim strip on top of
each tab - timelines, a depreciation sparkline, the holders' trail. All
computed (never stored), so they always reflect the record as it is.
"""
import json
from datetime import date

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.tools.misc import format_date, formatLang

STEPS = [('draft', 'Draft'), ('submit', 'Submit'), ('assigned', 'Assigned'),
         ('maintenance', 'Maintenance'), ('scrapped', 'Scrapped')]


def _span(d1, d2):
    """'1y 2m' / '3m' / '12d' between two dates."""
    if not d1 or not d2:
        return '—'
    days = (d2 - d1).days
    if days < 0:
        return '—'
    y, rem = divmod(days, 365)
    m = rem // 30
    if y:
        return '%sy %sm' % (y, m) if m else '%sy' % y
    return '%sm' % m if m else '%sd' % days


class AssetAssetFormUI(models.Model):
    _inherit = 'asset.asset'

    ga_pulse = fields.Html(compute='_compute_ga_visuals', sanitize=False)
    ga_warranty_strip = fields.Html(compute='_compute_ga_visuals', sanitize=False)
    ga_amc_strip = fields.Html(compute='_compute_ga_visuals', sanitize=False)
    ga_licence_strip = fields.Html(compute='_compute_ga_visuals', sanitize=False)
    ga_dep_strip = fields.Html(compute='_compute_ga_visuals', sanitize=False)
    ga_holders_strip = fields.Html(compute='_compute_ga_visuals', sanitize=False)
    ga_repair_strip = fields.Html(compute='_compute_ga_visuals', sanitize=False)

    # ------------------------------------------------------------ helpers
    def _ga(self, f, default=False):
        return self[f] if f in self._fields else default

    def _ga_money(self, v):
        cur = self._ga('currency_id') or self.env.company.currency_id
        return formatLang(self.env, v or 0.0, currency_obj=cur)

    def _ga_date(self, v):
        return format_date(self.env, v) if v else '—'

    def _ga_timeline(self, start, end, label, empty):
        """start ── today ── end bar, coloured by how much is left."""
        if not start or not end or end <= start:
            return Markup('<div class="ga-strip ga-empty"><i class="fa fa-info-circle"></i> %s</div>') % empty
        today = fields.Date.context_today(self)
        total = (end - start).days
        gone = min(max((today - start).days, 0), total)
        pct = gone * 100.0 / total
        left = (end - today).days
        if left < 0:
            tone, txt = 'bad', _('expired %s days ago') % -left
        elif left <= 60:
            tone, txt = 'warn', _('%s days left') % left
        else:
            tone, txt = 'ok', _('%s days left') % left
        return Markup(
            '<div class="ga-strip ga-tl %s"><span class="ga-tl-lbl">%s</span>'
            '<span class="ga-tl-d">%s</span>'
            '<div class="ga-tl-bar" title="%s"><i style="width:%.1f%%"></i><b style="left:%.1f%%"></b></div>'
            '<span class="ga-tl-d">%s</span><span class="ga-tl-left">%s</span></div>') % (
            tone, label, self._ga_date(start), _('today'), pct, pct, self._ga_date(end), txt)

    # ------------------------------------------------------------ compute
    @api.depends_context('uid')
    def _compute_ga_visuals(self):
        for rec in self:
            rec.ga_pulse = rec._ga_pulse_html()
            rec.ga_warranty_strip = rec._ga_timeline(
                rec._ga('warranty_start_date'), rec._ga('warranty_end_date'), _('Warranty'),
                _('No warranty dates yet - pick a warranty template and start date below.'))
            rec.ga_amc_strip = rec._ga_timeline(
                rec._ga('amc_start_date'), rec._ga('amc_end_date'), _('AMC'),
                _('Not under AMC.')) if rec._ga('is_amc') else Markup(
                '<div class="ga-strip ga-empty"><i class="fa fa-info-circle"></i> %s</div>') % _(
                'Not under AMC - tick "Under AMC" to track a contract.')
            rec.ga_licence_strip = rec._ga_timeline(
                rec._ga('licence_start_date'), rec._ga('licence_end_date'), _('Licence'),
                _('No licence period.')) if rec._ga('is_licence') else Markup(
                '<div class="ga-strip ga-empty"><i class="fa fa-info-circle"></i> %s</div>') % _('No licence on this asset.')
            rec.ga_dep_strip = rec._ga_dep_html()
            rec.ga_holders_strip = rec._ga_holders_html()
            rec.ga_repair_strip = rec._ga_repair_html()

    def _ga_pulse_html(self):
        today = fields.Date.context_today(self)
        segs = []

        # lifecycle dots
        st = self._ga('state')
        keys = [k for k, _l in STEPS]
        cur = keys.index(st) if st in keys else -1
        dots = Markup('').join(
            Markup('<i class="%s" title="%s"></i>') % (
                'on' if i < cur else ('now' if i == cur else ''), lbl)
            for i, (_k, lbl) in enumerate(STEPS))
        segs.append(Markup('<div class="ga-p"><small>%s</small><div class="ga-steps">%s</div><b>%s</b></div>') % (
            _('Lifecycle'), dots, dict(STEPS).get(st, st or '—')))

        # holder
        emp = self._ga('assigned_employee_id')
        if emp:
            since = self._ga('assignment_date')
            segs.append(Markup(
                '<div class="ga-p ga-who" data-ga-tab="assign"><small>%s</small><div class="ga-who-r">'
                '<img src="/web/image/hr.employee/%s/avatar_128" alt=""/><b>%s</b></div><em>%s</em></div>') % (
                _('With'), emp.id, emp.name, _('for %s') % _span(since, today) if since else ''))
        else:
            segs.append(Markup('<div class="ga-p ga-who" data-ga-tab="assign"><small>%s</small><b class="mute">%s</b><em>%s</em></div>') % (
                _('With'), _('In store'), _('not assigned')))

        # value
        cost = self._ga('purchase_cost') or self._ga('asset_value') or 0.0
        book = self._ga('current_value')
        if cost:
            if not book and not self._ga('depreciation_line_ids'):
                book = cost
            pct = max(min((book or 0.0) * 100.0 / cost, 100), 0)
            segs.append(Markup(
                '<div class="ga-p" data-ga-tab="depreciation"><small>%s</small><b data-count="%s">%s</b>'
                '<div class="ga-mini" title="%s"><i style="width:%.0f%%"></i></div><em>%s</em></div>') % (
                _('Book value'), book or 0.0, self._ga_money(book), _('%.0f%% of cost left') % pct, pct,
                _('of %s') % self._ga_money(cost)))

        # warranty
        wend = self._ga('warranty_end_date')
        if wend:
            left = (wend - today).days
            tone = 'bad' if left < 0 else ('warn' if left <= 60 else 'ok')
            big = _('%sd') % left if left >= 0 else _('expired')
            segs.append(Markup('<div class="ga-p %s" data-ga-tab="warranty"><small>%s</small><b>%s</b><em>%s</em></div>') % (
                tone, _('Warranty'), big, _('till %s') % self._ga_date(wend)))
        else:
            segs.append(Markup('<div class="ga-p" data-ga-tab="warranty"><small>%s</small><b class="mute">—</b><em>%s</em></div>') % (
                _('Warranty'), _('not set')))

        # age
        born = self._ga('purchase_date') or self._ga('acquisition_date')
        segs.append(Markup('<div class="ga-p"><small>%s</small><b>%s</b><em>%s</em></div>') % (
            _('Age'), _span(born, today), _('since %s') % self._ga_date(born) if born else ''))

        # repairs
        rc, ro = self._ga('repair_count', 0) or 0, self._ga('repair_open_count', 0) or 0
        segs.append(Markup('<div class="ga-p %s" data-ga-tab="repair_history"><small>%s</small><b>%s</b><em>%s</em></div>') % (
            'warn' if ro else '', _('Repairs'), rc, _('%s open') % ro if ro else _('all clear')))

        # health score: warranty, open repairs, wear
        score = 100
        if not wend:
            score -= 10
        elif (wend - today).days < 0:
            score -= 25
        score -= min(ro * 10, 30)
        score -= int((self._ga('depreciation_percentage') or 0.0) * 0.3)
        if self._ga('state') == 'scrapped':
            score = 0
        score = max(score, 0)
        tone = 'ok' if score >= 75 else ('warn' if score >= 50 else 'bad')
        health = Markup(
            '<div class="ga-p ga-health %s" title="%s"><div class="ga-ring" style="--p:%s"><b data-count="%s">%s</b></div>'
            '<div><small>%s</small><em>%s</em></div></div>') % (
            tone, _('Warranty, open repairs and wear'), score, score, score, _('Health'),
            {'ok': _('looking good'), 'warn': _('needs a look'), 'bad': _('attention')}[tone])

        acts = [
            ('action_open_direct_assign', '', 'fa-user-plus', _('Assign')),
            ('action_open_transfer', '', 'fa-exchange', _('Transfer')),
            ('action_calculate_depreciation', 'depreciation', 'fa-calculator', _('Recalculate depreciation')),
            ('action_view_it_asset', '', 'fa-desktop', _('IT asset')),
            ('action_open_asset_360', '', 'fa-tachometer', _('Asset 360')),
        ]
        chips = Markup('').join(
            Markup('<button type="button" class="ga-act" data-ga-btn="%s" data-ga-tab="%s"><i class="fa %s"></i> %s</button>') % a
            for a in acts)
        return Markup('<div class="ga-pulse">%s%s</div><div class="ga-acts">%s</div>') % (
            health, Markup('').join(segs), chips)

    def _ga_dep_html(self):
        lines = self._ga('depreciation_line_ids')
        cost = self._ga('purchase_cost') or self._ga('asset_value') or 0.0
        if not lines or not cost:
            return Markup('<div class="ga-strip ga-empty"><i class="fa fa-line-chart"></i> %s</div>') % _(
                'No schedule yet - set the method below and press Calculate Depreciation.')
        lines = lines.sorted(lambda l: (str(l.year or ''), l.id))
        pts = [(_('Start'), cost, 0.0)] + [
            (str(l.year or ''), l.remaining_value or 0.0, l.depreciation_amount or 0.0) for l in lines]
        W, H, P, BH = 600.0, 110.0, 10.0, 34.0
        n = len(pts) - 1 or 1
        mx = max(v for _y, v, _d in pts) or 1.0
        dmx = max(d for _y, _v, d in pts) or 1.0
        top = H - BH - 8
        xy = [(P + i * (W - 2 * P) / n, P + (1 - v / mx) * (top - P)) for i, (_y, v, _d) in enumerate(pts)]
        line = ' '.join('%.1f,%.1f' % p for p in xy)
        area = 'M%.1f,%.1f L%s L%.1f,%.1f Z' % (xy[0][0], top, line.replace(' ', ' L'), xy[-1][0], top)
        this_year = str(date.today().year)
        cur_i = next((i for i, (y, _v, _d) in enumerate(pts) if y.startswith(this_year)), None)
        bw = max(min((W - 2 * P) / (n + 1) * 0.5, 26), 4)
        bars = Markup('').join(
            Markup('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2" class="%s"/>') % (
                x - bw / 2, H - (d / dmx) * BH, bw, (d / dmx) * BH, 'now' if i == cur_i else '')
            for i, ((x, _y), (_l, _v, d)) in enumerate(zip(xy, pts)) if d)
        dots = Markup('').join(
            Markup('<i class="ga-dot %s" style="left:%.2f%%;top:%.2f%%"></i>') % ('now' if i == cur_i else '', x / W * 100, y / H * 100)
            for i, (x, y) in enumerate(xy))
        data = escape(json.dumps([
            {'x': round(x / W * 100, 2), 'y': round(y / H * 100, 2), 'l': l, 'v': self._ga_money(v),
             'd': self._ga_money(d) if d else '', 'now': i == cur_i}
            for i, ((x, y), (l, v, d)) in enumerate(zip(xy, pts))]))
        pct = self._ga('depreciation_percentage') or 0.0
        return Markup(
            '<div class="ga-strip ga-dep"><div class="ga-dep-k">'
            '<div class="ga-ring" style="--p:%.1f"><b>%s</b></div><small>%s</small><em>%s</em></div>'
            '<div class="ga-chart" data-pts="%s"><svg viewBox="0 0 %s %s" preserveAspectRatio="none">'
            '<defs><linearGradient id="gadep" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="#6366f1" stop-opacity=".35"/><stop offset="1" stop-color="#6366f1" stop-opacity="0"/>'
            '</linearGradient></defs>%s<path d="%s" fill="url(#gadep)"/>'
            '<polyline points="%s" fill="none" stroke="#6366f1" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>%s'
            '<div class="ga-x"><span>%s</span><span>%s</span></div><i class="ga-cross"></i><div class="ga-tip"></div></div></div>') % (
            pct, '%.0f%%' % pct, _('depreciated'), _('%s years · hover the chart') % len(lines),
            data, int(W), int(H), bars, area, line, dots, pts[0][0], pts[-1][0])

    def _ga_holders_html(self):
        hist = self._ga('assignment_history_ids')
        if not hist:
            return Markup('<div class="ga-strip ga-empty"><i class="fa fa-users"></i> %s</div>') % _(
                'Nobody has held this asset yet.')
        rows = hist.sorted(lambda h: (str(h.date or ''), h.id))[-8:]
        items = Markup('').join(
            Markup('<li class="%s" title="%s"><span>%s</span>%s<b>%s</b><small>%s</small></li>') % (
                'last' if h == rows[-1] else '', escape(h.description or ''),
                escape((dict(h._fields['action']._description_selection(self.env)).get(h.action) if 'action' in h._fields and h.action else '') or ''),
                (Markup('<img src="/web/image/hr.employee/%s/avatar_128" alt=""/>') % h.employee_id.id)
                if h.employee_id else Markup('<i class="fa fa-archive"></i>'),
                h.employee_id.name or _('Store'), self._ga_date(h.date.date() if hasattr(h.date, 'date') else h.date))
            for h in rows)
        return Markup('<ol class="ga-strip ga-trail">%s</ol>') % items

    def _ga_repair_html(self):
        done = self._ga('repair_done_count', 0) or 0
        open_ = self._ga('repair_open_count', 0) or 0
        nr = self._ga('repair_not_repairable_count', 0) or 0
        tot = done + open_ + nr
        cost = self._ga('maintenance_total_cost', 0.0) or 0.0
        last = self._ga('last_repair_date')
        if not tot and not cost:
            return Markup('<div class="ga-strip ga-empty"><i class="fa fa-check-circle"></i> %s</div>') % _(
                'No repairs or maintenance so far.')
        seg = lambda n, cls, lbl: (Markup('<i class="%s" style="flex:%s" title="%s: %s"></i>') % (cls, n, lbl, n)) if n else Markup('')
        return Markup(
            '<div class="ga-strip ga-rep"><div class="ga-rep-bar">%s%s%s</div>'
            '<span><b>%s</b> %s</span><span class="ok"><b>%s</b> %s</span><span class="warn"><b>%s</b> %s</span>'
            '<span class="bad"><b>%s</b> %s</span><span><b>%s</b> %s</span><span>%s %s</span></div>') % (
            seg(done, 'ok', _('Done')), seg(open_, 'warn', _('Open')), seg(nr, 'bad', _('Not repairable')),
            tot, _('repairs'), done, _('done'), open_, _('open'), nr, _('not repairable'),
            self._ga_money(cost), _('spent'), _('last'), self._ga_date(last.date() if hasattr(last, 'date') else last))
