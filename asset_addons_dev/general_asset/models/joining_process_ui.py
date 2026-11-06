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

        # who
        if emp:
            sub = ' · '.join(x for x in [emp.job_title or (emp.job_id.name if emp.job_id else ''),
                                         self.department_id.name or emp.department_id.name or ''] if x)
            who = Markup(
                '<img class="jp-av" src="/web/image/hr.employee/%s/avatar_128" alt=""/>'
                '<div class="jp-who"><small>%s</small><h2>%s</h2><span>%s</span></div>') % (
                emp.id, _('Assets for'), emp.name, sub or _('Employee'))
        else:
            who = Markup('<span class="jp-av jp-av-empty"><i class="fa fa-user-plus"></i></span>'
                         '<div class="jp-who"><small>%s</small><h2>%s</h2><span>%s</span></div>') % (
                _('Assets for'), _('Pick an employee'), _('Fill the request below'))

        meta = []
        if self.requested_by_id:
            meta.append(Markup('<span><i class="fa fa-user-circle-o"></i> %s <b>%s</b></span>') % (
                _('Requested by'), self.requested_by_id.name))
        when = self.joining_date or (self.create_date and self.create_date.date())
        if when:
            meta.append(Markup('<span><i class="fa fa-calendar"></i> %s</span>') % format_date(env, when))
        meta.append(Markup('<span class="jp-ref"><i class="fa fa-hashtag"></i> %s</span>') % (self.name or ''))

        # progress
        lines = self.request_line_ids
        req = sum(lines.mapped('quantity')) or 0
        got = sum(lines.mapped('assigned_qty')) or 0
        pct = int(got * 100 / req) if req else 0
        ring = Markup(
            '<div class="jp-ring" style="--p:%s"><b>%s<small>/%s</small></b><span>%s</span></div>') % (
            pct, int(got), int(req), _('assigned'))

        # journey
        keys = [k for k, _l in JOURNEY]
        cur = keys.index('approved') if st == 'validate' else (keys.index(st) if st in keys else -1)
        if st == 'cancelled':
            steps = Markup('<div class="jp-cancel"><i class="fa fa-ban"></i> %s</div>') % sel.get(st, st)
        else:
            steps = Markup('').join(
                Markup('<li class="%s"><i>%s</i><span>%s</span></li>') % (
                    'done' if i < cur or st == 'done' else ('now' if i == cur else ''),
                    Markup('<span class="fa fa-check"></span>') if (i < cur or st == 'done') else str(i + 1),
                    lbl)
                for i, (_k, lbl) in enumerate(JOURNEY))
            steps = Markup('<ol class="jp-steps">%s</ol>') % steps

        # handed over
        assets = self.allocation_ids.mapped('asset_ids')
        chips = Markup('').join(
            Markup('<a class="jp-chip" href="/odoo/asset.asset/%s" title="%s"><i class="fa %s"></i> %s</a>') % (
                a.id, escape(a.display_name), _icon(a.display_name, a.category_id.display_name or ''),
                a.asset_code or a.display_name)
            for a in assets)
        handed = Markup('<div class="jp-handed"><small>%s</small>%s</div>') % (
            _('Handed over'), chips) if assets else Markup('')

        desc = Markup('<p class="jp-desc"><i class="fa fa-quote-left"></i> %s</p>') % self.description \
            if self.description else Markup('')

        return Markup(
            '<div class="jp-hero s-%s">'
            '<div class="jp-top"><div class="jp-id">%s<div class="jp-meta">%s</div></div>%s</div>'
            '%s%s%s</div>') % (
            st, who, Markup('').join(meta), ring, steps, desc, handed)
