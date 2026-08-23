# -*- coding: utf-8 -*-
"""
CVE master data, synced from the NVD (National Vulnerability Database).

Source: https://services.nvd.nist.gov/rest/json/cves/2.0
Free, official, no key required. An API key raises the rate limit from
5 requests / 30s to 50 requests / 30s — set it in System Parameters as
`asset_vulnerability.nvd_api_key` if you have one.

Honest limitation
-----------------
NVD publishes affected-product data as CPE (Common Platform Enumeration)
strings with version ranges. Mapping "Google Chrome 141.0.7390.55" as reported
by an agent onto the right CPE is genuinely imprecise: vendors name products
inconsistently, and agent-reported names vary by installer.

This module therefore stores CPE data and does a CONSERVATIVE match. When it
cannot be confident it records the finding as `needs_review` instead of
`open`, so a human decides. Treating every match as fact would produce a
dashboard full of false positives that people quickly learn to ignore.
"""

import json
import logging
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

NVD_API = 'https://services.nvd.nist.gov/rest/json/cves/2.0'
NVD_PAGE_SIZE = 2000  # NVD max


def cvss_to_severity(score):
    """CVSS v3 qualitative rating."""
    if score is None:
        return 'unknown'
    if score >= 9.0:
        return 'critical'
    if score >= 7.0:
        return 'high'
    if score >= 4.0:
        return 'medium'
    if score > 0:
        return 'low'
    return 'none'


def normalize_product(name):
    """Reduce a product name to a comparable token.

    "Google Chrome"            -> "google chrome"
    "Mozilla Firefox (x64 en)" -> "mozilla firefox"
    """
    if not name:
        return ''
    s = name.lower()
    s = re.sub(r'\(.*?\)', ' ', s)                    # drop parentheticals
    s = re.sub(r'\b(x64|x86|64-bit|32-bit|win64|win32)\b', ' ', s)
    s = re.sub(r'\bversion\b', ' ', s)
    s = re.sub(r'[^a-z0-9\s.]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def parse_version(v):
    """Turn a version string into a comparable tuple of ints.

    Returns None when nothing numeric can be extracted, which callers must
    treat as "cannot compare" rather than "equal".
    """
    if not v:
        return None
    nums = re.findall(r'\d+', str(v))
    if not nums:
        return None
    return tuple(int(n) for n in nums[:5])


def version_in_range(ver, start_incl, start_excl, end_incl, end_excl):
    """True if `ver` falls inside the given NVD version bounds."""
    v = parse_version(ver)
    if v is None:
        return False

    def cmp_ok(bound, op):
        b = parse_version(bound)
        if b is None:
            return True  # unbounded on this side
        # Pad to equal length so tuple comparison is fair
        length = max(len(v), len(b))
        vv = v + (0,) * (length - len(v))
        bb = b + (0,) * (length - len(b))
        if op == 'ge':
            return vv >= bb
        if op == 'gt':
            return vv > bb
        if op == 'le':
            return vv <= bb
        if op == 'lt':
            return vv < bb
        return True

    if start_incl and not cmp_ok(start_incl, 'ge'):
        return False
    if start_excl and not cmp_ok(start_excl, 'gt'):
        return False
    if end_incl and not cmp_ok(end_incl, 'le'):
        return False
    if end_excl and not cmp_ok(end_excl, 'lt'):
        return False
    return True


class AssetCve(models.Model):
    _name = 'asset.cve'
    _description = 'CVE Record'
    _order = 'cvss_score desc, cve_id desc'
    _rec_name = 'cve_id'

    cve_id = fields.Char(required=True, index=True, string='CVE ID')
    description = fields.Text()
    cvss_score = fields.Float(string='CVSS Score', digits=(3, 1), index=True)
    cvss_vector = fields.Char()
    severity = fields.Selection(
        [('critical', 'Critical'), ('high', 'High'), ('medium', 'Medium'),
         ('low', 'Low'), ('none', 'None'), ('unknown', 'Unknown')],
        default='unknown', index=True,
    )
    published_date = fields.Date(index=True)
    modified_date = fields.Date()
    reference_url = fields.Char()

    # Remediation hint parsed out of the description where possible, e.g.
    # a Microsoft KB number. Best-effort only.
    remediation_hint = fields.Char(
        help='Suggested fix, e.g. a KB number extracted from the CVE text. '
             'Advisory only — always verify against the vendor advisory.',
    )

    cpe_ids = fields.One2many('asset.cve.cpe', 'cve_id_ref', string='Affected Products')
    vulnerability_ids = fields.One2many(
        'asset.vulnerability', 'cve_id', string='Findings',
    )
    affected_asset_count = fields.Integer(
        compute='_compute_affected_count', store=True,
    )

    _sql_constraints = [
        ('cve_id_uniq', 'unique(cve_id)', 'CVE ID must be unique.'),
    ]

    @api.depends('vulnerability_ids', 'vulnerability_ids.state')
    def _compute_affected_count(self):
        for rec in self:
            rec.affected_asset_count = len(
                rec.vulnerability_ids.filtered(
                    lambda v: v.state in ('open', 'needs_review')
                )
            )

    def name_get(self):
        return [(r.id, f'{r.cve_id} ({r.cvss_score or "?"})') for r in self]

    # ══════════════════════════════════════════════════════════════════════
    # NVD sync
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def _nvd_request(self, params, timeout=60):
        """Call the NVD API. Returns parsed JSON or raises."""
        api_key = self.env['ir.config_parameter'].sudo().get_param(
            'asset_vulnerability.nvd_api_key', ''
        )
        url = f'{NVD_API}?{urllib.parse.urlencode(params)}'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'OdooAssetManagement/1.0',
        })
        if api_key:
            req.add_header('apiKey', api_key)

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))

    @api.model
    def sync_from_nvd(self, days_back=7, max_pages=5):
        """Pull CVEs modified in the last `days_back` days.

        Deliberately incremental. A full NVD download is ~250k CVEs and would
        take hours; syncing the recent window daily keeps the local copy
        current at a fraction of the cost.
        """
        end = datetime.utcnow()
        start = end - timedelta(days=days_back)

        created = updated = 0
        start_index = 0

        for _page in range(max_pages):
            params = {
                'lastModStartDate': start.strftime('%Y-%m-%dT%H:%M:%S.000'),
                'lastModEndDate': end.strftime('%Y-%m-%dT%H:%M:%S.000'),
                'resultsPerPage': NVD_PAGE_SIZE,
                'startIndex': start_index,
            }
            try:
                data = self._nvd_request(params)
            except Exception as exc:
                _logger.error('[CVE] NVD request failed: %s', exc)
                raise UserError(
                    f'Could not reach the NVD API: {exc}\n\n'
                    'Check that the Odoo server has outbound HTTPS access to '
                    'services.nvd.nist.gov.'
                )

            vulns = data.get('vulnerabilities', [])
            if not vulns:
                break

            for item in vulns:
                c, u = self._upsert_cve(item.get('cve', {}))
                created += c
                updated += u

            total = data.get('totalResults', 0)
            start_index += len(vulns)
            if start_index >= total:
                break

        _logger.info('[CVE] NVD sync done — %s created, %s updated.', created, updated)
        return {'created': created, 'updated': updated}

    @api.model
    def _upsert_cve(self, cve_json):
        """Create or update one CVE from NVD JSON. Returns (created, updated)."""
        cve_id = cve_json.get('id')
        if not cve_id:
            return 0, 0

        # Description — prefer English
        description = ''
        for d in cve_json.get('descriptions', []):
            if d.get('lang') == 'en':
                description = d.get('value', '')
                break

        # CVSS — prefer v3.1, fall back to v3.0 then v2
        score = None
        vector = ''
        metrics = cve_json.get('metrics', {})
        for key in ('cvssMetricV31', 'cvssMetricV30', 'cvssMetricV2'):
            entries = metrics.get(key) or []
            if entries:
                cvss = entries[0].get('cvssData', {})
                score = cvss.get('baseScore')
                vector = cvss.get('vectorString', '')
                break

        # Remediation hint — look for a KB number in the text
        hint = ''
        kb_match = re.search(r'\bKB\d{6,7}\b', description or '', re.IGNORECASE)
        if kb_match:
            hint = f'Apply {kb_match.group(0).upper()} or later.'

        ref_url = ''
        refs = cve_json.get('references', [])
        if refs:
            ref_url = refs[0].get('url', '')

        def to_date(s):
            if not s:
                return False
            try:
                return datetime.strptime(s[:10], '%Y-%m-%d').date()
            except Exception:
                return False

        vals = {
            'cve_id': cve_id,
            'description': description,
            'cvss_score': score or 0.0,
            'cvss_vector': vector,
            'severity': cvss_to_severity(score),
            'published_date': to_date(cve_json.get('published')),
            'modified_date': to_date(cve_json.get('lastModified')),
            'reference_url': ref_url[:255] if ref_url else False,
            'remediation_hint': hint or False,
        }

        existing = self.search([('cve_id', '=', cve_id)], limit=1)
        if existing:
            existing.write(vals)
            rec = existing
            result = (0, 1)
        else:
            rec = self.create(vals)
            result = (1, 0)

        self.env['asset.cve.cpe']._sync_for_cve(rec, cve_json.get('configurations', []))
        return result

    @api.model
    def cron_sync_nvd(self):
        """Daily CVE refresh."""
        try:
            self.sync_from_nvd(days_back=3)
        except Exception as exc:
            _logger.error('[CVE] Scheduled sync failed: %s', exc)
        return True

    def action_manual_sync(self):
        res = self.sync_from_nvd(days_back=7)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'NVD Sync Complete',
                'message': f"{res['created']} new CVE(s), {res['updated']} updated.",
                'type': 'success',
                'sticky': False,
            },
        }


class AssetCveCpe(models.Model):
    """One affected-product rule belonging to a CVE."""
    _name = 'asset.cve.cpe'
    _description = 'CVE Affected Product (CPE)'
    _order = 'cve_id_ref, id'

    cve_id_ref = fields.Many2one(
        'asset.cve', required=True, ondelete='cascade', index=True,
    )
    cpe_uri = fields.Char(string='CPE URI')
    vendor = fields.Char(index=True)
    product = fields.Char(index=True)
    product_normalized = fields.Char(index=True)

    version_start_including = fields.Char()
    version_start_excluding = fields.Char()
    version_end_including = fields.Char()
    version_end_excluding = fields.Char()
    exact_version = fields.Char()

    @api.model
    def _sync_for_cve(self, cve_rec, configurations):
        """Replace this CVE's CPE rules from NVD configuration JSON."""
        self.search([('cve_id_ref', '=', cve_rec.id)]).unlink()

        rows = []
        for config in configurations or []:
            for node in config.get('nodes', []):
                for match in node.get('cpeMatch', []):
                    if not match.get('vulnerable'):
                        continue
                    uri = match.get('criteria', '')
                    # cpe:2.3:a:google:chrome:141.0.0.0:*:*:...
                    parts = uri.split(':')
                    vendor = parts[3] if len(parts) > 4 else ''
                    product = parts[4] if len(parts) > 5 else ''
                    exact = parts[5] if len(parts) > 6 else ''
                    if exact in ('*', '-'):
                        exact = ''

                    prod_label = product.replace('_', ' ')
                    rows.append({
                        'cve_id_ref': cve_rec.id,
                        'cpe_uri': uri[:255],
                        'vendor': vendor,
                        'product': product,
                        'product_normalized': normalize_product(
                            f'{vendor.replace("_", " ")} {prod_label}'
                        ),
                        'exact_version': exact or False,
                        'version_start_including': match.get('versionStartIncluding'),
                        'version_start_excluding': match.get('versionStartExcluding'),
                        'version_end_including': match.get('versionEndIncluding'),
                        'version_end_excluding': match.get('versionEndExcluding'),
                    })

        if rows:
            self.create(rows)
        return len(rows)

    def matches_version(self, version):
        """Does the given installed version fall inside this rule?"""
        self.ensure_one()
        if self.exact_version:
            return parse_version(self.exact_version) == parse_version(version)
        return version_in_range(
            version,
            self.version_start_including,
            self.version_start_excluding,
            self.version_end_including,
            self.version_end_excluding,
        )
