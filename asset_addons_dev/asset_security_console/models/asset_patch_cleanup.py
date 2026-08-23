# -*- coding: utf-8 -*-
"""
Cleanup wizard for patch records filed under the wrong platform.

The health check reports "1,478 non-Windows asset record(s) stored in the
Windows update table". That happens because /api/asset/updates/report always
writes to asset.windows.update, so Linux and macOS agents posting to it store
Ubuntu package names as Windows KB numbers.

This wizard shows exactly what would be removed before removing it, because
deleting 1,478 rows on a hunch is not something to do from a shell one-liner.
"""

import logging

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AssetPatchCleanupWizard(models.TransientModel):
    _name = 'asset.patch.cleanup.wizard'
    _description = 'Clean Up Misfiled Patch Records'

    preview_html = fields.Html(readonly=True)
    misfiled_count = fields.Integer(readonly=True)
    confirmed = fields.Boolean(
        string='I have repointed my Linux/macOS agents',
        help='Tick only after the agents post to /api/asset/os_updates/report. '
             'Otherwise they will simply recreate these rows on the next sync.',
    )
    result_message = fields.Text(readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        res.update(self._build_preview())
        return res

    @api.model
    def _build_preview(self):
        if 'asset.windows.update' not in self.env:
            return {'misfiled_count': 0,
                    'preview_html': '<p>Windows update model not installed.</p>'}

        WU = self.env['asset.windows.update'].sudo()
        misfiled = WU.search([
            ('asset_id.platform', '!=', 'windows'),
            ('asset_id.platform', '!=', False),
        ])

        if not misfiled:
            return {
                'misfiled_count': 0,
                'preview_html': (
                    '<div style="padding:16px;background:#dcfce7;'
                    'border-radius:8px;color:#15803d;">'
                    '<b>Nothing to clean up.</b> All patch records are filed '
                    'under the correct platform.</div>'
                ),
            }

        # Group by asset so the user can see which machines are affected.
        by_asset = {}
        for rec in misfiled:
            key = (rec.asset_id.id, rec.asset_id.display_name,
                   rec.asset_id.platform)
            by_asset.setdefault(key, []).append(rec)

        rows = []
        for (aid, name, plat), recs in sorted(
                by_asset.items(), key=lambda x: -len(x[1])):
            samples = ', '.join(r.kb_number for r in recs[:4])
            more = f' … and {len(recs) - 4} more' if len(recs) > 4 else ''
            rows.append(f'''
                <tr>
                  <td style="padding:8px;border-bottom:1px solid #f3f4f6;">
                    <b>{name}</b>
                    <div style="font-size:11px;color:#6b7280;">
                      platform: {plat}
                    </div>
                  </td>
                  <td style="padding:8px;border-bottom:1px solid #f3f4f6;
                             text-align:right;font-weight:600;">{len(recs)}</td>
                  <td style="padding:8px;border-bottom:1px solid #f3f4f6;
                             font-size:11.5px;color:#374151;">
                    {samples}{more}
                  </td>
                </tr>
            ''')

        html = f'''
        <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
          <div style="background:#fef2f2;border-left:4px solid #dc2626;
                      padding:14px 16px;border-radius:6px;margin-bottom:14px;">
            <b style="color:#b91c1c;">{len(misfiled):,} record(s) filed under
            the wrong platform</b>
            <div style="font-size:12.5px;color:#374151;margin-top:6px;">
              These are stored in the <b>Windows</b> update table but belong to
              non-Windows machines. They are almost certainly Linux package
              names posted to the Windows-only endpoint.
            </div>
          </div>
          <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <thead>
              <tr style="background:#f9fafb;">
                <th style="padding:8px;text-align:left;">Asset</th>
                <th style="padding:8px;text-align:right;">Rows</th>
                <th style="padding:8px;text-align:left;">Sample identifiers</th>
              </tr>
            </thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </div>
        '''
        return {'misfiled_count': len(misfiled), 'preview_html': html}

    def action_refresh_preview(self):
        self.ensure_one()
        self.write(self._build_preview())
        return self._reopen()

    def action_cleanup(self):
        self.ensure_one()
        if not self.env.user.has_group('asset_management.group_asset_manager'):
            raise UserError('Only Asset Managers can clean up patch records.')
        if not self.confirmed:
            raise UserError(
                'Tick the confirmation box first.\n\n'
                'If the agents still post to /api/asset/updates/report they '
                'will recreate these rows within minutes, and you will have '
                'achieved nothing.'
            )

        WU = self.env['asset.windows.update'].sudo()
        misfiled = WU.search([
            ('asset_id.platform', '!=', 'windows'),
            ('asset_id.platform', '!=', False),
        ])
        affected_assets = misfiled.mapped('asset_id')
        count = len(misfiled)
        misfiled.unlink()

        if affected_assets:
            affected_assets.refresh_security_posture()

        self.result_message = (
            f'Deleted {count:,} misfiled patch record(s) across '
            f'{len(affected_assets)} asset(s).\n\n'
            'Linux and macOS agents will repopulate the correct tables on '
            'their next sync, provided they now post to '
            '/api/asset/os_updates/report.'
        )
        _logger.info('[Cleanup] Removed %s misfiled patch records.', count)
        self.write(self._build_preview())
        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'asset.patch.cleanup.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
