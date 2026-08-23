# -*- coding: utf-8 -*-
"""
Raw agent telemetry — store everything, model nothing.

The problem this solves
-----------------------
The agent currently reports a fixed set of fields, each mapped to a column on
asset.asset. Every time you want one more piece of data (load average, failed
services, top processes, sensor temperatures) that means a new field, a new
migration, a new view change.

This model takes the opposite approach: the agent posts whatever it collected
as one JSON blob, Odoo stores it verbatim, and the UI renders it as a
navigable list. No schema, no migrations. Adding `sensors` output to the agent
requires zero Odoo changes.

Trade-off, stated plainly
-------------------------
JSON in a single column is not queryable the way real columns are. You cannot
filter assets by "load average > 4" without either a computed field or a
PostgreSQL JSON operator in a custom domain. This is a viewer and an audit
trail, not a reporting surface.

If a particular value turns out to matter operationally — you find yourself
wanting to filter or alert on it — promote that one value to a real field.
Keep the JSON for everything else.
"""

import json
import logging
from datetime import timedelta

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Keep this many historical snapshots per asset. Older ones are pruned by the
# cron. Raise it if you want longer history; each snapshot is a few KB.
SNAPSHOT_RETENTION = 20

# Reject payloads larger than this, so a runaway agent cannot fill the disk.
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024   # 2 MB


class AssetAssetTelemetry(models.Model):
    _inherit = 'asset.asset'

    agent_telemetry = fields.Json(
        string='Agent Telemetry',
        help='The most recent raw payload posted by the agent. Whatever the '
             'agent collected, stored verbatim.',
    )
    agent_telemetry_at = fields.Datetime(
        string='Telemetry Received', readonly=True,
    )
    agent_telemetry_size = fields.Integer(
        string='Payload Size (bytes)', readonly=True,
    )
    agent_telemetry_pretty = fields.Text(
        string='Telemetry (formatted)',
        compute='_compute_telemetry_pretty',
        help='Pretty-printed copy for reading and copy-paste. Not stored.',
    )
    telemetry_snapshot_ids = fields.One2many(
        'asset.telemetry.snapshot', 'asset_id', string='Telemetry History',
    )
    telemetry_snapshot_count = fields.Integer(
        compute='_compute_snapshot_count',
    )

    @api.depends('agent_telemetry')
    def _compute_telemetry_pretty(self):
        for asset in self:
            if not asset.agent_telemetry:
                asset.agent_telemetry_pretty = (
                    'No telemetry received yet.\n\n'
                    'Deploy agent_snippets/telemetry.py to the agent and call '
                    'report_telemetry() once per sync cycle.'
                )
                continue
            try:
                asset.agent_telemetry_pretty = json.dumps(
                    asset.agent_telemetry, indent=2, sort_keys=False,
                    ensure_ascii=False, default=str,
                )
            except Exception as exc:
                asset.agent_telemetry_pretty = f'Could not format: {exc}'

    @api.depends('telemetry_snapshot_ids')
    def _compute_snapshot_count(self):
        for asset in self:
            asset.telemetry_snapshot_count = len(asset.telemetry_snapshot_ids)

    # ══════════════════════════════════════════════════════════════════════
    # Ingest
    # ══════════════════════════════════════════════════════════════════════
    def record_telemetry(self, payload, keep_snapshot=True):
        """Store a telemetry payload against this asset."""
        self.ensure_one()

        try:
            raw = json.dumps(payload, default=str)
        except Exception as exc:
            raise UserError(f'Telemetry payload is not JSON-serialisable: {exc}')

        size = len(raw.encode('utf-8'))
        if size > MAX_PAYLOAD_BYTES:
            raise UserError(
                f'Telemetry payload is {size:,} bytes, over the '
                f'{MAX_PAYLOAD_BYTES:,} byte limit. Trim what the agent sends.'
            )

        now = fields.Datetime.now()
        self.write({
            'agent_telemetry': payload,
            'agent_telemetry_at': now,
            'agent_telemetry_size': size,
        })

        if keep_snapshot:
            self.env['asset.telemetry.snapshot'].sudo().create({
                'asset_id': self.id,
                'captured_at': now,
                'payload': payload,
                'size_bytes': size,
            })
            self._prune_snapshots()

        return True

    def _prune_snapshots(self):
        """Keep only the most recent SNAPSHOT_RETENTION snapshots."""
        self.ensure_one()
        Snapshot = self.env['asset.telemetry.snapshot'].sudo()
        keep = Snapshot.search(
            [('asset_id', '=', self.id)],
            order='captured_at desc', limit=SNAPSHOT_RETENTION,
        )
        stale = Snapshot.search([
            ('asset_id', '=', self.id),
            ('id', 'not in', keep.ids),
        ])
        if stale:
            stale.unlink()

    # ══════════════════════════════════════════════════════════════════════
    # Actions
    # ══════════════════════════════════════════════════════════════════════
    def action_view_telemetry_history(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Telemetry History — {self.display_name}',
            'res_model': 'asset.telemetry.snapshot',
            'view_mode': 'list,form',
            'domain': [('asset_id', '=', self.id)],
        }

    def action_clear_telemetry(self):
        """Wipe stored telemetry for these assets."""
        if not self.env.user.has_group('asset_management.group_asset_manager'):
            raise UserError('Only Asset Managers can clear telemetry.')
        self.env['asset.telemetry.snapshot'].sudo().search(
            [('asset_id', 'in', self.ids)]).unlink()
        self.write({
            'agent_telemetry': False,
            'agent_telemetry_at': False,
            'agent_telemetry_size': 0,
        })
        return True

    # ══════════════════════════════════════════════════════════════════════
    # Convenience accessors — for anything that wants a specific value
    # ══════════════════════════════════════════════════════════════════════
    def telemetry_get(self, path, default=None):
        """Read a nested value using a dotted path.

            asset.telemetry_get('memory.available_gb')
            asset.telemetry_get('disks.0.mount')
        """
        self.ensure_one()
        data = self.agent_telemetry or {}
        for part in path.split('.'):
            if isinstance(data, dict):
                if part not in data:
                    return default
                data = data[part]
            elif isinstance(data, list):
                try:
                    data = data[int(part)]
                except (ValueError, IndexError):
                    return default
            else:
                return default
        return data


class AssetTelemetrySnapshot(models.Model):
    _name = 'asset.telemetry.snapshot'
    _description = 'Agent Telemetry Snapshot'
    _order = 'captured_at desc, id desc'
    _rec_name = 'display_name'

    asset_id = fields.Many2one(
        'asset.asset', required=True, ondelete='cascade', index=True,
    )
    captured_at = fields.Datetime(required=True, index=True,
                                  default=fields.Datetime.now)
    payload = fields.Json(required=True)
    size_bytes = fields.Integer()

    display_name = fields.Char(compute='_compute_display_name')
    payload_pretty = fields.Text(compute='_compute_payload_pretty')
    section_summary = fields.Char(
        compute='_compute_section_summary',
        help='Top-level keys present in this snapshot.',
    )

    @api.depends('asset_id', 'captured_at')
    def _compute_display_name(self):
        for rec in self:
            stamp = rec.captured_at.strftime('%d %b %Y %H:%M:%S') \
                if rec.captured_at else '?'
            rec.display_name = f'{rec.asset_id.display_name or "?"} — {stamp}'

    @api.depends('payload')
    def _compute_payload_pretty(self):
        for rec in self:
            try:
                rec.payload_pretty = json.dumps(
                    rec.payload or {}, indent=2, ensure_ascii=False, default=str)
            except Exception as exc:
                rec.payload_pretty = f'Could not format: {exc}'

    @api.depends('payload')
    def _compute_section_summary(self):
        for rec in self:
            data = rec.payload or {}
            if isinstance(data, dict):
                keys = list(data.keys())
                rec.section_summary = ', '.join(keys[:8]) + (
                    f' … (+{len(keys) - 8})' if len(keys) > 8 else '')
            else:
                rec.section_summary = type(data).__name__

    # ══════════════════════════════════════════════════════════════════════
    # Housekeeping
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def cron_prune_snapshots(self, days=30):
        """Delete snapshots older than `days`, regardless of per-asset count."""
        cutoff = fields.Datetime.now() - timedelta(days=days)
        stale = self.search([('captured_at', '<', cutoff)])
        count = len(stale)
        if stale:
            stale.unlink()
            _logger.info('[Telemetry] Pruned %s snapshot(s) older than %s days.',
                         count, days)
        return True
