# -*- coding: utf-8 -*-
"""
Patch Management dashboard data + compliance history.

Two things live here:

  1. asset.patch.compliance.snapshot — one row per day, storing the fleet
     compliance percentage. Written by a nightly cron. This is what the
     "Compliance Over Time" chart reads; without stored snapshots there is
     no way to draw a trend, because the live data only knows "now".

  2. asset.patch.dashboard — an AbstractModel exposing read-only aggregation
     methods for the OWL dashboard, following the same pattern as the
     existing asset.dashboard model.

Compliance definition
---------------------
A machine is COMPLIANT when it has zero pending patches of severity
`security` or `critical`. Important/optional patches do not affect
compliance — otherwise the number would never reach 100% and would stop
being useful as a signal.

    fleet_compliance = compliant_machines / total_machines * 100
"""

import logging
from datetime import timedelta

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# Severities that count against compliance.
COMPLIANCE_SEVERITIES = ('security', 'critical')

# Statuses that mean "this patch is still outstanding".
MISSING_STATUSES = ('pending', 'allowed')

PLATFORM_SPECS = [
    # (platform key, model, identifier field, label)
    ('windows', 'asset.windows.update', 'kb_number', 'Windows'),
    ('linux', 'asset.linux.update', 'package_name', 'Linux'),
    ('macos', 'asset.macos.update', 'package_name', 'macOS'),
]


class AssetPatchComplianceSnapshot(models.Model):
    _name = 'asset.patch.compliance.snapshot'
    _description = 'Daily Patch Compliance Snapshot'
    _order = 'snapshot_date desc'
    _rec_name = 'snapshot_date'

    snapshot_date = fields.Date(required=True, index=True,
                                default=fields.Date.today)
    total_assets = fields.Integer()
    compliant_assets = fields.Integer()
    compliance_pct = fields.Float(string='Compliance %', digits=(5, 2))

    missing_total = fields.Integer()
    missing_critical = fields.Integer()
    missing_high = fields.Integer()

    _sql_constraints = [
        ('unique_date', 'unique(snapshot_date)',
         'Only one compliance snapshot per day.'),
    ]

    @api.model
    def cron_capture_snapshot(self):
        """Nightly. Stores today's compliance figures for trending."""
        Dash = self.env['asset.patch.dashboard']
        stats = Dash.get_compliance_stats()
        today = fields.Date.today()

        existing = self.search([('snapshot_date', '=', today)], limit=1)
        vals = {
            'snapshot_date': today,
            'total_assets': stats['total_assets'],
            'compliant_assets': stats['compliant_assets'],
            'compliance_pct': stats['compliance_pct'],
            'missing_total': stats['missing_total'],
            'missing_critical': stats['missing_critical'],
            'missing_high': stats['missing_high'],
        }
        if existing:
            existing.write(vals)
        else:
            self.create(vals)
        _logger.info('[Patch] Compliance snapshot: %.1f%%', stats['compliance_pct'])
        return True


class AssetPatchDashboard(models.AbstractModel):
    _name = 'asset.patch.dashboard'
    _description = 'Patch Management Dashboard Data'

    # ══════════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def _iter_platforms(self):
        """Yield (key, Model, id_field, label) for platform models that exist.

        Guards against a platform model being absent so the dashboard still
        renders if, say, macOS updates were never installed.
        """
        for key, model_name, id_field, label in PLATFORM_SPECS:
            if model_name in self.env:
                yield key, self.env[model_name].sudo(), id_field, label

    # ══════════════════════════════════════════════════════════════════════
    # KPI tiles
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def get_kpis(self):
        missing_total = 0
        missing_by_sev = {'security': 0, 'critical': 0,
                          'important': 0, 'optional': 0}
        approved = 0
        deployed = 0
        failed = 0

        for _key, Model, _idf, _label in self._iter_platforms():
            missing_total += Model.search_count([('status', 'in', MISSING_STATUSES)])
            for sev in missing_by_sev:
                missing_by_sev[sev] += Model.search_count([
                    ('status', 'in', MISSING_STATUSES), ('severity', '=', sev),
                ])
            approved += Model.search_count([('status', '=', 'installing')])
            deployed += Model.search_count([('status', '=', 'installed')])
            failed += Model.search_count([('status', '=', 'failed')])

        comp = self.get_compliance_stats()

        return {
            'missing_total': missing_total,
            'missing_critical': missing_by_sev['critical'] + missing_by_sev['security'],
            'missing_high': missing_by_sev['important'],
            'approved': approved,
            'deployed': deployed,
            'failed': failed,
            'compliance_pct': comp['compliance_pct'],
        }

    @api.model
    def get_compliance_stats(self):
        """Fleet compliance — machines with zero outstanding security/critical."""
        Asset = self.env['asset.asset'].sudo()
        assets = Asset.search([('platform', 'in', ['windows', 'linux', 'macos'])])
        total = len(assets)
        if not total:
            return {'total_assets': 0, 'compliant_assets': 0, 'compliance_pct': 0.0,
                    'missing_total': 0, 'missing_critical': 0, 'missing_high': 0}

        non_compliant_ids = set()
        missing_total = missing_critical = missing_high = 0

        for _key, Model, _idf, _label in self._iter_platforms():
            missing_total += Model.search_count([('status', 'in', MISSING_STATUSES)])
            missing_critical += Model.search_count([
                ('status', 'in', MISSING_STATUSES),
                ('severity', 'in', COMPLIANCE_SEVERITIES),
            ])
            missing_high += Model.search_count([
                ('status', 'in', MISSING_STATUSES), ('severity', '=', 'important'),
            ])
            groups = Model.read_group(
                domain=[('status', 'in', MISSING_STATUSES),
                        ('severity', 'in', COMPLIANCE_SEVERITIES)],
                fields=['asset_id'], groupby=['asset_id'],
            )
            for g in groups:
                if g.get('asset_id'):
                    non_compliant_ids.add(g['asset_id'][0])

        compliant = total - len(non_compliant_ids & set(assets.ids))
        return {
            'total_assets': total,
            'compliant_assets': compliant,
            'compliance_pct': round(100.0 * compliant / total, 1),
            'missing_total': missing_total,
            'missing_critical': missing_critical,
            'missing_high': missing_high,
        }

    # ══════════════════════════════════════════════════════════════════════
    # Charts
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def get_missing_by_severity(self):
        buckets = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
        # Map the platform models' four severities onto the dashboard's
        # critical/high/medium/low language used in the design.
        sev_map = {'security': 'critical', 'critical': 'critical',
                   'important': 'high', 'optional': 'low'}
        for _key, Model, _idf, _label in self._iter_platforms():
            groups = Model.read_group(
                domain=[('status', 'in', MISSING_STATUSES)],
                fields=['severity'], groupby=['severity'],
            )
            for g in groups:
                bucket = sev_map.get(g['severity'], 'medium')
                buckets[bucket] += g['severity_count']
        total = sum(buckets.values()) or 1
        return {
            'total': sum(buckets.values()),
            'items': [
                {'label': 'Critical', 'count': buckets['critical'],
                 'pct': round(100.0 * buckets['critical'] / total, 1), 'color': '#ef4444'},
                {'label': 'High', 'count': buckets['high'],
                 'pct': round(100.0 * buckets['high'] / total, 1), 'color': '#f97316'},
                {'label': 'Medium', 'count': buckets['medium'],
                 'pct': round(100.0 * buckets['medium'] / total, 1), 'color': '#eab308'},
                {'label': 'Low', 'count': buckets['low'],
                 'pct': round(100.0 * buckets['low'] / total, 1), 'color': '#3b82f6'},
            ],
        }

    @api.model
    def get_missing_by_platform(self):
        rows = []
        total = 0
        for key, Model, _idf, label in self._iter_platforms():
            count = Model.search_count([('status', 'in', MISSING_STATUSES)])
            rows.append({'platform': key, 'label': label, 'count': count})
            total += count
        total_safe = total or 1
        for r in rows:
            r['pct'] = round(100.0 * r['count'] / total_safe, 1)
        return {'total': total, 'items': rows}

    @api.model
    def get_compliance_trend(self, days=60):
        """Historical compliance for the line chart."""
        Snap = self.env['asset.patch.compliance.snapshot'].sudo()
        since = fields.Date.today() - timedelta(days=days)
        snaps = Snap.search([('snapshot_date', '>=', since)],
                            order='snapshot_date asc')
        return {
            'labels': [s.snapshot_date.strftime('%d %b') for s in snaps],
            'values': [round(s.compliance_pct, 1) for s in snaps],
            'has_data': bool(snaps),
        }

    # ══════════════════════════════════════════════════════════════════════
    # Tables
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def get_top_missing_patches(self, limit=10):
        """Patches missing on the largest number of machines."""
        results = []
        for key, Model, id_field, label in self._iter_platforms():
            groups = Model.read_group(
                domain=[('status', 'in', MISSING_STATUSES)],
                fields=[id_field, 'severity'],
                groupby=[id_field],
                limit=200,
            )
            for g in groups:
                ref = g.get(id_field)
                if not ref:
                    continue
                sample = Model.search([(id_field, '=', ref),
                                       ('status', 'in', MISSING_STATUSES)], limit=1)
                results.append({
                    'patch_id': ref,
                    'name': sample.title or ref,
                    'severity': sample.severity or 'optional',
                    'platform': key,
                    'affects': g[f'{id_field}_count'],
                })
        results.sort(key=lambda r: r['affects'], reverse=True)
        return results[:limit]

    @api.model
    def get_recent_deployments(self, limit=10):
        Dep = self.env['asset.patch.deployment'].sudo()
        deps = Dep.search([], limit=limit, order='create_date desc')
        return [{
            'id': d.id,
            'name': d.name,
            'platform': d.platform,
            'patches': d.patch_count,
            'targets': d.target_count,
            'state': d.state,
            'started_by': d.started_by.name if d.started_by else (
                'SYSTEM' if d.source_policy_id else ''),
            'started_at': d.started_at.strftime('%d-%b-%Y %I:%M %p') if d.started_at else '',
            'progress': round(d.progress_pct, 0),
        } for d in deps]

    @api.model
    def get_deployment_summary(self):
        Dep = self.env['asset.patch.deployment'].sudo()
        return {
            'in_progress': Dep.search_count([('state', '=', 'in_progress')]),
            'pending': Dep.search_count([('state', 'in', ('draft', 'pending'))]),
            'successful': Dep.search_count([('state', '=', 'successful')]),
            'failed': Dep.search_count([('state', 'in', ('failed', 'partial'))]),
        }

    @api.model
    def get_policy_status(self):
        # asset.update.policy ships in the separate auto-patching patch.
        # Degrade gracefully rather than crashing the whole dashboard if it
        # has not been installed.
        if 'asset.update.policy' not in self.env:
            return []
        Policy = self.env['asset.update.policy'].sudo()
        policies = Policy.with_context(active_test=False).search([], order='sequence')
        return [{
            'id': p.id,
            'name': p.name,
            'platform': 'windows',
            'last_run': p.last_run.strftime('%d-%b-%Y %I:%M %p') if p.last_run else 'Never',
            'total_approved': p.total_approved,
            'active': p.active,
        } for p in policies]

    # ══════════════════════════════════════════════════════════════════════
    # Single entry point for the OWL component
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def get_dashboard_data(self):
        return {
            'kpis': self.get_kpis(),
            'by_severity': self.get_missing_by_severity(),
            'by_platform': self.get_missing_by_platform(),
            'trend': self.get_compliance_trend(),
            'deployment_summary': self.get_deployment_summary(),
            'recent_deployments': self.get_recent_deployments(),
            'top_missing': self.get_top_missing_patches(),
            'policies': self.get_policy_status(),
        }
