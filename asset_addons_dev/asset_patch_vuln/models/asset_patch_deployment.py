# -*- coding: utf-8 -*-
"""
Patch deployment — groups many patches x many machines into one named batch.

The existing per-asset models (asset.windows.update / asset.linux.update /
asset.macos.update) stay the source of truth for individual patch state. This
model sits on top so an admin can say "June Security Updates — Windows,
25 patches, 125 targets" and track it as a single unit.

Deploying does not bypass the existing pipeline. It writes status='installing'
on the underlying update records exactly as the manual buttons do, so the
agent picks them up through the same /api/asset/updates/instructions endpoint.
"""

import logging

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

PLATFORM_MODEL = {
    'windows': 'asset.windows.update',
    'linux': 'asset.linux.update',
    'macos': 'asset.macos.update',
}

# Which field carries the patch identifier on each platform model.
PLATFORM_KEY = {
    'windows': 'kb_number',
    'linux': 'package_name',
    'macos': 'package_name',
}


class AssetPatchDeployment(models.Model):
    _name = 'asset.patch.deployment'
    _description = 'Patch Deployment Batch'
    _order = 'create_date desc, id desc'
    _inherit = ['mail.thread']

    name = fields.Char(
        string='Deployment Name', required=True, tracking=True,
        help='e.g. "June Security Updates — Windows"',
    )
    platform = fields.Selection(
        [('windows', 'Windows'), ('linux', 'Linux'),
         ('macos', 'macOS'), ('third_party', 'Third Party')],
        required=True, default='windows', tracking=True,
    )
    state = fields.Selection(
        [('draft', 'Draft'),
         ('pending', 'Pending'),
         ('in_progress', 'In Progress'),
         ('successful', 'Successful'),
         ('partial', 'Partially Failed'),
         ('failed', 'Failed'),
         ('cancelled', 'Cancelled')],
        default='draft', required=True, tracking=True,
    )

    started_by = fields.Many2one('res.users', string='Started By', readonly=True)
    started_at = fields.Datetime(string='Started', readonly=True)
    completed_at = fields.Datetime(string='Completed', readonly=True)

    # Points at asset.update.policy from the auto-patching patch. Declared as
    # a plain Integer rather than Many2one so this module installs cleanly
    # whether or not that patch is present.
    source_policy_id = fields.Many2one(
        'asset.update.policy', string='Created by Policy', readonly=True,
        ondelete='set null',
        help='Set when this batch was created automatically by a policy '
             'rather than by a person. Requires the auto-patching patch.',
    )

    line_ids = fields.One2many(
        'asset.patch.deployment.line', 'deployment_id', string='Lines',
    )

    # ── Counters ──────────────────────────────────────────────────────────
    patch_count = fields.Integer(compute='_compute_counters', store=True)
    target_count = fields.Integer(compute='_compute_counters', store=True)
    line_count = fields.Integer(compute='_compute_counters', store=True)
    success_count = fields.Integer(compute='_compute_counters', store=True)
    failed_count = fields.Integer(compute='_compute_counters', store=True)
    pending_count = fields.Integer(compute='_compute_counters', store=True)
    progress_pct = fields.Float(compute='_compute_counters', store=True)

    notes = fields.Text()

    @api.depends('line_ids', 'line_ids.state')
    def _compute_counters(self):
        for dep in self:
            lines = dep.line_ids
            dep.line_count = len(lines)
            dep.patch_count = len(set(lines.mapped('patch_ref')))
            dep.target_count = len(set(lines.mapped('asset_id').ids))
            dep.success_count = len(lines.filtered(lambda l: l.state == 'success'))
            dep.failed_count = len(lines.filtered(lambda l: l.state == 'failed'))
            dep.pending_count = len(
                lines.filtered(lambda l: l.state in ('pending', 'installing'))
            )
            done = dep.success_count + dep.failed_count
            dep.progress_pct = (100.0 * done / dep.line_count) if dep.line_count else 0.0

    # ══════════════════════════════════════════════════════════════════════
    # Actions
    # ══════════════════════════════════════════════════════════════════════
    def action_deploy(self):
        """Push every line — writes status on the underlying update records."""
        self.ensure_one()
        if not self.env.user.has_group('asset_management.group_asset_manager'):
            raise UserError('Only Asset Managers can deploy patches.')
        if self.state not in ('draft', 'pending'):
            raise UserError(f'Cannot deploy a batch in state "{self.state}".')
        if not self.line_ids:
            raise UserError('This deployment has no lines.')

        pushed = 0
        for line in self.line_ids.filtered(lambda l: l.state == 'pending'):
            if line._push_to_agent():
                pushed += 1

        self.write({
            'state': 'in_progress',
            'started_by': self.env.uid,
            'started_at': fields.Datetime.now(),
        })
        self.message_post(body=f'Deployment started — {pushed} patch task(s) queued.')
        return True

    def action_cancel(self):
        self.ensure_one()
        self.line_ids.filtered(
            lambda l: l.state in ('pending', 'installing')
        ).write({'state': 'cancelled'})
        self.state = 'cancelled'
        return True

    def action_view_lines(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Lines — {self.name}',
            'res_model': 'asset.patch.deployment.line',
            'view_mode': 'list,form',
            'domain': [('deployment_id', '=', self.id)],
        }

    def _refresh_state(self):
        """Recompute batch state from its lines. Called when a line changes."""
        for dep in self:
            if dep.state in ('draft', 'cancelled'):
                continue
            if dep.pending_count:
                dep.state = 'in_progress'
            elif dep.failed_count and dep.success_count:
                dep.state = 'partial'
                dep.completed_at = fields.Datetime.now()
            elif dep.failed_count:
                dep.state = 'failed'
                dep.completed_at = fields.Datetime.now()
            elif dep.success_count:
                dep.state = 'successful'
                dep.completed_at = fields.Datetime.now()


class AssetPatchDeploymentLine(models.Model):
    _name = 'asset.patch.deployment.line'
    _description = 'Patch Deployment Line'
    _order = 'deployment_id, asset_id'
    _rec_name = 'patch_ref'

    deployment_id = fields.Many2one(
        'asset.patch.deployment', required=True, ondelete='cascade', index=True,
    )
    asset_id = fields.Many2one(
        'asset.asset', required=True, ondelete='cascade', index=True,
    )
    platform = fields.Selection(related='deployment_id.platform', store=True)

    patch_ref = fields.Char(
        string='Patch', required=True, index=True,
        help='KB number on Windows, package name on Linux, update name on macOS.',
    )
    patch_title = fields.Char()
    severity = fields.Selection(
        [('security', 'Security'), ('critical', 'Critical'),
         ('important', 'Important'), ('optional', 'Optional')],
        default='optional',
    )

    # Link back to the concrete per-platform update record, if it exists.
    update_record_id = fields.Integer(
        string='Update Record ID', readonly=True,
        help='Primary key of the underlying platform-specific update record.',
    )

    state = fields.Selection(
        [('pending', 'Pending'),
         ('installing', 'Installing'),
         ('success', 'Success'),
         ('failed', 'Failed'),
         ('cancelled', 'Cancelled')],
        default='pending', required=True, index=True,
    )
    error_message = fields.Text()
    completed_at = fields.Datetime(readonly=True)

    def _platform_model(self):
        self.ensure_one()
        return PLATFORM_MODEL.get(self.platform)

    def _push_to_agent(self):
        """Set the underlying update record to 'installing'."""
        self.ensure_one()
        model_name = self._platform_model()
        if not model_name:
            self.write({'state': 'failed',
                        'error_message': f'Unsupported platform: {self.platform}'})
            return False

        Model = self.env[model_name].sudo()
        key_field = PLATFORM_KEY[self.platform]

        rec = None
        if self.update_record_id:
            rec = Model.browse(self.update_record_id).exists()
        if not rec:
            rec = Model.search([
                ('asset_id', '=', self.asset_id.id),
                (key_field, '=', self.patch_ref),
            ], limit=1)

        if not rec:
            self.write({
                'state': 'failed',
                'error_message': 'No matching update record found on this asset.',
            })
            return False

        rec.write({'status': 'installing', 'action_date': fields.Datetime.now()})
        self.write({'state': 'installing', 'update_record_id': rec.id})
        return True

    def action_mark_result(self, success, error=None):
        """Called when the agent reports back."""
        self.ensure_one()
        self.write({
            'state': 'success' if success else 'failed',
            'error_message': error or False,
            'completed_at': fields.Datetime.now(),
        })
        self.deployment_id._refresh_state()
        return True
