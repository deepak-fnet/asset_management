from datetime import datetime, timedelta, timezone

from odoo import _, api, fields, models

STATES = [('active', 'Active'), ('idle', 'Idle'), ('locked', 'Locked'), ('off', 'Off')]


def _fmt(minutes):
    minutes = int(round(minutes or 0))
    return '%dh %02dm' % (minutes // 60, minutes % 60) if minutes >= 60 else '%dm' % minutes


class AssetScreenTime(models.Model):
    """One machine, one day: when it was on, worked / idle / locked, and time
    per application - reported by the agent every few minutes."""
    _name = 'asset.screen.time'
    _description = 'Screen Time'
    _order = 'date desc, asset_id'
    _rec_name = 'display_name'

    asset_id = fields.Many2one('asset.asset', required=True, ondelete='cascade', index=True)
    date = fields.Date(required=True, index=True)
    user_name = fields.Char('User')
    first_on = fields.Datetime('First On')
    last_on = fields.Datetime('Last On')
    active_minutes = fields.Float('Active (min)')
    idle_minutes = fields.Float('Idle (min)')
    locked_minutes = fields.Float('Locked (min)')
    on_minutes = fields.Float('On (min)', help="Active + idle + locked.")
    active_display = fields.Char('Active', compute='_compute_display')
    on_display = fields.Char('On', compute='_compute_display')
    idle_display = fields.Char('Idle', compute='_compute_display')
    locked_display = fields.Char('Locked', compute='_compute_display')
    top_app = fields.Char('Most Used App')
    top_app_display = fields.Char('Most Used', compute='_compute_display')
    segment_ids = fields.One2many('asset.screen.time.segment', 'screen_time_id', string='Timeline')
    app_ids = fields.One2many('asset.screen.time.app', 'screen_time_id', string='Applications')
    timeline_html = fields.Html('Day', compute='_compute_timeline', sanitize=False)

    _asset_date_uniq = models.Constraint('UNIQUE(asset_id, date)', 'One screen-time record per asset per day.')

    @api.depends('asset_id', 'date')
    def _compute_display_name(self):
        for r in self:
            r.display_name = '%s - %s' % (r.asset_id.display_name or '', r.date or '')

    @api.depends('active_minutes', 'on_minutes', 'idle_minutes', 'locked_minutes', 'top_app', 'app_ids.minutes')
    def _compute_display(self):
        for r in self:
            r.active_display = _fmt(r.active_minutes)
            r.on_display = _fmt(r.on_minutes)
            r.idle_display = _fmt(r.idle_minutes)
            r.locked_display = _fmt(r.locked_minutes)
            top = r.app_ids.sorted('minutes', reverse=True)[:1]
            r.top_app_display = '%s (%s)' % (top.name, _fmt(top.minutes)) if top else ''

    @api.depends('segment_ids.start', 'segment_ids.end', 'segment_ids.state')
    def _compute_timeline(self):
        """24-hour bar: green active, amber idle, grey locked, blank = off."""
        colors = {'active': '#2e9d5b', 'idle': '#e0a526', 'locked': '#9aa0a6'}
        for r in self:
            tz_off = timedelta(minutes=r.segment_ids[:1].tz_offset_min if r.segment_ids else 0)
            day0 = datetime.combine(r.date, datetime.min.time()) if r.date else None
            bars = []
            for s in r.segment_ids.sorted('start'):
                if not day0:
                    break
                st = (s.start + tz_off - day0).total_seconds() / 864.0
                en = (s.end + tz_off - day0).total_seconds() / 864.0
                st, en = max(st, 0), min(en, 100)
                if en > st:
                    bars.append('<div title="%s %s-%s" style="position:absolute;left:%.3f%%;width:%.3f%%;'
                                'top:0;bottom:0;background:%s"></div>' % (
                                    dict(STATES)[s.state], s.start_local, s.end_local, st, en - st,
                                    colors.get(s.state, '#ccc')))
            ticks = ''.join('<div style="position:absolute;left:%.3f%%;top:2px;font-size:10px;'
                            'transform:translateX(-50%%)">%02d</div>' % (h * 100 / 24, h) for h in range(0, 25, 3))
            legend = ' &nbsp; '.join('<span style="display:inline-block;width:10px;height:10px;background:%s"></span> %s'
                                     % (c, n) for c, n in (('#2e9d5b', 'Active'), ('#e0a526', 'Idle'),
                                                           ('#9aa0a6', 'Locked'), ('#f1f3f4', 'Off')))
            r.timeline_html = ('<div style="position:relative;height:26px;background:#f1f3f4;border-radius:4px;'
                               'overflow:hidden">%s</div><div style="position:relative;height:18px;margin:0 6px">%s</div>'
                               '<div class="small mt-2">%s</div>' % (''.join(bars), ticks, legend))

    @api.model
    def _ingest(self, asset, payload):
        """Upsert the days the agent sends (it always sends the whole day)."""
        tz_min = int(payload.get('tz_offset') or 0) // 60
        for day in payload.get('days') or []:
            date = fields.Date.to_date(day.get('date'))
            if not date:
                continue
            rec = self.sudo().search([('asset_id', '=', asset.id), ('date', '=', date)], limit=1) \
                or self.sudo().create({'asset_id': asset.id, 'date': date})
            segs = day.get('segments') or []
            tot = {'active': 0, 'idle': 0, 'locked': 0}
            seg_cmds = [(5, 0, 0)]
            for s in segs:
                start = datetime.fromtimestamp(int(s['start']), tz=timezone.utc).replace(tzinfo=None)
                end = datetime.fromtimestamp(int(s['end']), tz=timezone.utc).replace(tzinfo=None)
                minutes = (end - start).total_seconds() / 60.0
                if s.get('state') in tot:
                    tot[s['state']] += minutes
                seg_cmds.append((0, 0, {'start': start, 'end': end, 'state': s.get('state') or 'active',
                                        'minutes': minutes, 'user_name': s.get('user'), 'tz_offset_min': tz_min}))
            apps = sorted(day.get('apps') or [], key=lambda a: -int(a.get('seconds') or 0))
            for a in apps:
                if a.get('icon'):
                    self.env['asset.app.icon']._store(a.get('app_id') or a.get('name'), a.get('name'), a['icon'])
                if not a.get('app_id') and a.get('name') == 'Unknown':
                    a['name'] = _('Not tracked (before login)')
                if (a.get('name') or '').lower() == 'gjs' or (a.get('app_id') or '').startswith('window:'):
                    a['name'], a['app_id'] = _('Desktop'), 'desktop'
            merged = {}
            for a in apps:
                nm = a.get('name') or a.get('app_id') or _('Unknown')
                m = merged.setdefault(nm, {'app_id': a.get('app_id'), 'name': nm, 'minutes': 0.0})
                m['minutes'] += int(a.get('seconds') or 0) / 60.0
            app_cmds = [(5, 0, 0)] + [(0, 0, m) for m in sorted(merged.values(), key=lambda x: -x['minutes'])]
            users = [s.get('user') for s in segs if s.get('user')]
            rec.write({
                'segment_ids': seg_cmds, 'app_ids': app_cmds,
                'active_minutes': tot['active'], 'idle_minutes': tot['idle'], 'locked_minutes': tot['locked'],
                'on_minutes': sum(tot.values()),
                'first_on': seg_cmds[1][2]['start'] if len(seg_cmds) > 1 else False,
                'last_on': seg_cmds[-1][2]['end'] if len(seg_cmds) > 1 else False,
                'user_name': max(set(users), key=users.count) if users else False,
                'top_app': apps[0].get('name') if apps else False,
            })
        return True


class AssetScreenTimeSegment(models.Model):
    _name = 'asset.screen.time.segment'
    _description = 'Screen Time Period'
    _order = 'start'

    screen_time_id = fields.Many2one('asset.screen.time', required=True, ondelete='cascade', index=True)
    start = fields.Datetime(required=True)
    end = fields.Datetime(required=True)
    state = fields.Selection(STATES, required=True)
    minutes = fields.Float()
    duration = fields.Char(compute='_compute_local')
    user_name = fields.Char('User')
    tz_offset_min = fields.Integer()
    start_local = fields.Char('From', compute='_compute_local')
    end_local = fields.Char('To', compute='_compute_local')

    def _compute_local(self):
        for s in self:
            off = timedelta(minutes=s.tz_offset_min or 0)
            s.start_local = (s.start + off).strftime('%H:%M') if s.start else ''
            s.end_local = (s.end + off).strftime('%H:%M') if s.end else ''
            s.duration = _fmt(s.minutes)


class AssetAppIcon(models.Model):
    """Application icons sent by the agents - one per application, shared by
    every machine."""
    _name = 'asset.app.icon'
    _description = 'Application Icon'

    key = fields.Char(required=True, index=True, help="Application id (desktop file) or name.")
    name = fields.Char()
    icon = fields.Binary(attachment=True)

    _key_uniq = models.Constraint('UNIQUE(key)', 'One icon per application.')

    @api.model
    def _store(self, key, name, icon_b64):
        rec = self.sudo().search([('key', '=', key)], limit=1)
        if rec:
            rec.write({'icon': icon_b64, 'name': name})
        else:
            self.sudo().create({'key': key, 'name': name, 'icon': icon_b64})


class AssetScreenTimeApp(models.Model):
    _name = 'asset.screen.time.app'
    _description = 'Screen Time per Application'
    _order = 'minutes desc'

    screen_time_id = fields.Many2one('asset.screen.time', required=True, ondelete='cascade', index=True)
    asset_id = fields.Many2one(related='screen_time_id.asset_id', store=True)
    date = fields.Date(related='screen_time_id.date', store=True)
    app_id = fields.Char('Application ID')
    name = fields.Char('Application', required=True)
    minutes = fields.Float('Minutes')
    duration = fields.Char('Time', compute='_compute_duration')
    share = fields.Float('% of Active', compute='_compute_duration')
    icon = fields.Binary('Icon', compute='_compute_icon')

    def _compute_icon(self):
        keys = {k for r in self for k in (r.app_id, r.name) if k}
        icons = {i.key: i.icon for i in self.env['asset.app.icon'].sudo().search([('key', 'in', list(keys))])}
        for r in self:
            r.icon = icons.get(r.app_id) or icons.get(r.name) or False

    def _compute_duration(self):
        for a in self:
            a.duration = _fmt(a.minutes)
            total = a.screen_time_id.active_minutes
            a.share = (a.minutes / total * 100.0) if total else 0.0


class AssetAsset(models.Model):
    _inherit = 'asset.asset'

    screen_time_ids = fields.One2many('asset.screen.time', 'asset_id', string='Screen Time')
