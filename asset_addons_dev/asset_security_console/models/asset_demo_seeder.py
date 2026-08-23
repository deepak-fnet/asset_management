# -*- coding: utf-8 -*-
"""
Demo data seeder.

Purpose: let you click through every screen and confirm the workflow behaves
correctly BEFORE the agent snippets are deployed. Without this you are stuck
looking at empty dashboards with no way to tell "not configured" apart from
"broken".

Everything created here is tagged so it can be removed cleanly:

  * CVEs get ids starting CVE-DEMO-
  * App updates get source='other' and a DEMO marker in package_id
  * Patch rows get a DEMO- prefix on their identifier

Nothing touches real agent-reported data, and Remove Demo Data deletes only
records carrying those markers.
"""

import logging
import random
from datetime import timedelta

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DEMO_PREFIX = 'DEMO-'
DEMO_CVE_PREFIX = 'CVE-DEMO-'
DEMO_PACKAGE_MARK = 'demo.package'

DEMO_PATCHES = [
    ('DEMO-KB5066791', '2025-10 Cumulative Update for Windows 10 22H2', 'critical'),
    ('DEMO-KB5062553', 'Security Update for Windows Kernel', 'security'),
    ('DEMO-KB5061234', 'Security Update for Windows SMB Server', 'security'),
    ('DEMO-KB5058999', 'Cumulative Update for .NET Framework', 'important'),
    ('DEMO-KB5055102', 'Windows Malicious Software Removal Tool', 'optional'),
]

DEMO_APPS = [
    ('Google Chrome', 'Google.Chrome', '141.0.7390.55', '142.0.7444.20'),
    ('Mozilla Firefox', 'Mozilla.Firefox', '131.0', '132.0.1'),
    ('7-Zip', '7zip.7zip', '23.01', '24.09'),
    ('Zoom Workplace', 'Zoom.Zoom', '6.1.5', '6.2.11'),
    ('Notepad++', 'Notepad++.Notepad++', '8.6.2', '8.7.1'),
]

DEMO_CVES = [
    ('CVE-DEMO-0001', 9.8, 'critical',
     'Remote code execution in the SMB server allows an unauthenticated '
     'attacker to execute arbitrary code over the network.',
     'Apply KB5061234 or later.', 'Google Chrome', '141.0.7390.55'),
    ('CVE-DEMO-0002', 8.6, 'high',
     'Heap buffer overflow in the browser rendering engine permits code '
     'execution via a crafted page.',
     'Update to Chrome 142 or later.', 'Google Chrome', '141.0.7390.55'),
    ('CVE-DEMO-0003', 7.8, 'high',
     'Elevation of privilege in the Common Log File System driver.',
     'Apply KB5062553 or later.', 'Mozilla Firefox', '131.0'),
    ('CVE-DEMO-0004', 5.9, 'medium',
     'Information disclosure in the .NET runtime may expose memory contents.',
     'Apply KB5058999 or later.', '7-Zip', '23.01'),
    ('CVE-DEMO-0005', 3.1, 'low',
     'Path traversal in archive extraction allows writing outside the target '
     'directory.',
     'Update to 7-Zip 24.09 or later.', '7-Zip', '23.01'),
]


class AssetSecurityDemoSeeder(models.TransientModel):
    _name = 'asset.security.demo.seeder'
    _description = 'Seed Demo Security Data'

    asset_id = fields.Many2one(
        'asset.asset', string='Target Asset', required=True,
        domain="[('platform', 'in', ('windows', 'linux', 'macos'))]",
        help='Demo data is created against this one machine so it is easy to '
             'find and easy to remove.',
    )
    seed_patches = fields.Boolean(string='OS Patches', default=True)
    seed_apps = fields.Boolean(string='Application Updates', default=True)
    seed_cves = fields.Boolean(string='CVEs and Findings', default=True)

    result_message = fields.Text(readonly=True)
    existing_demo_count = fields.Integer(
        compute='_compute_existing', string='Existing Demo Records',
    )

    @api.depends('asset_id')
    def _compute_existing(self):
        for rec in self:
            rec.existing_demo_count = rec._count_demo_records()

    def _count_demo_records(self):
        total = 0
        if 'asset.windows.update' in self.env:
            total += self.env['asset.windows.update'].sudo().search_count(
                [('kb_number', 'like', DEMO_PREFIX + '%')])
        if 'asset.app.update' in self.env:
            total += self.env['asset.app.update'].sudo().search_count(
                [('package_id', 'like', '%' + DEMO_PACKAGE_MARK + '%')])
        if 'asset.cve' in self.env:
            total += self.env['asset.cve'].sudo().search_count(
                [('cve_id', 'like', DEMO_CVE_PREFIX + '%')])
        return total

    # ══════════════════════════════════════════════════════════════════════
    # Seed
    # ══════════════════════════════════════════════════════════════════════
    def action_seed(self):
        self.ensure_one()
        if not self.env.user.has_group('asset_management.group_asset_manager'):
            raise UserError('Only Asset Managers can seed demo data.')

        asset = self.asset_id
        created = {'patches': 0, 'apps': 0, 'cves': 0, 'findings': 0}
        today = fields.Date.today()

        # ── OS patches ────────────────────────────────────────────────────
        if self.seed_patches and 'asset.windows.update' in self.env:
            WU = self.env['asset.windows.update'].sudo()
            for idx, (kb, title, severity) in enumerate(DEMO_PATCHES):
                if WU.search_count([('asset_id', '=', asset.id),
                                    ('kb_number', '=', kb)]):
                    continue
                # Mix of states so every column on the console has data.
                status = 'pending' if idx < 3 else 'installed'
                vals = {
                    'asset_id': asset.id,
                    'kb_number': kb,
                    'title': title,
                    'severity': severity,
                    'status': status,
                    # Backdated so delay_days logic can be exercised
                    'detected_date': today - timedelta(days=10 + idx),
                    'size': f'{random.randint(50, 900)} MB',
                }
                if 'first_detected_date' in WU._fields:
                    vals['first_detected_date'] = today - timedelta(days=10 + idx)
                WU.create(vals)
                created['patches'] += 1

        # ── Application updates ───────────────────────────────────────────
        if self.seed_apps and 'asset.app.update' in self.env:
            AU = self.env['asset.app.update'].sudo()
            for name, pkg, installed, available in DEMO_APPS:
                if AU.search_count([('asset_id', '=', asset.id),
                                    ('app_name', '=', name)]):
                    continue
                AU.create({
                    'asset_id': asset.id,
                    'app_name': name,
                    # Marked so removal can find it
                    'package_id': f'{pkg}.{DEMO_PACKAGE_MARK}',
                    'source': 'other',
                    'installed_version': installed,
                    'available_version': available,
                    'status': 'available',
                    'detected_date': today,
                    'first_detected_date': today,
                })
                created['apps'] += 1

        # ── CVEs and findings ─────────────────────────────────────────────
        if self.seed_cves and 'asset.cve' in self.env:
            Cve = self.env['asset.cve'].sudo()
            Vuln = self.env['asset.vulnerability'].sudo()

            scan = self.env['asset.vulnerability.scan'].sudo().create({
                'asset_id': asset.id,
                'state': 'completed',
                'assets_scanned': 1,
                'software_checked': len(DEMO_APPS),
                'duration_seconds': 12.4,
            })

            counts = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
            for cve_id, score, severity, desc, fix, sw_name, sw_ver in DEMO_CVES:
                cve = Cve.search([('cve_id', '=', cve_id)], limit=1)
                if not cve:
                    cve = Cve.create({
                        'cve_id': cve_id,
                        'description': desc,
                        'cvss_score': score,
                        'severity': severity,
                        'published_date': today - timedelta(days=45),
                        'modified_date': today - timedelta(days=5),
                        'remediation_hint': fix,
                    })
                    created['cves'] += 1

                if Vuln.search_count([('asset_id', '=', asset.id),
                                      ('cve_id', '=', cve.id)]):
                    continue

                # Mix confidence so the review queue is exercised too.
                confidence = 'high' if score >= 7.0 else 'medium'
                Vuln.create({
                    'asset_id': asset.id,
                    'cve_id': cve.id,
                    'scan_id': scan.id,
                    'software_name': sw_name,
                    'software_version': sw_ver,
                    'confidence': confidence,
                    'match_reason': 'Demo data — not a real match',
                    'state': 'open' if confidence == 'high' else 'needs_review',
                })
                created['findings'] += 1
                counts[severity] = counts.get(severity, 0) + 1

            scan.write({
                'findings_new': created['findings'],
                'findings_total': created['findings'],
                'critical_count': counts.get('critical', 0),
                'high_count': counts.get('high', 0),
                'medium_count': counts.get('medium', 0),
                'low_count': counts.get('low', 0),
            })

        self.result_message = (
            f"Seeded on {asset.display_name}:\n"
            f"  • {created['patches']} OS patch record(s) "
            f"(3 pending, 2 installed)\n"
            f"  • {created['apps']} application update(s)\n"
            f"  • {created['cves']} demo CVE(s)\n"
            f"  • {created['findings']} vulnerability finding(s)\n\n"
            "Now open that asset and check the Update Center and "
            "Vulnerability Report tabs, then look at the Asset Security "
            "Console.\n\n"
            "Remember to click Remove Demo Data before going live."
        )
        _logger.info('[DemoSeeder] Seeded demo data on %s: %s',
                     asset.display_name, created)

        return self._reopen()

    # ══════════════════════════════════════════════════════════════════════
    # Remove
    # ══════════════════════════════════════════════════════════════════════
    def action_remove_demo(self):
        self.ensure_one()
        if not self.env.user.has_group('asset_management.group_asset_manager'):
            raise UserError('Only Asset Managers can remove demo data.')

        removed = {'patches': 0, 'apps': 0, 'findings': 0, 'cves': 0, 'scans': 0}

        if 'asset.vulnerability' in self.env:
            vulns = self.env['asset.vulnerability'].sudo().search(
                [('cve_id.cve_id', 'like', DEMO_CVE_PREFIX + '%')])
            removed['findings'] = len(vulns)
            vulns.unlink()

        if 'asset.cve' in self.env:
            cves = self.env['asset.cve'].sudo().search(
                [('cve_id', 'like', DEMO_CVE_PREFIX + '%')])
            removed['cves'] = len(cves)
            cves.unlink()

        if 'asset.app.update' in self.env:
            apps = self.env['asset.app.update'].sudo().search(
                [('package_id', 'like', '%' + DEMO_PACKAGE_MARK + '%')])
            removed['apps'] = len(apps)
            apps.unlink()

        if 'asset.windows.update' in self.env:
            patches = self.env['asset.windows.update'].sudo().search(
                [('kb_number', 'like', DEMO_PREFIX + '%')])
            removed['patches'] = len(patches)
            patches.unlink()

        # Scans left with no findings after the above
        if 'asset.vulnerability.scan' in self.env:
            Scan = self.env['asset.vulnerability.scan'].sudo()
            orphans = Scan.search([('vulnerability_ids', '=', False),
                                   ('software_checked', '=', len(DEMO_APPS))])
            removed['scans'] = len(orphans)
            orphans.unlink()

        self.result_message = (
            'Demo data removed:\n'
            f"  • {removed['patches']} patch record(s)\n"
            f"  • {removed['apps']} application update(s)\n"
            f"  • {removed['findings']} finding(s)\n"
            f"  • {removed['cves']} CVE(s)\n"
            f"  • {removed['scans']} scan record(s)\n\n"
            'Real agent-reported data was not touched.'
        )
        _logger.info('[DemoSeeder] Removed demo data: %s', removed)
        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'asset.security.demo.seeder',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
