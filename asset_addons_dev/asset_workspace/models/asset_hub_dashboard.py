# -*- coding: utf-8 -*-
"""
Unified dashboard hub.

The problem it solves
---------------------
There were fifteen separate dashboards, each reachable only through its own
menu, and each showing a slice of the same fleet. Finding "how many Linux
machines are unpatched" meant knowing which of fifteen menus to open.

The approach
------------
One entry point with two filters — platform and view. Selecting a combination
opens the corresponding existing dashboard. The default view is a genuinely
new consolidated summary: fleet totals by platform, and the handful of numbers
that matter across all of them.

Deliberate choice: this does NOT re-implement the fifteen dashboards inside
one component. They already exist, they work, and duplicating their rendering
would mean every future change had to be made twice. The hub is a router with
a summary on the front, not a replacement.
"""

import logging
from datetime import date, timedelta

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


# ── Dashboard registry ────────────────────────────────────────────────────
# (platform key, label, icon, {view key: action xml_id})
# Actions are resolved at runtime; a missing one is reported rather than
# raising, so the hub still works if a module is uninstalled.
PLATFORMS = [
    {
        # Non-IT assets from general_asset. Only 'assets' is offered: these
        # have no agent, so there is no dashboard, live monitoring, antivirus
        # or app deployment to point at. Listing view keys that lead nowhere
        # would just produce buttons that error when pressed.
        'key': 'general', 'label': 'All Asset', 'icon': 'fa-cubes',
        'colour': '#F59F00',
        'views': {
            'assets':      'general_asset.action_asset_general',
            'transfer':    'general_asset.action_asset_transfer',
            'scrap':       'general_asset.action_asset_scrap',
        },
    },
    {
        'key': 'windows', 'label': 'Windows', 'icon': 'fa-windows',
        'colour': '#0078D4',
        'views': {
            'dashboard':   'asset_management.action_asset_dashboard_client',
            'assets':      'asset_management.action_asset_asset',
            'monitoring':  'asset_management.action_windows_monitoring',
            'antivirus':   'asset_management.action_windows_antivirus_dashboard',
            'deployment':  'asset_management.action_app_deployment_dashboard_windows',
        },
    },
    {
        'key': 'linux', 'label': 'Linux', 'icon': 'fa-linux',
        'colour': '#E95420',
        'views': {
            'dashboard':   'asset_management.action_ubuntu_agent_dashboard',
            'assets':      'asset_management.action_ubuntu_asset',
            'monitoring':  'asset_management.action_ubuntu_monitoring',
            'antivirus':   'asset_management.action_linux_antivirus_dashboard',
            'deployment':  'asset_management.action_app_deployment_dashboard_linux',
        },
    },
    {
        'key': 'macos', 'label': 'macOS', 'icon': 'fa-apple',
        'colour': '#555555',
        'views': {
            'dashboard':   'asset_management.action_mac_agent_dashboard',
            'assets':      'asset_management.action_mac_asset',
            'monitoring':  'asset_management.action_mac_monitoring',
            'antivirus':   'asset_management.action_macos_antivirus_dashboard',
            'deployment':  'asset_management.action_app_deployment_dashboard_macos',
        },
    },
    {
        'key': 'cctv', 'label': 'CCTV', 'icon': 'fa-video-camera',
        'colour': '#7048E8',
        'views': {
            'dashboard':   'asset_management.action_cctv_dashboard',
            'assets':      'asset_management.action_asset_camera',
            'monitoring':  'asset_management.action_cctv_monitoring',
        },
    },
    {
        'key': 'network', 'label': 'Network', 'icon': 'fa-sitemap',
        'colour': '#0CA678',
        'views': {
            'dashboard':   'asset_management.action_network_dashboard',
            'assets':      'asset_management.action_asset_network_device',
            'monitoring':  'asset_management.action_network_live_monitoring',
        },
    },
]

VIEW_LABELS = [
    ('dashboard',  'Dashboard',      'fa-tachometer'),
    ('assets',     'Assets',         'fa-list'),
    ('monitoring', 'Live Monitoring', 'fa-heartbeat'),
    ('antivirus',  'Antivirus',      'fa-shield'),
    ('deployment', 'App Deployment', 'fa-download'),
    ('transfer',   'Transfers',      'fa-exchange'),
    ('scrap',      'Scrap',          'fa-trash'),
]

# Platform values as stored on asset.asset, mapped to the hub's keys.
ASSET_PLATFORM_MAP = {
    'windows': ['windows'],
    'linux': ['linux', 'ubuntu'],
    'macos': ['macos', 'mac', 'darwin'],
}


def _heartbeat_timeout(env):
    """Seconds without a sync before a machine counts as offline.

    Mirrors asset_management's _search_agent_status so the hub's numbers agree
    with the per-platform dashboards.
    """
    return int(env['ir.config_parameter'].sudo().get_param(
        'asset_management.agent_heartbeat_timeout', default='180'))


def _is_searchable(model, field_name):
    """True only if `field_name` can appear in a search domain.

    A field being present in `_fields` is NOT enough: a computed field with
    store=False and no `search=` cannot be converted to SQL and raises
    "Cannot convert <field> to SQL because it is not stored". Odoo 19 raises
    this in more paths than 17 did, so test storedness/searchability, not
    mere existence.
    """
    field = model._fields.get(field_name)
    return bool(field) and (field.store or field.search)


class AssetHubDashboard(models.AbstractModel):
    _name = 'asset.hub.dashboard'
    _description = 'Unified Dashboard Hub Data'

    # ══════════════════════════════════════════════════════════════════════
    # Registry
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def get_registry(self):
        """Platforms and their available views, with unresolvable actions
        stripped out so the UI never offers a dead button."""
        result = []
        for platform in PLATFORMS:
            views = []
            for key, label, icon in VIEW_LABELS:
                xml_id = platform['views'].get(key)
                if not xml_id:
                    continue
                action = self.env.ref(xml_id, raise_if_not_found=False)
                if not action:
                    _logger.info('[Hub] %s / %s unavailable (%s not found)',
                                 platform['key'], key, xml_id)
                    continue
                views.append({
                    'key': key, 'label': label, 'icon': icon,
                    'action_id': action.id,
                    'action_type': action._name,
                })
            if views:
                result.append({
                    'key': platform['key'],
                    'label': platform['label'],
                    'icon': platform['icon'],
                    'colour': platform['colour'],
                    'views': views,
                })
        return result

    # ══════════════════════════════════════════════════════════════════════
    # Consolidated summary
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def _count_assets(self, platform_key):
        Asset = self.env['asset.asset'].sudo()
        values = ASSET_PLATFORM_MAP.get(platform_key)
        if not values:
            return {'total': 0, 'online': 0, 'offline': 0}

        domain = [('platform', 'in', values)]
        total = Asset.search_count(domain)

        # "Online" definition varies by install; fall back gracefully.
        #
        # Order matters. asset.asset.is_online is compute/store=False with no
        # search method, so it is NOT usable in a domain. agent_status is also
        # non-stored but DOES define search="_search_agent_status", which
        # resolves to last_sync_time against the configured heartbeat timeout
        # — the same definition the rest of the module uses.
        online = 0
        if _is_searchable(Asset, 'agent_status'):
            online = Asset.search_count(domain + [('agent_status', '=', 'online')])
        elif _is_searchable(Asset, 'is_online'):
            online = Asset.search_count(domain + [('is_online', '=', True)])
        elif _is_searchable(Asset, 'last_sync_time'):
            cutoff = fields.Datetime.now() - timedelta(seconds=_heartbeat_timeout(self.env))
            online = Asset.search_count(
                domain + [('last_sync_time', '!=', False),
                          ('last_sync_time', '>=', cutoff)])
        elif _is_searchable(Asset, 'last_agent_sync'):
            cutoff = fields.Datetime.now() - timedelta(seconds=_heartbeat_timeout(self.env))
            online = Asset.search_count(
                domain + [('last_agent_sync', '>=', cutoff)])

        return {'total': total, 'online': online, 'offline': total - online}

    @api.model
    def _count_other(self, model_name):
        if model_name not in self.env:
            return {'total': 0, 'online': 0, 'offline': 0}
        Model = self.env[model_name].sudo()
        total = Model.search_count([])
        online = 0
        if _is_searchable(Model, 'is_online'):
            online = Model.search_count([('is_online', '=', True)])
        elif _is_searchable(Model, 'connection_status'):
            online = Model.search_count([('connection_status', '=', 'online')])
        elif _is_searchable(Model, 'status'):
            online = Model.search_count([('status', '=', 'online')])
        return {'total': total, 'online': online, 'offline': total - online}

    @api.model
    def _count_general_assets(self):
        """Totals for the All Asset tile.

        These have no agent, so online/offline is meaningless - there is
        nothing reporting in to be online. Assigned vs unassigned is the
        equivalent split that actually tells you something, so it is mapped
        onto the same online/offline keys the tile already renders rather
        than adding a parallel shape the template would have to special-case.
        """
        blank = {'total': 0, 'online': 0, 'offline': 0}
        if 'asset.asset' not in self.env:
            return blank
        Asset = self.env['asset.asset'].sudo()
        if 'is_general_asset' not in Asset._fields:
            # general_asset is not installed; the tile shows zeros rather
            # than breaking the whole dashboard.
            return blank

        domain = [('is_general_asset', '=', True), ('state', '!=', 'scrapped')]
        total = Asset.search_count(domain)
        assigned = Asset.search_count(
            domain + [('assigned_employee_id', '!=', False)])
        return {
            'total': total,
            'online': assigned,
            'offline': total - assigned,
            # The tile renders these two numbers with a label. "online" for a
            # filing cabinet is nonsense, so the wording travels with the
            # data instead of being hardcoded in the template.
            'online_label': 'assigned',
            'offline_label': 'unassigned',
        }

    @api.model
    def get_platform_summary(self):
        rows = []
        for platform in PLATFORMS:
            key = platform['key']
            if key in ASSET_PLATFORM_MAP:
                counts = self._count_assets(key)
            elif key == 'cctv':
                counts = self._count_other('asset.camera')
            elif key == 'network':
                counts = self._count_other('asset.network.device')
            elif key == 'general':
                counts = self._count_general_assets()
            else:
                counts = {'total': 0, 'online': 0, 'offline': 0}

            rows.append({
                'key': key,
                'label': platform['label'],
                'icon': platform['icon'],
                'colour': platform['colour'],
                # Default wording for agent-reporting platforms; _count_*
                # helpers may override it (see _count_general_assets).
                'online_label': 'online',
                'offline_label': 'offline',
                **counts,
            })
        return rows

    @api.model
    def get_fleet_health(self):
        """The handful of numbers worth seeing across every platform.

        Each block is guarded independently: a module that is not installed
        contributes zeros rather than breaking the whole panel.
        """
        health = {
            'patches_pending': 0,
            'patches_failed': 0,
            'app_updates_pending': 0,
            'vuln_critical': 0,
            'vuln_high': 0,
            'vuln_review': 0,
            'alerts_active': 0,
            'compliance_pct': None,
            'assets_never_scanned': 0,
            'unmanaged_endpoints': 0,
            'agent_missing_purchased': 0,
        }

        # ── Patches ───────────────────────────────────────────────────────
        for model_name in ('asset.windows.update', 'asset.linux.update',
                           'asset.macos.update'):
            if model_name in self.env:
                Model = self.env[model_name].sudo()
                health['patches_pending'] += Model.search_count(
                    [('status', 'in', ('pending', 'allowed'))])
                health['patches_failed'] += Model.search_count(
                    [('status', '=', 'failed')])

        # ── App updates ───────────────────────────────────────────────────
        if 'asset.app.update' in self.env:
            health['app_updates_pending'] = self.env['asset.app.update'].sudo(
            ).search_count([('status', 'in', ('available', 'failed'))])

        # ── Vulnerabilities ───────────────────────────────────────────────
        if 'asset.vulnerability' in self.env:
            Vuln = self.env['asset.vulnerability'].sudo()
            health['vuln_critical'] = Vuln.search_count(
                [('state', '=', 'open'), ('severity', '=', 'critical')])
            health['vuln_high'] = Vuln.search_count(
                [('state', '=', 'open'), ('severity', '=', 'high')])
            health['vuln_review'] = Vuln.search_count(
                [('state', '=', 'needs_review')])

        # ── Telemetry alerts ──────────────────────────────────────────────
        if 'asset.telemetry.alert.log' in self.env:
            health['alerts_active'] = self.env[
                'asset.telemetry.alert.log'].sudo().search_count(
                [('state', '=', 'active')])

        # ── Compliance ────────────────────────────────────────────────────
        if 'asset.patch.dashboard' in self.env:
            try:
                stats = self.env['asset.patch.dashboard'].get_compliance_stats()
                health['compliance_pct'] = stats.get('compliance_pct')
            except Exception as exc:
                _logger.debug('[Hub] compliance unavailable: %s', exc)

        # ── Never scanned ─────────────────────────────────────────────────
        Asset = self.env['asset.asset'].sudo()
        if _is_searchable(Asset, 'last_vuln_scan'):
            health['assets_never_scanned'] = Asset.search_count([
                ('platform', 'in', ('windows', 'linux', 'macos')),
                ('last_vuln_scan', '=', False),
            ])

        # ── Unmanaged endpoints ───────────────────────────────────────────
        # Network discovery flags a host as unmanaged when it answers ping
        # but not SNMP, its TTL/open-port signature looks like a computer or
        # phone, and its IP/MAC does not match any asset.asset or
        # mobile.device already reporting through an agent. In practice:
        # a laptop or phone on the network with no agent installed.
        if 'asset.network.device' in self.env:
            health['unmanaged_endpoints'] = self.env[
                'asset.network.device'].sudo().search_count(
                [('is_unmanaged_endpoint', '=', True)])

        # ── Agent missing on a purchased IT asset ─────────────────────────
        # is_general_asset/is_it_asset/it_asset_id only exist once
        # general_asset is installed - a fixed asset (registered through
        # procurement, category flagged IT) that has no it_asset_id yet
        # means the agent was never installed/never checked in on that
        # machine, so it never linked itself. Different from "Agent
        # missing" above, which is a device seen ON THE NETWORK with no
        # matching asset at all - this one is a known, purchased asset with
        # nothing reporting for it yet.
        if _is_searchable(Asset, 'it_asset_id'):
            health['agent_missing_purchased'] = Asset.search_count([
                ('is_general_asset', '=', True),
                ('is_it_asset', '=', True),
                ('it_asset_id', '=', False),
            ])

        return health

    @api.model
    def get_attention_items(self, limit=8):
        """Machines that need a human to look at them, worst first."""
        Asset = self.env['asset.asset'].sudo()
        if not _is_searchable(Asset, 'security_status'):
            return []

        assets = Asset.search(
            [('security_status', 'in', ('at_risk', 'attention'))],
            limit=limit, order='security_status, id')

        return [{
            'id': a.id,
            'name': a.display_name,
            'platform': a.platform or '',
            'status': a.security_status,
            'note': getattr(a, 'security_note', '') or '',
        } for a in assets]

    @api.model
    def get_hub_data(self):
        return {
            'registry': self.get_registry(),
            'platforms': self.get_platform_summary(),
            'health': self.get_fleet_health(),
            'attention': self.get_attention_items(),
        }

    # ══════════════════════════════════════════════════════════════════════
    # All Asset Dashboard - one KPI-card summary across every kind of asset
    # this suite tracks (IT/agent-reported, general/non-IT, IoT), rather than
    # the platform-by-platform breakdown get_hub_data() already provides.
    # Same spirit as the separate asset_advanced_dashboard module's Analytics
    # Dashboard (KPI cards + click-through), built fresh against THIS
    # module's own asset.asset instead - that module's dashboard is bound to
    # an unrelated, incompatible asset.asset (different states/fields
    # entirely), so its code could not be reused directly.
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def get_all_asset_summary(self):
        Asset = self.env['asset.asset'].sudo()

        total_assets = Asset.search_count([])

        summary = {
            'total_assets': total_assets,
            'general_assets': 0,
            'it_assets': 0,
            'iot_devices': 0,
            'physical_verification_pending': 0,
            'categories': [],
        }

        # General (non-IT) vs IT/agent-reported split - is_general_asset only
        # exists once general_asset is installed.
        if _is_searchable(Asset, 'is_general_asset'):
            summary['general_assets'] = Asset.search_count(
                [('is_general_asset', '=', True)])
        summary['it_assets'] = max(0, total_assets - summary['general_assets'])

        if _is_searchable(Asset, 'is_iot_device'):
            summary['iot_devices'] = Asset.search_count(
                [('is_iot_device', '=', True)])

        if 'physical.verification' in self.env:
            summary['physical_verification_pending'] = self.env[
                'physical.verification'].sudo().search_count(
                [('state', '!=', 'done')])

        # Top categories by asset count - gives the KPI row something to
        # point at besides raw totals, same idea as the platform tiles.
        if 'category_id' in Asset._fields:
            grouped = Asset.sudo()._read_group(
                [('category_id', '!=', False)], ['category_id'],
                ['__count'], order='__count desc', limit=6)
            summary['categories'] = [
                {'id': cat.id, 'name': cat.display_name, 'count': count}
                for cat, count in grouped
            ]

        return summary

    @api.model
    def open_action_by_xmlid(self, xml_id):
        """Generic click-through for the All Asset Dashboard's KPI cards.

        Several of the actions a card should open (general_asset's own
        asset list, Physical Verification) live in a module that is not a
        hard dependency of this one - resolved at runtime the same way
        open_dashboard() already does for platform tiles, rather than a
        static XML reference that would break installation wherever that
        module is absent.
        """
        action = self.env.ref(xml_id, raise_if_not_found=False)
        if not action:
            return {'error': f'Action not installed: {xml_id}'}
        return {'action_id': action.id}

    @api.model
    def open_iot_devices(self):
        """IoT Devices KPI card - a plain domain filter, not a fixed action,
        since 'assets flagged is_iot_device' isn't its own menu/action
        anywhere else in the suite.
        """
        return {
            'type': 'ir.actions.act_window',
            'name': _('IoT Devices'),
            'res_model': 'asset.asset',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('is_iot_device', '=', True)],
        }

    @api.model
    def open_category_assets(self, category_id):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Assets'),
            'res_model': 'asset.asset',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('category_id', '=', category_id)],
        }

    # ══════════════════════════════════════════════════════════════════════
    # Asset Analytics - filters + KPI cards + charts + age profile, same
    # concept as the standalone asset_advanced_dashboard module's Analytics
    # Dashboard. That module's own dashboard is bound to asset.addition (a
    # different, unrelated model from a separate legacy "asset" addon, not
    # this suite's asset.asset) - rebuilt here against our own model/fields
    # instead of reused directly.
    # ══════════════════════════════════════════════════════════════════════
    ASSET_STATES = [
        ('draft', 'Draft'),
        ('assigned', 'Assigned'),
        ('maintenance', 'Maintenance'),
        ('scrapped', 'Scrapped'),
    ]

    AGE_BUCKETS = [
        (1, '0-3 yrs', None, 3),
        (2, '3-5 yrs', 3, 5),
        (3, '5-10 yrs', 5, 10),
        (4, '10-15 yrs', 10, 15),
        (5, '15+ yrs', 15, None),
    ]

    @staticmethod
    def _years_ago(today, years):
        """today minus N years, as a day count - avoids .replace(year=...)
        raising on Feb 29 landing on a non-leap year.
        """
        return today - timedelta(days=round(years * 365.25))

    def _analytics_domain(self, filters):
        filters = filters or {}
        domain = []
        for key, field in (
            ('department_id', 'department_id'),
            ('plant_id', 'plant_id'),
            ('location_id', 'location_id'),
        ):
            value = filters.get(key)
            if value and (field != 'plant_id' or 'plant_id' in self.env['asset.asset']._fields):
                domain.append((field, '=', int(value)))
        if filters.get('state'):
            domain.append(('state', '=', filters['state']))
        if filters.get('date_from'):
            domain.append(('purchase_date', '>=', filters['date_from']))
        if filters.get('date_to'):
            domain.append(('purchase_date', '<=', filters['date_to']))
        return domain

    @staticmethod
    def _grouped(model, domain, field):
        """[(label, count)] for a many2one grouping, biggest first."""
        rows = model._read_group(domain, [field], ['__count'])
        out = []
        for value, count in rows:
            if not value:
                label = 'Unassigned'
            elif hasattr(value, 'display_name'):
                label = value.display_name
            else:
                label = str(value)
            out.append((label, count))
        out.sort(key=lambda row: -row[1])
        return out

    @staticmethod
    def _series(pairs, limit=8):
        """Pack (label, count) pairs into a chart series, folding the tail
        into a single "Other" bucket past `limit` slots - a categorical
        palette only has so many distinct colours.
        """
        head, tail = pairs[:limit], pairs[limit:]
        labels = [label for label, _count in head]
        data = [count for _label, count in head]
        if tail:
            labels.append('Other')
            data.append(sum(count for _label, count in tail))
        return {'labels': labels, 'data': data}

    @api.model
    def get_asset_analytics_filters(self):
        def options(model_name, domain=None):
            if model_name not in self.env:
                return []
            return [{'id': rec.id, 'name': rec.display_name}
                    for rec in self.env[model_name].sudo().search(domain or [])]

        return {
            'departments': options('hr.department'),
            'plants': options('plant.master'),
            'locations': options('asset.location'),
            'states': [{'value': code, 'label': label}
                      for code, label in self.ASSET_STATES],
        }

    @api.model
    def get_asset_analytics_data(self, filters=None):
        return {
            'kpis': self._analytics_kpis(filters),
            'charts': self._analytics_charts(filters),
            'age_profile': self._analytics_age_profile(filters),
            'risk': self._analytics_risk(filters),
        }

    # ── Data Quality & Risk ─────────────────────────────────────────────
    # Register hygiene (missing tag/serial/location/category - can't be
    # tracked properly without these) and things about to expire (warranty
    # always available; AMC/Licence only once general_asset is installed).
    RISK_ITEMS = [
        ('missing_tag', 'Missing Tag', 'tag_number'),
        ('missing_serial', 'Missing Serial', 'serial_number'),
        ('missing_location', 'Missing Location', 'location_id'),
        ('missing_category', 'Missing Category', 'category_id'),
    ]

    def _analytics_risk(self, filters):
        Asset = self.env['asset.asset'].sudo()
        domain = self._analytics_domain(filters) + [('state', '!=', 'scrapped')]
        today = fields.Date.today()
        soon = today + timedelta(days=30)

        result = []
        for key, label, field_name in self.RISK_ITEMS:
            if field_name not in Asset._fields:
                continue
            field = Asset._fields[field_name]
            if field.type == 'many2one':
                extra = [(field_name, '=', False)]
            else:
                extra = ['|', (field_name, '=', False), (field_name, '=', '')]
            result.append({
                'key': key, 'label': label,
                'count': Asset.search_count(domain + extra),
            })

        def expiring(key, label, end_field, flag_field=None):
            if end_field not in Asset._fields:
                return
            extra = [(end_field, '<=', soon), (end_field, '>=', today)]
            if flag_field and flag_field in Asset._fields:
                extra.append((flag_field, '=', True))
            result.append({
                'key': key, 'label': label,
                'count': Asset.search_count(domain + extra),
            })

        expiring('warranty_expiring', 'Warranty Expiring (30d)', 'warranty_end_date')
        expiring('amc_expiring', 'AMC Expiring (30d)', 'amc_end_date', 'is_amc')
        expiring('licence_expiring', 'Licence Expiring (30d)', 'licence_end_date', 'is_licence')

        old_cutoff = self._years_ago(today, 15)
        result.append({
            'key': 'old_assets', 'label': 'Older Than 15 Years',
            'count': Asset.search_count(domain + [('purchase_date', '<=', old_cutoff)]),
        })

        return result

    @api.model
    def open_risk_bucket(self, filters, key):
        Asset = self.env['asset.asset'].sudo()
        domain = self._analytics_domain(filters) + [('state', '!=', 'scrapped')]
        today = fields.Date.today()
        soon = today + timedelta(days=30)

        field_by_key = {r[0]: r[2] for r in self.RISK_ITEMS}
        if key in field_by_key:
            field_name = field_by_key[key]
            field = Asset._fields[field_name]
            if field.type == 'many2one':
                domain += [(field_name, '=', False)]
            else:
                domain += ['|', (field_name, '=', False), (field_name, '=', '')]
        elif key == 'warranty_expiring':
            domain += [('warranty_end_date', '<=', soon), ('warranty_end_date', '>=', today)]
        elif key == 'amc_expiring':
            domain += [('is_amc', '=', True), ('amc_end_date', '<=', soon),
                      ('amc_end_date', '>=', today)]
        elif key == 'licence_expiring':
            domain += [('is_licence', '=', True), ('licence_end_date', '<=', soon),
                      ('licence_end_date', '>=', today)]
        elif key == 'old_assets':
            domain += [('purchase_date', '<=', self._years_ago(today, 15))]

        return {
            'type': 'ir.actions.act_window',
            'name': _('Assets'),
            'res_model': 'asset.asset',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': domain,
        }

    def _analytics_kpis(self, filters):
        Asset = self.env['asset.asset'].sudo()
        domain = self._analytics_domain(filters)

        def count(extra):
            return Asset.search_count(domain + extra)

        live = domain + [('state', '!=', 'scrapped')]
        worth = 0.0
        if 'purchase_cost' in Asset._fields:
            worth = sum(Asset.search(live).mapped('purchase_cost'))

        under_transfer = 0
        if 'asset.transfer' in self.env:
            under_transfer = self.env['asset.transfer'].sudo().search_count(
                [('state', 'not in', ('done', 'cancelled'))])

        removals_open = 0
        if 'asset.scrap' in self.env:
            removals_open = self.env['asset.scrap'].sudo().search_count(
                [('state', 'not in', ('done', 'cancelled'))])

        verification_due = 0
        if 'physical.verification' in self.env:
            verification_due = self.env['physical.verification'].sudo(
            ).search_count([('state', '!=', 'done')])

        return {
            'total_assets': Asset.search_count(live),
            'worth': worth,
            'active': count([('state', '=', 'assigned')]),
            'pending_approval': count([('is_submit', '=', False)])
                if 'is_submit' in Asset._fields else 0,
            'assigned': count([('assigned_employee_id', '!=', False)]),
            'unassigned': count(
                [('assigned_employee_id', '=', False), ('state', '!=', 'scrapped')]),
            'under_transfer': under_transfer,
            'verification_due': verification_due,
            'removals_open': removals_open,
            'removed': count([('state', '=', 'scrapped')]),
        }

    def _analytics_charts(self, filters):
        Asset = self.env['asset.asset'].sudo()
        domain = self._analytics_domain(filters)

        by_code = {code: 0 for code, _label in self.ASSET_STATES}
        for value, cnt in Asset._read_group(domain, ['state'], ['__count']):
            by_code[value] = cnt
        state_series = {'labels': [], 'data': []}
        for code, label in self.ASSET_STATES:
            if by_code.get(code):
                state_series['labels'].append(label)
                state_series['data'].append(by_code[code])

        live = domain + [('state', '!=', 'scrapped')]
        charts = {
            'by_state': state_series,
            'by_department': self._series(self._grouped(Asset, live, 'department_id')),
            'by_category': self._series(self._grouped(Asset, live, 'category_id')),
            'monthly_trend': self._analytics_monthly_trend(Asset, domain),
        }
        if 'location_id' in Asset._fields:
            charts['by_location'] = self._series(
                self._grouped(Asset, live, 'location_id'))
        return charts

    def _analytics_monthly_trend(self, Asset, domain):
        """Assets acquired (by purchase_date) per month, last 12 months."""
        today = fields.Date.today()
        first = (today.replace(day=1) - timedelta(days=365)).replace(day=1)
        rows = Asset._read_group(
            domain + [('purchase_date', '>=', first)],
            ['purchase_date:month'], ['__count'])
        counts = {month.strftime('%Y-%m'): cnt for month, cnt in rows if month}

        labels, data = [], []
        year, month = today.year, today.month
        months = []
        for _i in range(12):
            months.append((year, month))
            month -= 1
            if month == 0:
                month, year = 12, year - 1
        for year, month in reversed(months):
            key = date(year, month, 1)
            labels.append(key.strftime('%b %y'))
            data.append(counts.get(key.strftime('%Y-%m'), 0))
        return {'labels': labels, 'data': data}

    def _analytics_age_profile(self, filters):
        Asset = self.env['asset.asset'].sudo()
        base = self._analytics_domain(filters) + [('state', '!=', 'scrapped')]
        today = fields.Date.today()

        result = []
        for bucket_id, label, low, high in self.AGE_BUCKETS:
            bucket_domain = list(base)
            if low is not None:
                bucket_domain.append(
                    ('purchase_date', '<=', self._years_ago(today, low)))
            if high is not None:
                bucket_domain.append(
                    ('purchase_date', '>', self._years_ago(today, high)))
            assets = Asset.search(bucket_domain)
            breakdown = [
                {'name': name, 'count': cnt}
                for name, cnt in self._grouped(Asset, bucket_domain, 'category_id')[:4]
            ]
            worth = (sum(assets.mapped('purchase_cost'))
                     if 'purchase_cost' in Asset._fields else 0.0)
            result.append({
                'id': bucket_id,
                'name': label,
                'count': len(assets),
                'total_value': worth,
                'breakdown': breakdown,
            })
        return result

    @api.model
    def open_analytics_bucket(self, filters, low, high):
        """Drill-down for an Age Profile bar - reapplies the same filters
        plus that bucket's purchase_date window.
        """
        domain = self._analytics_domain(filters) + [('state', '!=', 'scrapped')]
        today = fields.Date.today()
        if low is not None:
            domain.append(('purchase_date', '<=', self._years_ago(today, low)))
        if high is not None:
            domain.append(('purchase_date', '>', self._years_ago(today, high)))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Assets'),
            'res_model': 'asset.asset',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': domain,
        }

    # ══════════════════════════════════════════════════════════════════════
    # Routing
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def open_dashboard(self, platform_key, view_key):
        """Return the action for a platform/view combination."""
        platform = next((p for p in PLATFORMS if p['key'] == platform_key), None)
        if not platform:
            return {'error': f'Unknown platform: {platform_key}'}

        xml_id = platform['views'].get(view_key)
        if not xml_id:
            return {'error': f'{platform["label"]} has no {view_key} view'}

        action = self.env.ref(xml_id, raise_if_not_found=False)
        if not action:
            return {'error': f'Action not installed: {xml_id}'}

        return {'action_id': action.id}

    @api.model
    def get_agent_missing_purchased_action(self):
        """The 'Agent Missing (Purchased Assets)' health tile action.

        Uses general_asset's own form/list views (view_asset_general_list /
        view_asset_general_form) rather than asset.asset's default views, so
        clicking through opens the same layout as the General Assets
        (now "All Asset") screen - not the generic list/form neither of
        which knows about tag_number, sub_category_id, it_asset_id etc.

        general_asset is not a hard dependency of this module, so the views
        are looked up rather than referenced in static XML - falls back to
        the default (unspecified) views if general_asset is absent, same
        as the tile itself falling back to a zero count in get_fleet_health.
        """
        views = [(False, 'list'), (False, 'form')]
        list_view = self.env.ref(
            'general_asset.view_asset_general_list', raise_if_not_found=False)
        form_view = self.env.ref(
            'general_asset.view_asset_general_form', raise_if_not_found=False)
        if list_view and form_view:
            views = [(list_view.id, 'list'), (form_view.id, 'form')]

        return {
            'type': 'ir.actions.act_window',
            'name': _('Agent Missing (Purchased Assets)'),
            'res_model': 'asset.asset',
            'view_mode': 'list,form',
            'views': views,
            'domain': [
                ('is_general_asset', '=', True),
                ('is_it_asset', '=', True),
                ('it_asset_id', '=', False),
            ],
        }