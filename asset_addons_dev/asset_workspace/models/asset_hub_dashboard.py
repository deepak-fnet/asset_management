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
from datetime import timedelta

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


# ── Dashboard registry ────────────────────────────────────────────────────
# (platform key, label, icon, {view key: action xml_id})
# Actions are resolved at runtime; a missing one is reported rather than
# raising, so the hub still works if a module is uninstalled.
PLATFORMS = [
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
    {
        # Non-IT assets from general_asset. Only 'assets' is offered: these
        # have no agent, so there is no dashboard, live monitoring, antivirus
        # or app deployment to point at. Listing view keys that lead nowhere
        # would just produce buttons that error when pressed.
        'key': 'general', 'label': 'General Assets', 'icon': 'fa-cubes',
        'colour': '#F59F00',
        'views': {
            'assets':      'general_asset.action_asset_general',
            'transfer':    'general_asset.action_asset_transfer',
            'scrap':       'general_asset.action_asset_scrap',
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
        """Totals for the General Assets tile.

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