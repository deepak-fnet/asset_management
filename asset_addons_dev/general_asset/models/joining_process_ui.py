"""Asset Assign Process - a custom header instead of Odoo's plain sheet.

One computed (never stored) HTML block: who it is for, the journey from
request to done, how much is assigned, each requested item with its own
progress, and the assets handed over.
"""
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.tools.misc import format_date

JOURNEY = [('draft', 'Requested'), ('confirmed', 'Pending approval'), ('approved', 'Approved'),
           ('partial', 'Assigning'), ('done', 'Done')]

ICONS = [('laptop', 'fa-laptop'), ('notebook', 'fa-laptop'), ('thinkpad', 'fa-laptop'),
         ('desktop', 'fa-desktop'), ('monitor', 'fa-desktop'), ('mouse', 'fa-mouse-pointer'),
         ('keyboard', 'fa-keyboard-o'), ('phone', 'fa-mobile'), ('mobile', 'fa-mobile'),
         ('chair', 'fa-wheelchair-alt'), ('table', 'fa-columns'), ('ac', 'fa-snowflake-o'),
         ('air', 'fa-snowflake-o'), ('print', 'fa-print'), ('headset', 'fa-headphones'),
         ('camera', 'fa-camera'), ('server', 'fa-server'), ('network', 'fa-sitemap')]


def _icon(*texts):
    words = ' '.join(t.lower() for t in texts if t).replace('/', ' ').split()
    for key, icon in ICONS:
        if key in words or (len(key) > 3 and any(key in w for w in words)):
            return icon
    return 'fa-cube'


class AssetJoiningProcessUI(models.Model):
    _inherit = 'asset.joining.process'

    jp_hero = fields.Html(compute='_compute_jp_hero', sanitize=False)

    @api.depends_context('uid')
    def _compute_jp_hero(self):
        for rec in self:
            rec.jp_hero = rec._jp_hero_html()

    def _jp_hero_html(self):
        env = self.env
        emp = self.employee_id
        st = self.state
        sel = dict(self._fields['state']._description_selection(env))

        if emp:
            sub = ' · '.join(x for x in [emp.job_title or (emp.job_id.name if emp.job_id else ''),
                                         self.department_id.name or emp.department_id.name or ''] if x)
            av = Markup('<img class="jx-av" src="/web/image/hr.employee/%s/avatar_128" alt=""/>') % emp.id
            name = emp.name
        else:
            sub = _('Fill the request below')
            av = Markup('<span class="jx-av jx-av-empty"><i class="fa fa-user-plus"></i></span>')
            name = _('Pick an employee')

        lines = self.request_line_ids
        req = int(sum(lines.mapped('quantity')) or 0)
        got = int(sum(lines.mapped('assigned_qty')) or 0)
        pct = int(got * 100 / req) if req else 0
        when = self.joining_date or (self.create_date and self.create_date.date())

        def stat(label, val, cls=''):
            return Markup('<div class="jx-stat %s"><b>%s</b><span>%s</span></div>') % (cls, val, label)
        stats = stat(_('Requested'), req) + stat(_('Assigned'), got, 'ok') + \
            stat(_('Pending'), max(req - got, 0), 'warn' if req - got else '')

        keys = [k for k, _l in JOURNEY]
        cur = keys.index('approved') if st == 'validate' else (keys.index(st) if st in keys else -1)
        if st == 'cancelled':
            track = Markup('<div class="jx-cancel"><i class="fa fa-ban"></i> %s</div>') % sel.get(st, st)
        else:
            track = Markup('<div class="jx-track">%s</div>') % Markup('').join(
                Markup('<div class="jx-seg %s"><i></i><span>%s</span></div>') % (
                    'done' if (i < cur or st == 'done') else ('now' if i == cur else ''), lbl)
                for i, (_k, lbl) in enumerate(JOURNEY))

        meta = []
        if self.requested_by_id:
            meta.append(Markup('<span><i class="fa fa-user-o"></i> %s</span>') % self.requested_by_id.name)
        if when:
            meta.append(Markup('<span><i class="fa fa-calendar-o"></i> %s</span>') % format_date(env, when))
        meta.append(Markup('<span class="jx-ref">%s</span>') % (self.name or ''))

        assets = self.allocation_ids.mapped('asset_ids')
        chips = Markup('').join(
            Markup('<a class="jx-chip" href="/odoo/asset.asset/%s" title="%s"><i class="fa %s"></i> %s</a>') % (
                a.id, escape(a.display_name), _icon(a.display_name, a.category_id.display_name or ''),
                a.asset_code or a.display_name)
            for a in assets)
        handed = Markup('<div class="jx-handed"><small>%s</small>%s</div>') % (
            _('Handed over'), chips) if assets else Markup('')
        desc = Markup('<p class="jx-desc">%s</p>') % self.description if self.description else Markup('')

        return Markup(
            '<div class="jx-hero s-%s" style="--p:%s">'
            '<div class="jx-row">'
            '<div class="jx-id">%s<div><h2>%s</h2><p>%s</p></div></div>'
            '<div class="jx-stats">%s<div class="jx-pct"><b>%s%%</b><span>%s</span></div></div>'
            '</div>'
            '<div class="jx-meta">%s<em class="jx-state">%s</em></div>'
            '%s%s%s</div>') % (
            st, pct, av, name, sub or _('Employee'), stats, pct, _('complete'),
            Markup('').join(meta), sel.get(st, st), track, desc, handed)
