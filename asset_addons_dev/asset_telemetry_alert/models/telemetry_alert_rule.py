# -*- coding: utf-8 -*-
"""
Telemetry alert rules — conditions against the agent's JSON payload.

Why this exists
---------------
custom_asset_alert_mail watches real Odoo fields. That works, but it means
every value you want to alert on must first become a column on asset.asset,
and its MONITORED_FIELDS tuple has to be edited and the module upgraded.

The agent now posts everything as JSON, so most of what you would want to
alert on has no column at all: per-mount disk usage, load average, failed
systemd units, top process memory, sensor temperatures.

This module lets a rule point at a path inside that JSON instead.

    memory.virtual.percent          > 90
    disks.mounts[*].percent         > 85      (any mount)
    cpu.load_average.5min           > 4
    services.failed_count           > 0
    hardware.battery.percent        < 60
    packages.reboot_required        == True

Two safety features that matter in practice
-------------------------------------------
COOLDOWN. Telemetry arrives every 15 minutes. Without a cooldown a machine
sitting at 91% disk mails someone 96 times a day, and they stop reading.
Default is 24 hours per rule per asset.

RECOVERY. A rule that has fired stays "active" until the condition clears,
at which point an optional recovery mail is sent. Without this you cannot
tell "still broken" from "broken again".
"""

import logging
import re
from datetime import timedelta

from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)

# Path segment forms:  key   |   key[0]   |   key[*]
_SEGMENT_RE = re.compile(r'^([^\[\]]+)(?:\[(\*|\d+)\])?$')


def resolve_path(data, path):
    """Resolve a dotted JSON path. Returns (found, value).

        memory.virtual.percent      -> single value
        disks.mounts[*].percent     -> list of values, one per mount
        disks.mounts[0].percent     -> value from the first mount

    `found` is False when any segment is missing, which callers must treat
    as "cannot evaluate" rather than as a zero.
    """
    if not path:
        return False, None

    current = data
    for raw_segment in path.split('.'):
        match = _SEGMENT_RE.match(raw_segment.strip())
        if not match:
            return False, None
        key, index = match.group(1), match.group(2)

        # ── Step into the key ────────────────────────────────────────────
        if isinstance(current, list):
            # Fan out over a list produced by an earlier [*]
            collected = []
            for item in current:
                if isinstance(item, dict) and key in item:
                    collected.append(item[key])
            if not collected:
                return False, None
            current = collected
            if index is None:
                continue
        elif isinstance(current, dict):
            if key not in current:
                return False, None
            current = current[key]
        else:
            return False, None

        # ── Apply the index, if any ──────────────────────────────────────
        if index is not None:
            if not isinstance(current, list):
                return False, None
            if index == '*':
                continue                        # keep the whole list
            try:
                current = current[int(index)]
            except (ValueError, IndexError):
                return False, None

    return True, current


def collect_paths(data, prefix='', max_depth=6, _depth=0):
    """Enumerate every leaf path in a payload, for the path browser.

    Lists are represented once with [*] rather than per index, so the output
    stays readable on a payload with 40 processes.
    """
    paths = []
    if _depth > max_depth:
        return paths

    if isinstance(data, dict):
        for key, value in data.items():
            child = f'{prefix}.{key}' if prefix else key
            if isinstance(value, (dict, list)):
                paths.extend(collect_paths(value, child, max_depth, _depth + 1))
            else:
                paths.append((child, type(value).__name__, value))
    elif isinstance(data, list):
        if data and isinstance(data[0], dict):
            paths.extend(
                collect_paths(data[0], f'{prefix}[*]', max_depth, _depth + 1))
        elif data:
            paths.append((f'{prefix}[*]', f'list[{type(data[0]).__name__}]',
                          data[:3]))
    return paths


OPERATORS = [
    ('lt', 'is less than'),
    ('lte', 'is less than or equal to'),
    ('gt', 'is greater than'),
    ('gte', 'is greater than or equal to'),
    ('eq', 'equals'),
    ('neq', 'does not equal'),
    ('contains', 'contains'),
    ('not_contains', 'does not contain'),
    ('is_true', 'is true'),
    ('is_false', 'is false'),
    ('is_set', 'has any value'),
    ('is_empty', 'is empty or missing'),
]

AGGREGATIONS = [
    ('none', 'Single value'),
    ('any', 'Any item matches'),
    ('all', 'All items match'),
    ('max', 'Maximum of list'),
    ('min', 'Minimum of list'),
    ('avg', 'Average of list'),
    ('count', 'Number of items'),
]


class AssetTelemetryAlertRule(models.Model):
    _name = 'asset.telemetry.alert.rule'
    _description = 'Telemetry Alert Rule'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Text()

    severity = fields.Selection(
        [('info', 'Info'), ('warning', 'Warning'), ('critical', 'Critical')],
        default='warning', required=True,
    )

    # ── What to look at ───────────────────────────────────────────────────
    json_path = fields.Char(
        string='JSON Path', required=True,
        help='Dotted path into the telemetry payload.\n\n'
             '  memory.virtual.percent\n'
             '  cpu.load_average.5min\n'
             '  disks.mounts[*].percent     (every mount)\n'
             '  disks.mounts[0].percent     (first mount only)\n'
             '  services.failed_count\n\n'
             'Use "Browse Available Paths" to see what a real payload '
             'contains.',
    )
    aggregation = fields.Selection(
        AGGREGATIONS, default='none', required=True,
        help='How to reduce a list to something comparable. Only relevant '
             'when the path uses [*].',
    )
    operator = fields.Selection(OPERATORS, default='gt', required=True)
    threshold = fields.Char(
        help='Compared against the resolved value. Numbers are parsed as '
             'numbers; anything else is compared as text. Leave blank for '
             'operators that need no threshold (is_true, is_set…).',
    )

    # ── Advanced ──────────────────────────────────────────────────────────
    use_expression = fields.Boolean(
        string='Use Python Expression',
        help='For conditions the simple form cannot express. When enabled, '
             'json_path and operator are ignored.',
    )
    expression = fields.Char(
        string='Expression',
        help='Python boolean expression. In scope:\n'
             '  telemetry  — the whole payload as a dict\n'
             '  asset      — the asset record\n'
             '  get(path)  — resolve a path, returns None if missing\n\n'
             "e.g. get('memory.virtual.percent') > 90 and "
             "get('cpu.load_average.5min') > 4",
    )

    # ── Which assets ──────────────────────────────────────────────────────
    asset_ids = fields.Many2many(
        'asset.asset', 'telemetry_rule_asset_rel', 'rule_id', 'asset_id',
        string='Limit to Assets',
        help='Leave empty to apply to every asset that reports telemetry.',
    )
    platform_filter = fields.Selection(
        [('all', 'All'), ('windows', 'Windows'), ('linux', 'Linux'),
         ('macos', 'macOS')],
        default='all', required=True,
    )

    # ── Notification ──────────────────────────────────────────────────────
    mail_to = fields.Char(
        string='Mail To', required=True,
        help='One address, or several separated by commas.',
    )
    email_subject = fields.Char(
        default='Asset Alert: {asset_name} — {rule_name}',
        help='Placeholders: {asset_name} {asset_serial} {rule_name} '
             '{value} {threshold} {severity}',
    )
    email_content = fields.Html(
        default='''<p>Alert triggered on <b>{asset_name}</b>.</p>
<table style="border-collapse:collapse;">
  <tr><td style="padding:4px 12px 4px 0;"><b>Rule</b></td><td>{rule_name}</td></tr>
  <tr><td style="padding:4px 12px 4px 0;"><b>Path</b></td><td>{json_path}</td></tr>
  <tr><td style="padding:4px 12px 4px 0;"><b>Value</b></td><td>{value}</td></tr>
  <tr><td style="padding:4px 12px 4px 0;"><b>Threshold</b></td><td>{threshold}</td></tr>
  <tr><td style="padding:4px 12px 4px 0;"><b>Severity</b></td><td>{severity}</td></tr>
  <tr><td style="padding:4px 12px 4px 0;"><b>Serial</b></td><td>{asset_serial}</td></tr>
</table>
<p><a href="{record_url}">Open the asset in Odoo</a></p>''',
        help='Same placeholders as the subject, plus {record_url} and '
             '{json_path}.',
    )

    # ── Noise control ─────────────────────────────────────────────────────
    cooldown_hours = fields.Integer(
        default=24, required=True,
        help='Minimum hours between mails for the same rule on the same '
             'asset.\n\n'
             'Telemetry arrives every 15 minutes. Without a cooldown a '
             'machine sitting above the threshold mails 96 times a day and '
             'people stop reading. Set 0 only if you genuinely want every '
             'occurrence.',
    )
    send_recovery_mail = fields.Boolean(
        default=True,
        help='Send a follow-up when the condition clears. Without it you '
             'cannot tell "still broken" from "broken again".',
    )

    # ── Stats ─────────────────────────────────────────────────────────────
    trigger_count = fields.Integer(readonly=True)
    last_triggered = fields.Datetime(readonly=True)
    log_ids = fields.One2many('asset.telemetry.alert.log', 'rule_id')
    active_alert_count = fields.Integer(compute='_compute_active_alerts')

    @api.depends('log_ids.state')
    def _compute_active_alerts(self):
        for rule in self:
            rule.active_alert_count = len(
                rule.log_ids.filtered(lambda l: l.state == 'active'))

    # ══════════════════════════════════════════════════════════════════════
    # Validation
    # ══════════════════════════════════════════════════════════════════════
    @api.constrains('cooldown_hours')
    def _check_cooldown(self):
        for rule in self:
            if rule.cooldown_hours < 0:
                raise ValidationError('Cooldown cannot be negative.')

    @api.constrains('use_expression', 'expression', 'json_path')
    def _check_condition_defined(self):
        for rule in self:
            if rule.use_expression and not rule.expression:
                raise ValidationError(
                    f'Rule "{rule.name}" uses an expression but none is set.')
            if not rule.use_expression and not rule.json_path:
                raise ValidationError(
                    f'Rule "{rule.name}" needs a JSON path.')

    # ══════════════════════════════════════════════════════════════════════
    # Evaluation
    # ══════════════════════════════════════════════════════════════════════
    def _parse_threshold(self):
        """Threshold as a number when it looks numeric, else as text."""
        self.ensure_one()
        raw = (self.threshold or '').strip()
        if not raw:
            return None
        lowered = raw.lower()
        if lowered in ('true', 'yes'):
            return True
        if lowered in ('false', 'no'):
            return False
        try:
            return float(raw) if '.' in raw else int(raw)
        except ValueError:
            return raw

    def _aggregate(self, value):
        """Reduce a list per the aggregation setting."""
        self.ensure_one()
        if self.aggregation == 'none' or not isinstance(value, list):
            return value

        numeric = [v for v in value if isinstance(v, (int, float))
                   and not isinstance(v, bool)]

        if self.aggregation == 'count':
            return len(value)
        if self.aggregation == 'max':
            return max(numeric) if numeric else None
        if self.aggregation == 'min':
            return min(numeric) if numeric else None
        if self.aggregation == 'avg':
            return round(sum(numeric) / len(numeric), 2) if numeric else None
        # any / all are handled by the comparison step
        return value

    def _compare(self, value, threshold):
        """Apply the operator. Returns None when it cannot be evaluated."""
        self.ensure_one()
        op = self.operator

        if op == 'is_set':
            return value is not None and value != ''
        if op == 'is_empty':
            return value is None or value == '' or value == []
        if op == 'is_true':
            return bool(value)
        if op == 'is_false':
            return not bool(value)

        if value is None:
            return None

        if op in ('contains', 'not_contains'):
            haystack = str(value).lower()
            needle = str(threshold).lower()
            found = needle in haystack
            return found if op == 'contains' else not found

        if op in ('eq', 'neq'):
            equal = value == threshold
            return equal if op == 'eq' else not equal

        # Numeric comparisons
        try:
            left = float(value)
            right = float(threshold)
        except (TypeError, ValueError):
            return None

        return {
            'lt': left < right,
            'lte': left <= right,
            'gt': left > right,
            'gte': left >= right,
        }.get(op)

    def evaluate(self, asset, telemetry):
        """Evaluate this rule. Returns (triggered, observed_value, note)."""
        self.ensure_one()

        # ── Scope ─────────────────────────────────────────────────────────
        if self.asset_ids and asset.id not in self.asset_ids.ids:
            return False, None, 'asset not in scope'
        if self.platform_filter != 'all' and asset.platform != self.platform_filter:
            return False, None, 'platform not in scope'

        # ── Expression mode ───────────────────────────────────────────────
        if self.use_expression:
            def _get(path, default=None):
                found, val = resolve_path(telemetry, path)
                return val if found else default

            context = {
                'telemetry': telemetry,
                'asset': asset,
                'get': _get,
            }
            try:
                return bool(safe_eval(self.expression, context)), None, ''
            except Exception as exc:
                _logger.warning('[TelemetryAlert] Rule "%s" expression failed: %s',
                                self.name, exc)
                return False, None, f'expression error: {exc}'

        # ── Simple mode ───────────────────────────────────────────────────
        found, raw_value = resolve_path(telemetry, self.json_path)
        if not found:
            return False, None, f'path not present: {self.json_path}'

        threshold = self._parse_threshold()

        # any / all over a list
        if isinstance(raw_value, list) and self.aggregation in ('any', 'all'):
            results = [self._compare(item, threshold) for item in raw_value]
            usable = [r for r in results if r is not None]
            if not usable:
                return False, raw_value, 'no comparable items'
            matched = any(usable) if self.aggregation == 'any' else all(usable)
            if matched:
                offenders = [v for v, r in zip(raw_value, results) if r]
                return True, offenders, ''
            return False, raw_value, ''

        value = self._aggregate(raw_value)
        result = self._compare(value, threshold)
        if result is None:
            return False, value, 'could not compare'
        return bool(result), value, ''

    # ══════════════════════════════════════════════════════════════════════
    # Path browser
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def get_available_paths(self, asset_id=None):
        """Enumerate paths from a real payload, so rules can be written
        against something concrete rather than guessed."""
        Asset = self.env['asset.asset'].sudo()
        if asset_id:
            asset = Asset.browse(asset_id)
        else:
            asset = Asset.search([('agent_telemetry', '!=', False)],
                                 order='agent_telemetry_at desc', limit=1)
        if not asset or not asset.agent_telemetry:
            return {'ok': False, 'paths': [],
                    'message': 'No asset has reported telemetry yet.'}

        paths = collect_paths(asset.agent_telemetry)
        return {
            'ok': True,
            'asset': asset.display_name,
            'count': len(paths),
            'paths': [{'path': p, 'type': t, 'sample': str(v)[:60]}
                      for p, t, v in sorted(paths)],
        }

    def action_browse_paths(self):
        """Show available paths in a dialog."""
        res = self.get_available_paths()
        if not res['ok']:
            raise UserError(res['message'])

        lines = [f"Paths from: {res['asset']}", '']
        for item in res['paths'][:120]:
            lines.append(f"  {item['path']}")
            lines.append(f"      {item['type']} = {item['sample']}")
        if res['count'] > 120:
            lines.append(f'  … and {res["count"] - 120} more')

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': f"{res['count']} available paths",
                'message': '\n'.join(lines),
                'type': 'info',
                'sticky': True,
            },
        }

    def action_test_rule(self):
        """Evaluate this rule against every asset that has telemetry,
        without sending mail."""
        self.ensure_one()
        assets = self.env['asset.asset'].sudo().search(
            [('agent_telemetry', '!=', False)])
        if not assets:
            raise UserError('No asset has reported telemetry yet.')

        would_fire, checked, notes = [], 0, []
        for asset in assets:
            triggered, value, note = self.evaluate(asset, asset.agent_telemetry)
            checked += 1
            if triggered:
                would_fire.append(f'  {asset.display_name}: {value}')
            elif note and note not in notes:
                notes.append(note)

        msg = [f'Checked {checked} asset(s).', '']
        if would_fire:
            msg.append(f'{len(would_fire)} would trigger:')
            msg.extend(would_fire[:20])
        else:
            msg.append('None would trigger.')
        if notes:
            msg.append('')
            msg.append('Notes: ' + '; '.join(notes[:4]))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': f'Test — {self.name}',
                'message': '\n'.join(msg),
                'type': 'warning' if would_fire else 'success',
                'sticky': True,
            },
        }

    def action_view_logs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Alerts — {self.name}',
            'res_model': 'asset.telemetry.alert.log',
            'view_mode': 'list,form',
            'domain': [('rule_id', '=', self.id)],
        }
