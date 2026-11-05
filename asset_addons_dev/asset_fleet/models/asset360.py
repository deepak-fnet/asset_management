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
        # opened from the IT asset: business data is on its fixed asset
        if 'fixed_asset_id' in asset._fields and asset.fixed_asset_id:
            asset = asset.fixed_asset_id
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
                'it_view_id': self.env.ref('asset_management.view_asset_asset_form').id,
                # a record linked to an IT asset is the fixed asset: its own (general) form
                'fa_view_id': (self.env.ref('general_asset.view_asset_general_form', raise_if_not_found=False)
                               or self.env.ref('asset_management.view_asset_asset_form')).id
                if (it != asset or not (asset.is_it_asset if 'is_it_asset' in asset._fields else False))
                else self.env.ref('asset_management.view_asset_asset_form').id,
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
        data['details'] = self._360_details(asset, it)
        data['fixed'] = self._360_fixed(asset, cur)
        data['osu'] = self._360_os_upgrade(it)
        data['all'] = self._360_all_fields(asset, it)
        return data

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _fmt_val(rec, name, cur=None):
        f = rec._fields.get(name)
        if not f:
            return False
        v = rec[name]
        if f.type in ('one2many', 'many2many', 'binary', 'html', 'json', 'properties', 'reference'):
            return False
        if f.type == 'boolean':
            return 'Yes' if v else False
        if v is False or v is None or v == '':
            return False
        if f.type in ('integer', 'float', 'monetary') and not v:
            return False
        if f.type == 'many2one':
            return v.display_name
        if f.type == 'selection':
            sel = f.selection(rec) if callable(f.selection) else f.selection
            return dict(sel or []).get(v, v)
        if f.type == 'date':
            return v.strftime('%d %b %Y')
        if f.type == 'datetime':
            return fields.Datetime.context_timestamp(rec, v).strftime('%d %b %Y %H:%M')
        if f.type == 'monetary':
            c = rec[f.currency_field] if f.currency_field and f.currency_field in rec._fields else cur
            return '%s %s' % ((c.symbol if c else ''), '{:,.2f}'.format(v))
        if f.type == 'float':
            return '{:,.2f}'.format(v).rstrip('0').rstrip('.')
        return str(v)

    def _360_fields(self, rec, names, cur=None):
        out = []
        for n, label in names:
            v = self._fmt_val(rec, n, cur)
            if v:
                out.append({'label': label or rec._fields[n].string, 'value': v})
        return out

    def _360_fixed(self, asset, cur):
        P = [('acquisition_type', 'Acquisition'), ('procurement_vendor_id', 'Vendor'), ('purchase_order_id', 'Purchase order'),
             ('invoice_move_id', 'Vendor bill'), ('invoice_ref', 'Invoice ref'), ('purchase_date', 'Purchase date'),
             ('receipt_date', 'Received'), ('acquisition_date', 'Acquired'), ('procurement_department_id', 'Procured for'),
             ('purchase_cost', 'Purchase cost'), ('asset_value', 'Asset value')]
        W = [('warranty_status', 'Status'), ('warranty_type', 'Type'), ('warranty_template_id', 'Plan'),
             ('warranty_provider_id', 'Provider'), ('warranty_contract_no', 'Contract no'),
             ('warranty_start_date', 'Start'), ('warranty_end_date', 'End'), ('warranty_period_months', 'Months'),
             ('warranty_support_contact', 'Support'), ('warranty_coverage_notes', 'Coverage')]
        A = [('amc_status', 'AMC'), ('amc_vendor_id', 'AMC vendor'), ('amc_start_date', 'AMC start'),
             ('amc_end_date', 'AMC end'), ('amc_nature', 'Nature')]
        D = [('depreciation_method', 'Method'), ('useful_life_years', 'Useful life (years)'),
             ('depreciation_rate', 'Declining rate %'), ('salvage_value_percent', 'Salvage %'),
             ('salvage_value', 'Salvage value'), ('accumulated_depreciation', 'Accumulated'),
             ('current_value', 'Current value'), ('depreciation_percentage', 'Depreciated %')]
        AC = [('accounting_asset_id', 'Accounting asset'), ('accounting_asset_state', 'Status'),
              ('accounting_journal_id', 'Journal'), ('accounting_account_asset_id', 'Asset account'),
              ('accounting_account_depreciation_id', 'Accumulated dep. account'),
              ('accounting_account_expense_id', 'Expense account'), ('accounting_entry_count', 'Journal entries')]
        sched = []
        if 'depreciation_line_ids' in asset._fields:
            for l in asset.depreciation_line_ids.sorted('id'):
                sched.append({'year': l.year, 'days': l.days, 'amount': _r(l.depreciation_amount, 2),
                              'acc': _r(l.accumulated_value, 2), 'rem': _r(l.remaining_value, 2)})
        board = []
        if 'accounting_line_ids' in asset._fields:
            for l in asset.accounting_line_ids.sorted('sequence'):
                board.append({'date': fields.Date.to_string(l.depreciation_date) if l.depreciation_date else '',
                              'amount': _r(l.amount, 2), 'cum': _r(l.depreciated_value, 2),
                              'entry': l.move_id.name or '', 'posted': bool(l.move_id and l.move_id.state == 'posted')})
        repairs = []
        if 'repair_management_ids' in asset._fields:
            for r in asset.repair_management_ids.sorted('id', reverse=True)[:8]:
                st = dict(r._fields['state'].selection).get(r.state) if 'state' in r._fields else ''
                repairs.append({'name': r.display_name, 'state': st,
                                'date': fields.Datetime.to_string(r.create_date)})
        licenses = []
        if 'license_assignment_ids' in asset._fields:
            for l in asset.license_assignment_ids[:10]:
                licenses.append({'name': l.display_name})
        claims = []
        if 'warranty_claim_ids' in asset._fields:
            for c in asset.warranty_claim_ids[:6]:
                claims.append({'name': c.display_name,
                               'state': dict(c._fields['state'].selection).get(c.state) if 'state' in c._fields else ''})
        return {'procurement': self._360_fields(asset, P, cur), 'warranty': self._360_fields(asset, W, cur),
                'amc': self._360_fields(asset, A, cur), 'depreciation': self._360_fields(asset, D, cur),
                'accounting': self._360_fields(asset, AC, cur), 'schedule': sched, 'board': board,
                'repairs': repairs, 'licenses': licenses, 'claims': claims,
                'po_id': asset.purchase_order_id.id if 'purchase_order_id' in asset._fields and asset.purchase_order_id else False,
                'bill_id': asset.invoice_move_id.id if 'invoice_move_id' in asset._fields and asset.invoice_move_id else False}

    def _360_os_upgrade(self, it):
        reqs = []
        if 'asset.os.upgrade.request' in self.env:
            for r in self.env['asset.os.upgrade.request'].sudo().search([('asset_id', '=', it.id)], order='id desc', limit=10):
                reqs.append({'id': r.id, 'name': r.name, 'state': dict(r._fields['state'].selection).get(r.state),
                             'state_key': r.state, 'from': r.current_os_version or '', 'to': r.target_version or '',
                             'scheduled': self._fmt_val(r, 'scheduled_date') or '',
                             'started': self._fmt_val(r, 'started_date') or '',
                             'completed': self._fmt_val(r, 'completed_date') or '',
                             'result': r.result_os_version or '',
                             'error': (r.error_message or '').strip()[-400:]})
        return {'current': self._fmt_val(it, 'os_name') or '', 'codename': self._fmt_val(it, 'os_codename') or '',
                'available': bool(it.os_upgrade_available) if 'os_upgrade_available' in it._fields else False,
                'available_version': self._fmt_val(it, 'os_upgrade_available_version') or '',
                'available_codename': self._fmt_val(it, 'os_upgrade_available_codename') or '',
                'checked': self._fmt_val(it, 'os_upgrade_checked_date') or '',
                'win11': self._fmt_val(it, 'win11_status') or '', 'requests': reqs}

    def _360_all_fields(self, asset, it):
        """Every filled-in field of the fixed-asset and IT-asset forms, tab by tab -
        new fields on those forms show up here without changing the dashboard."""
        from lxml import etree
        out = []
        if it != asset:
            views = [('Fixed asset', asset, 'general_asset.view_asset_general_form'),
                     ('IT asset', it, 'asset_management.view_asset_asset_form')]
        elif 'is_it_asset' in asset._fields and asset.is_it_asset:
            views = [('IT asset', asset, 'asset_management.view_asset_asset_form')]
        else:
            views = [('Fixed asset', asset, 'general_asset.view_asset_general_form')]
        for title, rec, xmlid in views:
            view = self.env.ref(xmlid, raise_if_not_found=False)
            if not view:
                continue
            try:
                arch = etree.fromstring(rec.get_view(view.id, 'form')['arch'])
            except Exception:  # noqa: BLE001
                continue
            seen = set()

            def collect(node):
                items = []
                for fnode in node.iter('field'):
                    if any(p.tag == 'field' for p in fnode.iterancestors()):
                        continue      # inside a one2many sub-view
                    n = fnode.get('name')
                    if not n or n in seen or fnode.get('invisible') in ('1', 'True', 'true'):
                        continue
                    seen.add(n)
                    v = self._fmt_val(rec, n)
                    if v:
                        items.append({'label': fnode.get('string') or rec._fields[n].string, 'value': v[:200]})
                return items
            sections = []
            for page in arch.iter('page'):
                items = collect(page)
                if items:
                    sections.append({'title': page.get('string') or '', 'items': items})
            sheet = arch.find('.//sheet')
            if sheet is not None:
                general = collect(sheet)       # whatever is outside the notebook
                if general:
                    sections.insert(0, {'title': 'General', 'items': general})
            out.append({'title': '%s · %s' % (title, rec.display_name), 'sections': sections})
        return out

    def _360_details(self, asset, it):
        def dt(v):
            if not v:
                return False
            if isinstance(v, datetime):
                return fields.Datetime.to_string(v)
            if isinstance(v, date):
                return fields.Date.to_string(v)
            return str(v)[:19]
        out = {'storage': [], 'os_patches': [], 'app_updates': [], 'cves': [], 'assignments': [], 'security': [],
               'recent_installs': []}
        if 'storage_volume_ids' in it._fields:
            for v in it.storage_volume_ids:
                out['storage'].append({'name': v.drive_letter or v.drive_label or '/', 'label': v.drive_label or '',
                                       'total': _r(v.total_size), 'used': _r(v.used_space), 'free': _r(v.free_space),
                                       'pct': _r(v.usage_percentage, 0)})
        for f in ('linux_update_ids', 'windows_update_ids', 'macos_update_ids'):
            if f in it._fields and it[f]:
                for u in it[f].filtered(lambda x: x.status in ('pending', 'available', 'detected', False))[:10]:
                    out['os_patches'].append({'name': getattr(u, 'package_name', False) or getattr(u, 'title', '') or u.display_name,
                                              'from': getattr(u, 'current_version', '') or '',
                                              'to': getattr(u, 'new_version', '') or '',
                                              'severity': getattr(u, 'severity', '') or ''})
                break
        if 'app_update_ids' in it._fields:
            for u in it.app_update_ids.filtered(lambda x: x.status in ('available', 'failed', 'queued'))[:10]:
                out['app_updates'].append({'name': u.app_name, 'from': u.installed_version or '',
                                           'to': u.available_version or '', 'status': u.status, 'source': u.source or ''})
        if 'vulnerability_ids' in it._fields:
            for v in it.vulnerability_ids.sorted(lambda x: -(x.cvss_score or 0))[:6]:
                out['cves'].append({'cve': v.cve_ref or '', 'score': _r(v.cvss_score), 'severity': v.severity or '',
                                    'software': '%s %s' % (v.software_name or '', v.software_version or '')})
        hist = self.env['asset.assignment.history'].sudo().search(
            [('asset_id', 'in', list({asset.id, it.id}))], order='date desc', limit=8) \
            if 'asset.assignment.history' in self.env else []
        for h in hist:
            out['assignments'].append({'date': dt(h.date), 'employee': h.employee_id.name or '',
                                       'action': h.action or '', 'note': h.description or ''})
        if 'security_log_ids' in it._fields:
            for l in it.security_log_ids.sorted('id', reverse=True)[:8]:
                out['security'].append({'date': dt(l.timestamp or l.date), 'type': l.log_type or '',
                                        'action': l.action or '', 'text': (l.description or '')[:120]})
        if 'installed_application_ids' in it._fields:
            apps = it.installed_application_ids.filtered('installed_date').sorted('installed_date', reverse=True)[:8] \
                or it.installed_application_ids[:8]
            for a in apps:
                out['recent_installs'].append({'name': a.name, 'version': a.version or '', 'publisher': a.publisher or '',
                                               'date': dt(a.installed_date)})
        out['installed_total'] = len(it.installed_application_ids) if 'installed_application_ids' in it._fields else 0
        out['specs'] = [(k, v) for k, v in (
            ('Device', it.device_name if 'device_name' in it._fields else ''), ('Hostname', it.hostname),
            ('OS', '%s %s' % (it.os_name or '', it.os_version or '')), ('Processor', it.processor),
            ('Memory', '%s GB' % _r(it.ram_size) if it.ram_size else ''),
            ('Storage', '%s GB' % _r(it.total_storage_size) if it.total_storage_size else ''),
            ('Disk type', it.disk_type if 'disk_type' in it._fields else ''),
            ('Battery health', '%s%%' % _r(it.battery_health, 0) if 'battery_health' in it._fields and it.battery_health else ''),
            ('Serial', it.serial_number), ('Asset code', asset.asset_code if 'asset_code' in asset._fields else ''),
        ) if v and str(v).strip()]
        return out

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
                 'share': min(100, _r(m['minutes'] / tot * 100, 0)), 'icon': m['icon']}
                for m in sorted(merged.values(), key=lambda x: -x['minutes'])]
        hourly = [0] * 24
        for sg in segs(sel):
            if sg['state'] != 'active':
                continue
            m = sg['s']
            while m < sg['e']:
                h = int(m // 60)
                nxt = min(sg['e'], (h + 1) * 60)
                hourly[min(h, 23)] += nxt - m
                m = nxt
        prev = recs.filtered(lambda x: x.date == sel.date - timedelta(days=1))[:1]
        return {
            'hourly': [round(v) for v in hourly], 'prev_active': _r(prev.active_minutes, 0) if prev else None,
            'apps_count': len(apps),
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
