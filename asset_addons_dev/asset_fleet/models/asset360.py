from datetime import date, datetime, timedelta

from odoo import _, api, fields, models
from odoo.tools import html2plaintext


def _fmt_min(m):
    m = int(round(m or 0))
    return '%dh %02dm' % (m // 60, m % 60) if m >= 60 else '%dm' % m


def _r(v, n=1):
    try:
        return round(float(v or 0), n)
    except (TypeError, ValueError):
        return 0


class AssetAsset360(models.Model):
    _inherit = 'asset.asset'

    def action_open_asset_360(self):
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'asset_fleet.asset_360', 'name': _('Asset 360'),
                'params': {'asset_id': self.id}, 'context': {'active_id': self.id}}

    # ------------------------------------------------------------------ data
    def _img(self, model, rec, field):
        return '/web/image/%s/%s/%s' % (model, rec.id, field) if rec and rec[field] else False

    @api.model
    def get_asset_360(self, asset_id, day=None):
        asset = self.browse(int(asset_id)).exists()
        if not asset:
            return {'error': _('Asset not found')}
        it = asset.it_asset_id if 'it_asset_id' in asset._fields and asset.it_asset_id else asset
        emp = (asset.assigned_employee_id or it.assigned_employee_id) if 'assigned_employee_id' in asset._fields \
            else self.env['hr.employee']
        cur = asset.currency_id if 'currency_id' in asset._fields else self.env.company.currency_id
        live = self.env['asset.live.monitoring'].sudo().search([('asset_id', '=', it.id)], limit=1) \
            if 'asset.live.monitoring' in self.env else None

        def g(rec, f, default=False):
            return rec[f] if rec and f in rec._fields else default

        last_seen = g(live, 'last_heartbeat') or g(it, 'last_sync_time') or g(it, 'last_agent_sync')
        online = bool(last_seen and (fields.Datetime.now() - last_seen).total_seconds() < 600)
        data = {
            'asset': {
                'id': asset.id, 'name': asset.display_name, 'code': g(asset, 'asset_code'),
                'serial': asset.serial_number,
                'category': asset.category_id.display_name if 'category_id' in asset._fields and asset.category_id else '',
                'image': self._img('asset.asset', asset, 'image_1920') if 'image_1920' in asset._fields else False,
                'state': dict(asset._fields['state'].selection).get(asset.state) if 'state' in asset._fields else '',
                'department': asset.department_id.name if 'department_id' in asset._fields and asset.department_id else '',
                'site': asset.location_id.display_name if 'location_id' in asset._fields and asset.location_id else '',
                'it_id': it.id, 'it_name': it.display_name,
            },
            'employee': emp and {
                'id': emp.id, 'name': emp.name, 'job': emp.job_title or (emp.job_id.name if emp.job_id else ''),
                'email': emp.work_email, 'phone': emp.mobile_phone or emp.work_phone,
                'image': '/web/image/hr.employee/%s/avatar_128' % emp.id,
                'since': fields.Date.to_string(g(asset, 'assignment_date')) if g(asset, 'assignment_date') else False,
            } or False,
            'device': {
                'hostname': g(it, 'hostname'), 'os': g(it, 'os_name'), 'os_version': g(it, 'os_version'),
                'ip': (g(it, 'ip_address') or '').split(':')[0], 'processor': g(it, 'processor'),
                'ram': _r(g(it, 'ram_size')), 'storage': _r(g(it, 'total_storage_size')),
                'online': online, 'last_seen': fields.Datetime.to_string(last_seen) if last_seen else False,
                'uptime': g(live, 'uptime'),
            },
            'live': {
                'cpu': _r(g(live, 'cpu_usage_percent') or g(live, 'cpu_usage') or g(it, 'cpu_usage_percent')),
                'ram': _r(g(live, 'ram_usage_percent') or g(it, 'ram_usage_percent')),
                'disk': _r(g(live, 'disk_usage_percent') or g(it, 'disk_usage_percent')),
                'battery': _r(g(live, 'battery_percentage') or g(live, 'battery_level') or g(it, 'battery_percentage')),
                'battery_health': _r(g(live, 'battery_health') or g(it, 'battery_health')),
                'up': _r(g(live, 'network_upload_mbps'), 2), 'down': _r(g(live, 'network_download_mbps'), 2),
            },
        }
        data['location'] = self._360_location(it)
        data['screen'] = self._360_screen(it, day)
        data['health'] = self._360_health(it)
        data['finance'] = self._360_finance(asset, cur)
        data['feed'] = self._360_feed(asset, it)
        return data

    def _360_location(self, it):
        hist = []
        if 'location_history_ids' in it._fields:
            for h in it.location_history_ids.sorted('date', reverse=True)[:8]:
                hist.append({'date': fields.Datetime.to_string(h.date), 'city': h.city or '',
                             'address': h.full_address or '', 'source': h.source or '',
                             'lat': h.latitude, 'lon': h.longitude})
        src = dict(it._fields['location_source'].selection).get(it.location_source) \
            if 'location_source' in it._fields else ''
        return {'lat': it.latitude if 'latitude' in it._fields else 0,
                'lon': it.longitude if 'longitude' in it._fields else 0,
                'source': src, 'source_key': it.location_source if 'location_source' in it._fields else '',
                'accuracy': _r(it.location_accuracy, 0) if 'location_accuracy' in it._fields else 0,
                'city': it.location_city or '', 'state': it.location_state or '',
                'address': it.location_address or '',
                'updated': fields.Datetime.to_string(it.last_location_update) if it.last_location_update else False,
                'history': hist}

    def _360_screen(self, it, day):
        ST = self.env['asset.screen.time'].sudo()
        recs = ST.search([('asset_id', '=', it.id)], order='date desc', limit=60)
        if not recs:
            return {'has_data': False}
        days_available = [fields.Date.to_string(r.date) for r in recs]
        sel = recs.filtered(lambda r: fields.Date.to_string(r.date) == day)[:1] if day else recs[:1]
        sel = sel or recs[:1]
        icons = {}
        apps_all = self.env['asset.screen.time.app'].sudo().search([('screen_time_id', 'in', recs.ids)])
        keys = {k for a in apps_all for k in (a.app_id, a.name) if k}
        for i in self.env['asset.app.icon'].sudo().search([('key', 'in', list(keys))]):
            icons[i.key] = '/web/image/asset.app.icon/%s/icon' % i.id

        def icon(a):
            return icons.get(a.app_id) or icons.get(a.name) or False

        def segs(r):
            out = []
            for s in r.segment_ids.sorted('start'):
                off = timedelta(minutes=s.tz_offset_min or 0)
                d0 = datetime.combine(r.date, datetime.min.time())
                st = max(0, (s.start + off - d0).total_seconds() / 60)
                en = min(1440, (s.end + off - d0).total_seconds() / 60)
                if en > st:
                    out.append({'s': round(st), 'e': round(en), 'state': s.state, 'from': s.start_local,
                                'to': s.end_local, 'dur': s.duration})
            return out

        # last 7 days ending at the selected day
        end = sel.date
        week = []
        heat = [[0] * 24 for _i in range(7)]
        app_week = {}
        for i in range(6, -1, -1):
            d = end - timedelta(days=i)
            r = recs.filtered(lambda x, d=d: x.date == d)[:1]
            week.append({'date': fields.Date.to_string(d), 'label': d.strftime('%a'), 'day': d.day,
                         'active': _r(r.active_minutes, 0) if r else 0, 'idle': _r(r.idle_minutes, 0) if r else 0,
                         'locked': _r(r.locked_minutes, 0) if r else 0})
            if r:
                for s in segs(r):
                    if s['state'] != 'active':
                        continue
                    m = s['s']
                    while m < s['e']:
                        h = int(m // 60)
                        nxt = min(s['e'], (h + 1) * 60)
                        heat[6 - i][h] += nxt - m
                        m = nxt
                for a in r.app_ids:
                    k = a.name
                    app_week.setdefault(k, {'name': k, 'icon': icon(a), 'days': [0] * 7, 'total': 0})
                    app_week[k]['days'][6 - i] += _r(a.minutes, 0)
                    app_week[k]['total'] += _r(a.minutes, 0)
        week_active = [w['active'] for w in week if w['active']]
        merged = {}
        for a in sel.app_ids:
            m = merged.setdefault(a.name, {'name': a.name, 'minutes': 0, 'icon': icon(a)})
            m['minutes'] += a.minutes
            m['icon'] = m['icon'] or icon(a)
        tot = sel.active_minutes or 1
        apps = [{'name': m['name'], 'minutes': _r(m['minutes'], 0), 'dur': _fmt_min(m['minutes']),
                 'share': _r(m['minutes'] / tot * 100, 0), 'icon': m['icon']}
                for m in sorted(merged.values(), key=lambda x: -x['minutes'])]
        return {
            'has_data': True, 'days': days_available, 'day': fields.Date.to_string(sel.date),
            'user': sel.user_name, 'active': _r(sel.active_minutes, 0), 'idle': _r(sel.idle_minutes, 0),
            'locked': _r(sel.locked_minutes, 0), 'on': _r(sel.on_minutes, 0),
            'first_on': fields.Datetime.to_string(sel.first_on) if sel.first_on else False,
            'last_on': fields.Datetime.to_string(sel.last_on) if sel.last_on else False,
            'segments': segs(sel), 'apps': apps, 'week': week, 'heat': [[round(v) for v in row] for row in heat],
            'week_avg': round(sum(week_active) / len(week_active)) if week_active else 0,
            'week_total': round(sum(w['active'] for w in week)),
            'app_week': sorted(app_week.values(), key=lambda a: -a['total'])[:8],
            'target': 480,
        }

    def _360_health(self, it):
        def g(f, d=0):
            return it[f] if f in it._fields else d
        restr = {}
        tele = it.agent_telemetry if 'agent_telemetry' in it._fields and isinstance(it.agent_telemetry, dict) else {}
        r = tele.get('restrictions') or {}
        if r:
            restr = {'screenshot': r.get('screenshot'), 'record': r.get('screen_record'), 'usb': r.get('usb_storage'),
                     'websites': r.get('blocked_websites'), 'extensions': len(r.get('detected_extensions') or []),
                     'lock': r.get('screen_lock_minutes'), 'removed': r.get('removed_files')}
        osu = self.env['asset.os.upgrade.request'].sudo().search([('asset_id', '=', it.id)], order='id desc',
                                                                 limit=1) if 'asset.os.upgrade.request' in self.env else None
        return {
            'os_pending': g('os_update_pending_count'), 'app_pending': g('app_update_pending_count'),
            'apps_installed': g('installed_application_count'),
            'vuln': {'critical': g('vuln_critical_count'), 'high': g('vuln_high_count'),
                     'medium': g('vuln_medium_count'), 'low': g('vuln_low_count')},
            'antivirus': {'installed': g('antivirus_installed', False), 'running': g('antivirus_running', False),
                          'product': g('antivirus_product', '')},
            'security': dict(it._fields['security_status'].selection).get(it.security_status)
            if 'security_status' in it._fields and it.security_status else '',
            'restrictions': restr,
            'os_upgrade': osu and {'name': osu.name, 'state': dict(osu._fields['state'].selection).get(osu.state),
                                   'target': osu.target_version} or False,
            'policy': it.telemetry_policy_id.name if 'telemetry_policy_id' in it._fields and it.telemetry_policy_id
            else _('Default Policy'),
        }

    def _360_finance(self, asset, cur):
        def g(f, d=0):
            return asset[f] if f in asset._fields else d
        cost = g('purchase_cost') or g('asset_value') or 0
        value = g('current_value') or cost
        return {
            'symbol': cur.symbol, 'cost': _r(cost, 0), 'value': _r(value, 0),
            'dep_pct': _r(g('depreciation_percentage'), 0),
            'purchase_date': fields.Date.to_string(g('purchase_date')) if g('purchase_date') else False,
            'warranty_end': fields.Date.to_string(g('warranty_end_date')) if g('warranty_end_date') else False,
            'warranty_days': g('warranty_days_left') if g('warranty_end_date') else False,
            'amc': dict(asset._fields['amc_status'].selection).get(asset.amc_status)
            if 'amc_status' in asset._fields and asset.amc_status else '',
            'repairs_open': g('repair_open_count'), 'repairs_done': g('repair_done_count'),
            'maintenance_cost': _r(g('maintenance_total_cost'), 0),
            'next_maintenance': fields.Date.to_string(g('next_maintenance_date')) if g('next_maintenance_date') else False,
        }

    def _360_feed(self, asset, it):
        msgs = self.env['mail.message'].sudo().search(
            [('model', '=', 'asset.asset'), ('res_id', 'in', list({asset.id, it.id})),
             ('message_type', 'in', ('comment', 'notification'))], order='date desc', limit=80)
        out = []
        for m in msgs:
            text = html2plaintext(m.body or '').strip()
            # agent sync bookkeeping is noise here
            tvs = m.tracking_value_ids.filtered(lambda tv: not any(
                k in (tv.field_id.name or '') for k in ('sync', 'last_', 'change_detected', 'heartbeat')))
            if not text and m.tracking_value_ids and not tvs:
                continue
            if not text and tvs:
                t = tvs[:2]
                text = ', '.join('%s: %s' % (tv.field_id.field_description, tv.new_value_char or tv.new_value_integer
                                             or tv.new_value_float or tv.new_value_datetime or '') for tv in t)
            if text:
                out.append({'date': fields.Datetime.to_string(m.date), 'author': m.author_id.name or 'System',
                            'text': text[:160]})
            if len(out) >= 14:
                break
        return out
