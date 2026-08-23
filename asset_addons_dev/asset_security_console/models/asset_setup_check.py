# -*- coding: utf-8 -*-
"""
Setup & Health Check.

The problem this solves: three modules are now stacked on top of each other,
each with its own crons, endpoints and prerequisites. When something shows
zero it is not obvious whether that means "healthy", "not configured yet", or
"broken".

This runs every check in order and reports, in plain language, what is working
and what is not — with the exact next action for each failure.
"""

import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

OK = 'ok'
WARN = 'warn'
FAIL = 'fail'


class AssetSecuritySetupCheck(models.TransientModel):
    _name = 'asset.security.setup.check'
    _description = 'Security Setup & Health Check'

    report_html = fields.Html(readonly=True)
    overall_status = fields.Selection(
        [('ready', 'Ready'), ('partial', 'Partially Configured'),
         ('not_ready', 'Not Configured')],
        readonly=True,
    )

    # ══════════════════════════════════════════════════════════════════════
    # Individual checks
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def _check_agents_reporting(self):
        """Are agents sending OS patch data at all?"""
        found = {}
        for platform, model_name in (('Windows', 'asset.windows.update'),
                                     ('Linux', 'asset.linux.update'),
                                     ('macOS', 'asset.macos.update')):
            if model_name in self.env:
                found[platform] = self.env[model_name].sudo().search_count([])

        total = sum(found.values())
        breakdown = ', '.join(f'{k}: {v}' for k, v in found.items())

        if total == 0:
            return {
                'name': 'Agents reporting OS patches',
                'status': FAIL,
                'detail': 'No patch records at all.',
                'action': 'Agents are not posting update data. Check that at '
                          'least one agent is running and can reach '
                          '/api/asset/updates/report.',
            }
        return {
            'name': 'Agents reporting OS patches',
            'status': OK,
            'detail': f'{total:,} record(s) — {breakdown}',
            'action': '',
        }

    @api.model
    def _check_platform_misfiling(self):
        """Detect the known bug: Linux packages stored in the Windows table."""
        if 'asset.windows.update' not in self.env:
            return None
        WU = self.env['asset.windows.update'].sudo()
        misfiled = WU.search_count([('asset_id.platform', '!=', 'windows'),
                                    ('asset_id.platform', '!=', False)])
        if misfiled:
            return {
                'name': 'Patch records filed under correct platform',
                'status': FAIL,
                'detail': f'{misfiled:,} non-Windows asset record(s) stored in '
                          'the Windows update table.',
                'action': 'Linux/macOS agents are posting to '
                          '/api/asset/updates/report, which always writes to '
                          'the Windows model. Repoint them at '
                          '/api/asset/os_updates/report, then delete the bad '
                          'rows (see GUIDE.md).',
            }
        return {
            'name': 'Patch records filed under correct platform',
            'status': OK,
            'detail': 'No cross-platform misfiling detected.',
            'action': '',
        }

    @api.model
    def _check_app_updates(self):
        if 'asset.app.update' not in self.env:
            return {
                'name': 'Application update tracking',
                'status': FAIL,
                'detail': 'Model not installed.',
                'action': 'Install the asset_update_center module.',
            }
        count = self.env['asset.app.update'].sudo().search_count([])
        if count == 0:
            return {
                'name': 'Application update tracking',
                'status': WARN,
                'detail': 'No application updates reported yet.',
                'action': 'Expected until the agent snippet is deployed. Add '
                          'agent_snippets/app_updates.py to the agent and call '
                          'check_and_report_app_updates() once per sync.',
            }
        return {
            'name': 'Application update tracking',
            'status': OK,
            'detail': f'{count:,} application update record(s).',
            'action': '',
        }

    @api.model
    def _check_cve_database(self):
        if 'asset.cve' not in self.env:
            return {
                'name': 'CVE database',
                'status': FAIL,
                'detail': 'Model not installed.',
                'action': 'Install the asset_patch_vuln module.',
            }
        count = self.env['asset.cve'].sudo().search_count([])
        if count == 0:
            return {
                'name': 'CVE database',
                'status': FAIL,
                'detail': 'Empty — 0 CVE records.',
                'action': 'Vulnerability scanning cannot work without this. '
                          'Run Security → Vulnerability Management → CVE '
                          'Database → Test NVD Connectivity, then sync. If the '
                          'server has no internet, use Offline CVE Import.',
            }
        if count < 500:
            return {
                'name': 'CVE database',
                'status': WARN,
                'detail': f'Only {count:,} CVE record(s) — thin coverage.',
                'action': 'Run a few more syncs with different date windows to '
                          'build up the database.',
            }
        return {
            'name': 'CVE database',
            'status': OK,
            'detail': f'{count:,} CVE record(s).',
            'action': '',
        }

    @api.model
    def _check_nvd_key(self):
        key = self.env['ir.config_parameter'].sudo().get_param(
            'asset_vulnerability.nvd_api_key', '')
        if not key:
            return {
                'name': 'NVD API key',
                'status': WARN,
                'detail': 'Not set — limited to 5 requests per 30 seconds.',
                'action': 'Optional but recommended. Free key from '
                          'nvd.nist.gov/developers/request-an-api-key, stored '
                          'in Settings → Technical → System Parameters as '
                          'asset_vulnerability.nvd_api_key',
            }
        return {
            'name': 'NVD API key',
            'status': OK,
            'detail': f'Configured ({len(key)} characters).',
            'action': '',
        }

    @api.model
    def _check_scans_run(self):
        if 'asset.vulnerability.scan' not in self.env:
            return None
        Scan = self.env['asset.vulnerability.scan'].sudo()
        completed = Scan.search_count([('state', '=', 'completed')])
        if completed == 0:
            return {
                'name': 'Vulnerability scans',
                'status': WARN,
                'detail': 'No completed scans.',
                'action': 'Once the CVE database is populated, run one scan '
                          'from the Vulnerability dashboard or the Asset '
                          'Security Console.',
            }
        last = Scan.search([('state', '=', 'completed')],
                           order='scan_date desc', limit=1)
        return {
            'name': 'Vulnerability scans',
            'status': OK,
            'detail': f'{completed} completed. Last: '
                      f'{last.scan_date.strftime("%d %b %Y %H:%M")}',
            'action': '',
        }

    @api.model
    def _check_crons(self):
        """Report which scheduled actions are enabled."""
        specs = [
            ('asset_patch_vuln.cron_patch_compliance_snapshot',
             'Daily compliance snapshot', True),
            ('asset_patch_vuln.cron_cve_sync', 'CVE sync from NVD', False),
            ('asset_patch_vuln.cron_vulnerability_scan',
             'Nightly vulnerability scan', False),
            ('asset_patch_management.ir_cron_run_update_policies',
             'Auto-patch policy runner', False),
        ]
        rows = []
        for xmlid, label, should_be_on in specs:
            cron = self.env.ref(xmlid, raise_if_not_found=False)
            if not cron:
                rows.append((label, 'not installed', WARN))
            elif cron.active:
                rows.append((label, 'enabled', OK))
            else:
                rows.append((label, 'disabled',
                             WARN if should_be_on else OK))

        enabled = sum(1 for _, s, _ in rows if s == 'enabled')
        detail = '; '.join(f'{lbl}: {state}' for lbl, state, _ in rows)
        return {
            'name': 'Scheduled actions',
            'status': OK if enabled else WARN,
            'detail': detail,
            'action': ('Several crons ship disabled on purpose. Enable them '
                       'only after the corresponding manual step works. '
                       'Settings → Technical → Scheduled Actions.'),
        }

    @api.model
    def _check_policies(self):
        if 'asset.update.policy' not in self.env:
            return {
                'name': 'Auto-patch policies',
                'status': WARN,
                'detail': 'Module not installed.',
                'action': 'Optional. Install asset_patch_management for '
                          'automatic patch approval.',
            }
        Policy = self.env['asset.update.policy'].sudo()
        active = Policy.search_count([('active', '=', True)])
        total = Policy.with_context(active_test=False).search_count([])
        if total == 0:
            return {
                'name': 'Auto-patch policies',
                'status': WARN,
                'detail': 'None configured.',
                'action': 'Optional — patches can be approved manually. '
                          'Security → Patch Management → Policies to automate.',
            }
        return {
            'name': 'Auto-patch policies',
            'status': OK if active else WARN,
            'detail': f'{total} policy(ies), {active} active.',
            'action': '' if active else 'All policies are inactive — nothing '
                                        'will be auto-approved.',
        }

    @api.model
    def _check_assets(self):
        Asset = self.env['asset.asset'].sudo()
        total = Asset.search_count([])
        managed = Asset.search_count(
            [('platform', 'in', ('windows', 'linux', 'macos'))])
        if managed == 0:
            return {
                'name': 'Managed assets',
                'status': FAIL,
                'detail': f'{total} asset(s), none with a recognised platform.',
                'action': 'Assets need platform set to windows/linux/macos. '
                          'This is normally set by the agent on first report.',
            }
        return {
            'name': 'Managed assets',
            'status': OK,
            'detail': f'{managed} managed asset(s) out of {total} total.',
            'action': '',
        }

    # ══════════════════════════════════════════════════════════════════════
    # Runner
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def run_all_checks(self):
        checks = [
            self._check_assets(),
            self._check_agents_reporting(),
            self._check_platform_misfiling(),
            self._check_app_updates(),
            self._check_cve_database(),
            self._check_nvd_key(),
            self._check_scans_run(),
            self._check_policies(),
            self._check_crons(),
        ]
        return [c for c in checks if c]

    def action_run_check(self):
        self.ensure_one()
        results = self.run_all_checks()

        fails = sum(1 for r in results if r['status'] == FAIL)
        warns = sum(1 for r in results if r['status'] == WARN)

        if fails:
            overall = 'not_ready'
        elif warns:
            overall = 'partial'
        else:
            overall = 'ready'

        icons = {OK: '&#10004;', WARN: '&#9888;', FAIL: '&#10008;'}
        colors = {OK: '#16a34a', WARN: '#ca8a04', FAIL: '#dc2626'}

        rows = []
        for r in results:
            action_html = (
                f'<div style="font-size:12px;color:#6b7280;margin-top:4px;">'
                f'<b>Next step:</b> {r["action"]}</div>'
            ) if r['action'] else ''
            rows.append(f'''
                <tr>
                  <td style="padding:10px 8px;vertical-align:top;
                             font-size:18px;color:{colors[r['status']]};">
                    {icons[r['status']]}
                  </td>
                  <td style="padding:10px 8px;">
                    <div style="font-weight:600;color:#111827;">{r['name']}</div>
                    <div style="font-size:12.5px;color:#374151;margin-top:2px;">
                      {r['detail']}
                    </div>
                    {action_html}
                  </td>
                </tr>
            ''')

        summary_map = {
            'ready': ('#16a34a', 'Ready',
                      'All checks passed. Patch and vulnerability management '
                      'are configured and working.'),
            'partial': ('#ca8a04', 'Partially Configured',
                        f'{warns} warning(s). The system works but some '
                        'optional pieces are not set up yet.'),
            'not_ready': ('#dc2626', 'Not Configured',
                          f'{fails} blocking issue(s) and {warns} warning(s). '
                          'Work through the failures below in order.'),
        }
        color, label, blurb = summary_map[overall]

        self.report_html = f'''
        <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;">
          <div style="background:{color};color:#fff;padding:14px 18px;
                      border-radius:8px;margin-bottom:16px;">
            <div style="font-size:18px;font-weight:700;">{label}</div>
            <div style="font-size:13px;margin-top:4px;opacity:.95;">{blurb}</div>
          </div>
          <table style="width:100%;border-collapse:collapse;">
            {''.join(rows)}
          </table>
        </div>
        '''
        self.overall_status = overall

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'asset.security.setup.check',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
