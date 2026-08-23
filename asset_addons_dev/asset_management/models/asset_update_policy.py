# -*- coding: utf-8 -*-
"""
Automatic Windows Update approval policies.

An admin defines ONE OR MORE policies. A cron runs hourly, and for each
active policy it finds `pending` updates that match the policy's criteria
and flips them to `installing`. The existing agent loop
(/api/asset/updates/instructions) then picks them up and installs them —
no change needed on the agent side.

Safety features, in order of importance:

  1. delay_days      — wait N days after Microsoft released it. This is the
                       single most valuable setting. Bad patches usually get
                       pulled within 3-7 days; a delay means the internet
                       finds out before your fleet does.
  2. maintenance_window — only approve inside a defined day/hour window, so
                       installs and reboots never land mid-workday.
  3. staged rollout  — pilot group goes first. Fleet only follows after the
                       pilot has been clean for N days.
  4. max_per_run     — hard cap on how many machines get approved per cron
                       tick, so a misconfigured policy can't hit 500 machines
                       at once.
"""

import logging
from datetime import timedelta

from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Weekday numbers match Python's datetime.weekday(): Monday = 0
WEEKDAYS = [
    ('0', 'Monday'),
    ('1', 'Tuesday'),
    ('2', 'Wednesday'),
    ('3', 'Thursday'),
    ('4', 'Friday'),
    ('5', 'Saturday'),
    ('6', 'Sunday'),
]


class AssetUpdatePolicy(models.Model):
    _name = 'asset.update.policy'
    _description = 'Automatic Windows Update Policy'
    _order = 'sequence, id'

    name = fields.Char(string='Policy Name', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Text()

    # ── Which severities to auto-approve ──────────────────────────────────
    # Separate booleans rather than a multi-select, because Odoo has no
    # native multi-select and comma-joined Char fields are painful to query.
    approve_security = fields.Boolean(
        string='Auto-approve Security', default=True,
        help='Security updates. Usually safe and urgent — most policies want this on.',
    )
    approve_critical = fields.Boolean(
        string='Auto-approve Critical', default=True,
    )
    approve_important = fields.Boolean(
        string='Auto-approve Important', default=False,
    )
    approve_optional = fields.Boolean(
        string='Auto-approve Optional', default=False,
        help='Optional updates include driver updates and preview builds. '
             'Leaving this OFF is strongly recommended.',
    )

    # ── Timing ────────────────────────────────────────────────────────────
    delay_days = fields.Integer(
        string='Delay (days)', default=7, required=True,
        help='Wait this many days after the update was first detected before '
             'auto-approving it. Gives Microsoft time to pull a bad patch. '
             'Set 0 for same-day (not recommended except for emergencies).',
    )
    use_maintenance_window = fields.Boolean(
        string='Restrict to Maintenance Window', default=True,
    )
    window_weekday = fields.Selection(
        WEEKDAYS, string='Day', default='6',
        help='Day of week the maintenance window opens (server timezone).',
    )
    window_start_hour = fields.Integer(string='Start Hour (0-23)', default=2)
    window_end_hour = fields.Integer(string='End Hour (0-23)', default=5)

    # ── Targeting ─────────────────────────────────────────────────────────
    target_all = fields.Boolean(
        string='Apply to All Windows Assets', default=True,
    )
    department_ids = fields.Many2many(
        'hr.department', 'asset_update_policy_dept_rel',
        'policy_id', 'dept_id', string='Departments',
        help='Leave empty to target every department.',
    )
    asset_type_filter = fields.Selection(
        [('all', 'All Types'), ('laptop', 'Laptops Only'),
         ('desktop', 'Desktops Only'), ('server', 'Servers Only')],
        string='Asset Type', default='all',
    )
    excluded_asset_ids = fields.Many2many(
        'asset.asset', 'asset_update_policy_excl_rel',
        'policy_id', 'asset_id', string='Excluded Assets',
        help='Machines that must never be auto-patched — e.g. production '
             'servers, or a machine running a fragile legacy application.',
    )

    # ── Staged rollout ────────────────────────────────────────────────────
    use_staged_rollout = fields.Boolean(
        string='Staged Rollout', default=False,
        help='Approve on the pilot group first. Only after the pilot has been '
             'clean for the configured number of days does the rest of the '
             'fleet get approved.',
    )
    pilot_asset_ids = fields.Many2many(
        'asset.asset', 'asset_update_policy_pilot_rel',
        'policy_id', 'asset_id', string='Pilot Assets',
    )
    pilot_soak_days = fields.Integer(
        string='Pilot Soak (days)', default=3,
        help='How long a KB must have been installed successfully on the '
             'pilot group before the rest of the fleet is approved.',
    )

    # ── Throttle ──────────────────────────────────────────────────────────
    max_per_run = fields.Integer(
        string='Max Approvals per Run', default=25,
        help='Hard cap per cron tick. Prevents a misconfigured policy from '
             'triggering installs across the whole fleet simultaneously.',
    )

    # ── Stats ─────────────────────────────────────────────────────────────
    last_run = fields.Datetime(string='Last Run', readonly=True)
    total_approved = fields.Integer(
        string='Total Auto-Approved', readonly=True, default=0,
    )

    # ══════════════════════════════════════════════════════════════════════
    # Constraints
    # ══════════════════════════════════════════════════════════════════════
    @api.constrains('window_start_hour', 'window_end_hour')
    def _check_window_hours(self):
        for rec in self:
            for h in (rec.window_start_hour, rec.window_end_hour):
                if h < 0 or h > 23:
                    raise ValidationError('Window hours must be between 0 and 23.')
            if rec.use_maintenance_window and \
                    rec.window_start_hour == rec.window_end_hour:
                raise ValidationError(
                    'Maintenance window start and end hour cannot be identical.'
                )

    @api.constrains('delay_days', 'max_per_run', 'pilot_soak_days')
    def _check_positive(self):
        for rec in self:
            if rec.delay_days < 0:
                raise ValidationError('Delay (days) cannot be negative.')
            if rec.max_per_run < 1:
                raise ValidationError('Max approvals per run must be at least 1.')
            if rec.pilot_soak_days < 0:
                raise ValidationError('Pilot soak days cannot be negative.')

    @api.constrains('use_staged_rollout', 'pilot_asset_ids')
    def _check_pilot(self):
        for rec in self:
            if rec.use_staged_rollout and not rec.pilot_asset_ids:
                raise ValidationError(
                    'Staged rollout is enabled but no pilot assets are selected.'
                )

    # ══════════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════════
    def _approved_severities(self):
        """Return the list of severity keys this policy auto-approves."""
        self.ensure_one()
        sev = []
        if self.approve_security:
            sev.append('security')
        if self.approve_critical:
            sev.append('critical')
        if self.approve_important:
            sev.append('important')
        if self.approve_optional:
            sev.append('optional')
        return sev

    def _in_maintenance_window(self, now=None):
        """True if `now` falls inside this policy's maintenance window."""
        self.ensure_one()
        if not self.use_maintenance_window:
            return True
        now = now or fields.Datetime.now()
        if str(now.weekday()) != self.window_weekday:
            return False
        start, end = self.window_start_hour, self.window_end_hour
        if start < end:
            return start <= now.hour < end
        # Window wraps past midnight, e.g. 22:00 → 03:00
        return now.hour >= start or now.hour < end

    def _target_asset_domain(self):
        """Domain selecting the assets this policy applies to."""
        self.ensure_one()
        domain = [('platform', '=', 'windows')]

        # Never touch a machine whose updates are explicitly locked.
        domain.append(('windows_update_locked', '=', False))

        if self.asset_type_filter and self.asset_type_filter != 'all':
            domain.append(('asset_type', '=', self.asset_type_filter))

        if self.department_ids:
            domain.append(('department_id', 'in', self.department_ids.ids))

        if self.excluded_asset_ids:
            domain.append(('id', 'not in', self.excluded_asset_ids.ids))

        return domain

    def _kb_passed_pilot(self, kb_number):
        """For staged rollout: has this KB been installed cleanly on the
        pilot group for at least `pilot_soak_days`?

        Returns False if any pilot machine reported `failed` for this KB —
        a failure anywhere in the pilot halts the wider rollout.
        """
        self.ensure_one()
        if not self.pilot_asset_ids:
            return False

        WU = self.env['asset.windows.update'].sudo()
        pilot_records = WU.search([
            ('asset_id', 'in', self.pilot_asset_ids.ids),
            ('kb_number', '=', kb_number),
        ])
        if not pilot_records:
            return False

        # Any failure in the pilot blocks the fleet.
        if any(r.status == 'failed' for r in pilot_records):
            _logger.info(
                '[UpdatePolicy] %s blocked for fleet — failed on pilot.', kb_number
            )
            return False

        installed = pilot_records.filtered(lambda r: r.status == 'installed')
        if not installed:
            return False

        # Soak period measured from the most recent pilot install.
        latest = max(r.action_date for r in installed if r.action_date) \
            if any(r.action_date for r in installed) else None
        if not latest:
            return False

        return fields.Datetime.now() >= latest + timedelta(days=self.pilot_soak_days)

    # ══════════════════════════════════════════════════════════════════════
    # Core engine
    # ══════════════════════════════════════════════════════════════════════
    def run_policy(self, dry_run=False):
        """Evaluate this policy and approve matching updates.

        Returns a dict summary. When `dry_run` is True nothing is written —
        used by the "Preview" button so an admin can see what WOULD happen.
        """
        self.ensure_one()
        now = fields.Datetime.now()
        result = {
            'policy': self.name,
            'approved': 0,
            'skipped_window': False,
            'candidates': [],
        }

        severities = self._approved_severities()
        if not severities:
            _logger.info('[UpdatePolicy] "%s" has no severities enabled.', self.name)
            return result

        if not self._in_maintenance_window(now):
            result['skipped_window'] = True
            return result

        assets = self.env['asset.asset'].sudo().search(self._target_asset_domain())
        if not assets:
            return result

        cutoff_date = fields.Date.today() - timedelta(days=self.delay_days)

        WU = self.env['asset.windows.update'].sudo()
        candidates = WU.search([
            ('asset_id', 'in', assets.ids),
            ('status', '=', 'pending'),
            ('severity', 'in', severities),
            ('detected_date', '<=', cutoff_date),
        ], order='severity, detected_date')

        approved_count = 0
        for upd in candidates:
            if approved_count >= self.max_per_run:
                break

            # Staged rollout gate — pilot machines go immediately, everyone
            # else waits for the pilot to prove the KB is safe.
            if self.use_staged_rollout:
                is_pilot = upd.asset_id.id in self.pilot_asset_ids.ids
                if not is_pilot and not self._kb_passed_pilot(upd.kb_number):
                    continue

            result['candidates'].append({
                'kb': upd.kb_number,
                'asset': upd.asset_id.asset_name or upd.asset_id.asset_code or '',
                'severity': upd.severity,
            })

            if not dry_run:
                # Write directly rather than calling action_push_update(),
                # which enforces an interactive manager permission check.
                upd.write({
                    'status': 'installing',
                    'action_date': now,
                    # action_by intentionally left empty → the UI shows
                    # "System", distinguishing automated from manual actions.
                })
                _logger.info(
                    '[UpdatePolicy] "%s" auto-approved %s on %s',
                    self.name, upd.kb_number,
                    upd.asset_id.asset_name or upd.asset_id.id,
                )
            approved_count += 1

        result['approved'] = approved_count

        if not dry_run:
            self.write({
                'last_run': now,
                'total_approved': self.total_approved + approved_count,
            })

        return result

    def action_preview(self):
        """Preview button — shows what this policy would approve right now,
        without changing anything."""
        self.ensure_one()
        res = self.run_policy(dry_run=True)

        if res['skipped_window']:
            msg = ('Outside the maintenance window right now — nothing would '
                   'be approved. Preview ignores only the write, not the '
                   'window check.')
        elif not res['candidates']:
            msg = 'No updates currently match this policy.'
        else:
            lines = [
                f"• {c['kb']} ({c['severity']}) → {c['asset']}"
                for c in res['candidates'][:20]
            ]
            more = len(res['candidates']) - 20
            if more > 0:
                lines.append(f'…and {more} more')
            msg = (f"{len(res['candidates'])} update(s) would be approved:\n\n"
                   + '\n'.join(lines))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': f'Preview — {self.name}',
                'message': msg,
                'sticky': True,
                'type': 'info',
            },
        }

    def action_run_now(self):
        """Manual trigger — bypasses the cron schedule but still respects
        the maintenance window and all other safety settings."""
        self.ensure_one()
        if not self.env.user.has_group('asset_management.group_asset_manager'):
            raise UserError('Only Asset Managers can run an update policy.')

        res = self.run_policy()
        if res['skipped_window']:
            msg = 'Skipped — currently outside the maintenance window.'
        else:
            msg = f"{res['approved']} update(s) approved and queued for install."

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': self.name,
                'message': msg,
                'sticky': False,
                'type': 'success' if res['approved'] else 'warning',
            },
        }

    # ══════════════════════════════════════════════════════════════════════
    # Cron entry point
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def cron_run_update_policies(self):
        """Called hourly. Runs every active policy in sequence order."""
        policies = self.search([('active', '=', True)], order='sequence, id')
        total = 0
        for policy in policies:
            try:
                res = policy.run_policy()
                total += res['approved']
            except Exception as exc:
                # One bad policy must not stop the others.
                _logger.error(
                    '[UpdatePolicy] Error running "%s": %s',
                    policy.name, exc, exc_info=True,
                )
        if total:
            _logger.info('[UpdatePolicy] Cron auto-approved %s update(s).', total)
        return True
