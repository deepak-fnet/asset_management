# -*- coding: utf-8 -*-
"""
Per-asset vulnerability report — the scan-report layout, per machine.

Honest framing
--------------
The report you supplied as a reference is an OpenVAS output. OpenVAS is a
network vulnerability scanner: it probes ports, fingerprints services, and
tests for exploitable conditions from outside the machine.

This is not that, and pretending otherwise would be misleading. What this
produces is an *agent-based* report:

  * Software correlated against the CVE cache      (we have this)
  * Listening ports as reported by the agent       (added here)
  * Remediation hints from CVE text                (we have this)

What it cannot tell you, and OpenVAS can:

  * Whether a listening service is actually exploitable from the network
  * Weak or default credentials on a service
  * TLS/cipher misconfiguration
  * Anything about a device with no agent installed

The agent approach has a compensating advantage: it sees every installed
application, including ones that expose no network service at all — which a
network scanner is blind to. The two techniques are complementary. If you need
true network scanning later, the right move is integrating OpenVAS rather
than reimplementing it.
"""

import logging
from datetime import timedelta

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

ACTIVE_STATES = ('open', 'needs_review')

# Ports worth calling out in the report, with why they matter.
NOTABLE_PORTS = {
    21: ('FTP', 'Plaintext credentials unless FTPS is enforced'),
    22: ('SSH', 'Remote shell — ensure key-only auth'),
    23: ('Telnet', 'Plaintext protocol, should be disabled'),
    25: ('SMTP', 'Mail relay — verify it is not open'),
    80: ('HTTP', 'Unencrypted web service'),
    135: ('MSRPC', 'Windows RPC endpoint mapper'),
    139: ('NetBIOS', 'Legacy SMB — disable if unused'),
    443: ('HTTPS', 'Encrypted web service'),
    445: ('SMB', 'File sharing — common ransomware vector'),
    1433: ('MSSQL', 'Database exposed on the network'),
    3306: ('MySQL', 'Database exposed on the network'),
    3389: ('RDP', 'Remote Desktop — high-value target, restrict access'),
    5432: ('PostgreSQL', 'Database exposed on the network'),
    5900: ('VNC', 'Remote control, often weakly authenticated'),
    8080: ('HTTP-alt', 'Unencrypted web service'),
}


class AssetListeningPort(models.Model):
    _name = 'asset.listening.port'
    _description = 'Listening Port on an Asset'
    _order = 'asset_id, port'
    _rec_name = 'port'

    asset_id = fields.Many2one(
        'asset.asset', required=True, ondelete='cascade', index=True,
    )
    port = fields.Integer(required=True, index=True)
    service_name = fields.Char()
    risk_note = fields.Char()
    last_seen = fields.Datetime(default=fields.Datetime.now)

    _sql_constraints = [
        ('asset_port_uniq', 'unique(asset_id, port)',
         'One row per port per asset.'),
    ]

    @api.model
    def record_ports(self, asset, ports):
        """Replace the recorded port list for an asset."""
        existing = self.search([('asset_id', '=', asset.id)])
        existing_map = {p.port: p for p in existing}
        now = fields.Datetime.now()
        seen = set()

        for port in ports:
            try:
                port = int(port)
            except (TypeError, ValueError):
                continue
            seen.add(port)
            name, note = NOTABLE_PORTS.get(port, ('', ''))
            if port in existing_map:
                existing_map[port].write({'last_seen': now,
                                          'service_name': name or False,
                                          'risk_note': note or False})
            else:
                self.create({
                    'asset_id': asset.id,
                    'port': port,
                    'service_name': name or False,
                    'risk_note': note or False,
                    'last_seen': now,
                })

        # Ports that stopped listening
        gone = existing.filtered(lambda p: p.port not in seen)
        if gone:
            gone.unlink()

        return len(seen)


class AssetAssetVulnReport(models.Model):
    _inherit = 'asset.asset'

    vulnerability_ids = fields.One2many(
        'asset.vulnerability', 'asset_id', string='Vulnerabilities',
    )
    listening_port_ids = fields.One2many(
        'asset.listening.port', 'asset_id', string='Listening Ports',
    )

    vuln_critical_count = fields.Integer(compute='_compute_vuln_counts')
    vuln_high_count = fields.Integer(compute='_compute_vuln_counts')
    vuln_medium_count = fields.Integer(compute='_compute_vuln_counts')
    vuln_low_count = fields.Integer(compute='_compute_vuln_counts')
    vuln_total_count = fields.Integer(compute='_compute_vuln_counts')
    vuln_review_count = fields.Integer(compute='_compute_vuln_counts')
    last_vuln_scan = fields.Datetime(compute='_compute_vuln_counts')

    @api.depends('vulnerability_ids', 'vulnerability_ids.state',
                 'vulnerability_ids.severity')
    def _compute_vuln_counts(self):
        for asset in self:
            active = asset.vulnerability_ids.filtered(
                lambda v: v.state in ACTIVE_STATES
            )
            asset.vuln_critical_count = len(
                active.filtered(lambda v: v.severity == 'critical'))
            asset.vuln_high_count = len(
                active.filtered(lambda v: v.severity == 'high'))
            asset.vuln_medium_count = len(
                active.filtered(lambda v: v.severity == 'medium'))
            asset.vuln_low_count = len(
                active.filtered(lambda v: v.severity == 'low'))
            asset.vuln_total_count = len(active)
            asset.vuln_review_count = len(
                asset.vulnerability_ids.filtered(
                    lambda v: v.state == 'needs_review'))

            scans = asset.vulnerability_ids.mapped('scan_id').filtered(
                lambda s: s.state == 'completed')
            asset.last_vuln_scan = max(
                scans.mapped('scan_date')) if scans else False

    # ══════════════════════════════════════════════════════════════════════
    # Actions
    # ══════════════════════════════════════════════════════════════════════
    def action_scan_this_asset(self):
        """Run a vulnerability correlation for this machine only."""
        self.ensure_one()
        cve_count = self.env['asset.cve'].sudo().search_count([])
        if not cve_count:
            from odoo.exceptions import UserError
            raise UserError(
                'The CVE database is empty, so a scan would find nothing.\n\n'
                'Go to Security → Vulnerability Management → CVE Database and '
                'either run Sync from NVD, or use Offline Import if this '
                'server has no internet access.'
            )

        scan = self.env['asset.vulnerability.engine'].run_scan([self.id])
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': f'Scan complete — {self.display_name}',
                'message': (
                    f'{scan.findings_new} new finding(s). '
                    f'{scan.critical_count} critical, {scan.high_count} high. '
                    f'Checked {scan.software_checked} application(s).'
                ),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_view_vulnerabilities(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Vulnerabilities — {self.display_name}',
            'res_model': 'asset.vulnerability',
            'view_mode': 'list,form',
            'domain': [('asset_id', '=', self.id)],
            'context': {'search_default_f_open': 1},
        }

    # ══════════════════════════════════════════════════════════════════════
    # Report data
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def get_asset_vuln_report(self, asset_id):
        """Everything needed to render the per-asset scan report."""
        asset = self.sudo().browse(asset_id)
        if not asset.exists():
            return {}

        Vuln = self.env['asset.vulnerability'].sudo()
        active = Vuln.search([
            ('asset_id', '=', asset_id),
            ('state', 'in', ACTIVE_STATES),
        ], order='cvss_score desc', limit=50)

        findings = [{
            'vuln_id': v.id,
            'cve_ref': v.cve_ref,
            'title': (v.cve_id.description or '')[:120],
            'component': f'{v.software_name} {v.software_version or ""}'.strip(),
            'cvss': v.cvss_score,
            'severity': v.severity,
            'state': v.state,
            'confidence': v.confidence,
            'remediation': v.cve_id.remediation_hint or '',
        } for v in active]

        ports = [{
            'port': p.port,
            'service': p.service_name or '—',
            'note': p.risk_note or '',
        } for p in asset.listening_port_ids.sorted('port')]

        Scan = self.env['asset.vulnerability.scan'].sudo()
        recent = Scan.search([('state', '=', 'completed')],
                             order='scan_date desc', limit=7)
        recent = recent.sorted(key=lambda s: s.scan_date)

        return {
            'asset': {
                'id': asset.id,
                'name': asset.display_name,
                'serial': asset.serial_number or '',
                'os': asset.os_name or '',
                'platform': asset.platform or '',
                'ip': getattr(asset, 'ip_address', '') or '',
            },
            'summary': {
                'critical': asset.vuln_critical_count,
                'high': asset.vuln_high_count,
                'medium': asset.vuln_medium_count,
                'low': asset.vuln_low_count,
                'total': asset.vuln_total_count,
                'needs_review': asset.vuln_review_count,
            },
            'last_scan': asset.last_vuln_scan.strftime('%d %b %Y, %I:%M %p')
                         if asset.last_vuln_scan else 'Never scanned',
            'findings': findings,
            'ports': ports,
            'trend': {
                'labels': [s.scan_date.strftime('%d %b') for s in recent],
                'totals': [s.findings_total for s in recent],
            },
            'scan_method': 'Agent-based software correlation (not a network scan)',
        }
