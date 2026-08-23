# -*- coding: utf-8 -*-
"""
Third-party application updates — "Chrome 141 installed, 142 available".

This is the missing piece. Your module already had:

  * asset.windows.update      — OS patches, identified by KB number
  * asset_management.app_deployment — manual install/uninstall of an app

but nothing that answers "which applications on this machine are out of
date?". That is what this model provides.

How the data gets here
----------------------
The agent runs `winget upgrade` (Windows), `apt list --upgradable` (Linux) or
`brew outdated` (macOS) and posts the result. Those tools already know what is
outdated — there is no need to build version tracking or scrape vendor
websites.

Terminology, since it causes confusion
--------------------------------------
  OS patch     = Windows Update / apt security update. Identified by KB number
                 or package name. Lives in asset.windows.update et al.
  App update   = Chrome, Firefox, Zoom, 7-Zip. Identified by app name.
                 Lives here.

Both are "patching" in the everyday sense. They are separate systems because
Microsoft and Google ship updates through completely different channels.
"""

import logging

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AssetAppUpdate(models.Model):
    _name = 'asset.app.update'
    _description = 'Third-Party Application Update'
    _order = 'asset_id, app_name'
    _rec_name = 'app_name'

    asset_id = fields.Many2one(
        'asset.asset', required=True, ondelete='cascade', index=True,
    )
    app_name = fields.Char(string='Application', required=True, index=True)
    package_id = fields.Char(
        string='Package ID',
        help='Vendor package identifier, e.g. Google.Chrome for winget. '
             'This is what the agent uses to perform the upgrade.',
    )
    source = fields.Selection(
        [('winget', 'winget'), ('choco', 'Chocolatey'), ('apt', 'apt'),
         ('brew', 'Homebrew'), ('other', 'Other')],
        default='winget',
    )

    installed_version = fields.Char(string='Installed')
    available_version = fields.Char(string='Available')

    status = fields.Selection(
        [('available', 'Update Available'),
         ('queued', 'Queued'),
         ('updating', 'Updating'),
         ('updated', 'Updated'),
         ('failed', 'Failed'),
         ('blocked', 'Blocked')],
        default='available', required=True, index=True,
    )

    detected_date = fields.Date(default=fields.Date.today, index=True)
    first_detected_date = fields.Date(
        readonly=True,
        help='Never refreshed on re-report, so it can be used to measure how '
             'long an app has been out of date.',
    )
    action_by = fields.Many2one('res.users', readonly=True)
    action_date = fields.Datetime(readonly=True)
    error_message = fields.Text(readonly=True)

    _sql_constraints = [
        ('asset_app_uniq', 'unique(asset_id, app_name)',
         'One update row per application per asset.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('first_detected_date'):
                vals['first_detected_date'] = (
                    vals.get('detected_date') or fields.Date.today()
                )
        return super().create(vals_list)

    # ── Guard ─────────────────────────────────────────────────────────────
    def _require_manager(self):
        if not self.env.user.has_group('asset_management.group_asset_manager'):
            raise UserError('Only Asset Managers can trigger application updates.')

    # ── Actions ───────────────────────────────────────────────────────────
    def action_queue_update(self):
        """Queue this app for upgrade. The agent picks it up on next poll."""
        self._require_manager()
        for rec in self:
            if rec.status not in ('available', 'failed', 'blocked'):
                continue
            rec.write({
                'status': 'queued',
                'action_by': self.env.uid,
                'action_date': fields.Datetime.now(),
                'error_message': False,
            })
            _logger.info('[AppUpdate] %s queued on %s by %s',
                         rec.app_name, rec.asset_id.display_name,
                         self.env.user.name)
        return True

    def action_block(self):
        self._require_manager()
        self.write({'status': 'blocked', 'action_by': self.env.uid,
                    'action_date': fields.Datetime.now()})
        return True

    def action_unblock(self):
        self._require_manager()
        self.write({'status': 'available'})
        return True

    @api.model
    def action_queue_all_for_asset(self, asset_id):
        """'Upgrade All' — queue every available app update on one machine."""
        self._require_manager()
        pending = self.search([
            ('asset_id', '=', asset_id),
            ('status', 'in', ('available', 'failed')),
        ])
        if not pending:
            raise UserError('Nothing to upgrade on this machine.')
        pending.action_queue_update()
        return len(pending)
