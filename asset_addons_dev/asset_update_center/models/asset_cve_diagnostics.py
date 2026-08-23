# -*- coding: utf-8 -*-
"""
CVE sync — diagnostics, hardened networking, and an offline fallback.

Why this file exists
--------------------
The first sync attempt failed with a generic message, which tells you nothing
about the cause. Nine times out of ten it is one of:

  1. No outbound internet from the Odoo server (corporate firewall / proxy)
  2. NVD rate limiting (5 req/30s without a key) returning HTTP 403
  3. API key stored in the wrong place, so it is not being sent
  4. Date window wider than NVD's 120-day maximum

This replaces the sync with a version that reports the actual HTTP status and
response body, retries on rate limits, and offers an offline import path for
servers that genuinely cannot reach the internet.
"""

import json
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

NVD_API = 'https://services.nvd.nist.gov/rest/json/cves/2.0'
NVD_HOST = 'services.nvd.nist.gov'

# NVD hard limits
MAX_WINDOW_DAYS = 120
MAX_RESULTS_PER_PAGE = 2000


class AssetCveDiagnostics(models.Model):
    _inherit = 'asset.cve'

    # ══════════════════════════════════════════════════════════════════════
    # Connectivity test
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def test_nvd_connectivity(self):
        """Step-by-step reachability check. Returns a human-readable report
        rather than a bare exception, so the failure point is obvious."""
        steps = []

        # 1. DNS
        try:
            ip = socket.gethostbyname(NVD_HOST)
            steps.append({'step': 'DNS resolution', 'ok': True,
                          'detail': f'{NVD_HOST} → {ip}'})
        except Exception as exc:
            steps.append({'step': 'DNS resolution', 'ok': False,
                          'detail': f'Cannot resolve {NVD_HOST}: {exc}'})
            return {'ok': False, 'steps': steps,
                    'verdict': 'DNS failure. The server cannot resolve the NVD '
                               'hostname. Check /etc/resolv.conf and whether an '
                               'internal DNS server is blocking external lookups.'}

        # 2. TCP 443
        try:
            sock = socket.create_connection((NVD_HOST, 443), timeout=10)
            sock.close()
            steps.append({'step': 'TCP connect :443', 'ok': True,
                          'detail': 'Port 443 reachable'})
        except Exception as exc:
            steps.append({'step': 'TCP connect :443', 'ok': False,
                          'detail': str(exc)})
            return {'ok': False, 'steps': steps,
                    'verdict': 'DNS works but port 443 is blocked. This is a '
                               'firewall or egress-proxy rule. Ask your network '
                               'team to allow outbound HTTPS to '
                               f'{NVD_HOST}, or use the offline import below.'}

        # 3. Actual API call — smallest possible request
        api_key = self.env['ir.config_parameter'].sudo().get_param(
            'asset_vulnerability.nvd_api_key', '')
        steps.append({
            'step': 'API key configured',
            'ok': bool(api_key),
            'detail': (f'Key present ({len(api_key)} chars)' if api_key
                       else 'No key — limited to 5 requests / 30 seconds'),
        })

        try:
            url = f'{NVD_API}?resultsPerPage=1'
            req = urllib.request.Request(url, headers={
                'User-Agent': 'OdooAssetManagement/1.0',
            })
            if api_key:
                req.add_header('apiKey', api_key)
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode('utf-8'))
                total = body.get('totalResults', 0)
            steps.append({'step': 'NVD API call', 'ok': True,
                          'detail': f'HTTP 200 — NVD reports {total:,} total CVEs'})
            return {'ok': True, 'steps': steps,
                    'verdict': 'All checks passed. Sync should work.'}

        except urllib.error.HTTPError as exc:
            detail = f'HTTP {exc.code}'
            try:
                detail += f' — {exc.read().decode("utf-8")[:300]}'
            except Exception:
                pass
            steps.append({'step': 'NVD API call', 'ok': False, 'detail': detail})

            if exc.code == 403:
                verdict = ('HTTP 403 from NVD. Usually rate limiting. Request a '
                           'free API key at '
                           'https://nvd.nist.gov/developers/request-an-api-key '
                           'and store it in Settings → Technical → System '
                           'Parameters as asset_vulnerability.nvd_api_key')
            elif exc.code == 404:
                verdict = 'HTTP 404 — the API endpoint changed. Check NVD docs.'
            else:
                verdict = f'NVD returned HTTP {exc.code}. See detail above.'
            return {'ok': False, 'steps': steps, 'verdict': verdict}

        except Exception as exc:
            steps.append({'step': 'NVD API call', 'ok': False, 'detail': str(exc)})
            return {'ok': False, 'steps': steps,
                    'verdict': f'Request failed: {exc}. If your network uses an '
                               'HTTP proxy, Odoo needs http_proxy / https_proxy '
                               'environment variables set in its systemd unit.'}

    def action_test_connectivity(self):
        """Button — shows the diagnostic report in a dialog."""
        res = self.test_nvd_connectivity()
        lines = []
        for s in res['steps']:
            mark = '✓' if s['ok'] else '✗'
            lines.append(f"{mark} {s['step']}: {s['detail']}")
        lines.append('')
        lines.append(res['verdict'])

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'NVD Connectivity Test',
                'message': '\n'.join(lines),
                'type': 'success' if res['ok'] else 'danger',
                'sticky': True,
            },
        }

    # ══════════════════════════════════════════════════════════════════════
    # Hardened sync
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def _nvd_request(self, params, timeout=90, max_retries=3):
        """Override the base implementation with retry + real error reporting."""
        api_key = self.env['ir.config_parameter'].sudo().get_param(
            'asset_vulnerability.nvd_api_key', '')
        url = f'{NVD_API}?{urllib.parse.urlencode(params)}'

        last_error = None
        for attempt in range(max_retries):
            req = urllib.request.Request(url, headers={
                'User-Agent': 'OdooAssetManagement/1.0',
                'Accept': 'application/json',
            })
            if api_key:
                req.add_header('apiKey', api_key)

            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode('utf-8'))

            except urllib.error.HTTPError as exc:
                body = ''
                try:
                    body = exc.read().decode('utf-8')[:300]
                except Exception:
                    pass
                last_error = f'HTTP {exc.code}: {body}'

                # 403/503 from NVD is usually throttling — back off and retry.
                if exc.code in (403, 429, 503) and attempt < max_retries - 1:
                    wait = 8 * (attempt + 1)
                    _logger.warning(
                        '[CVE] NVD %s, backing off %ss (attempt %s/%s)',
                        exc.code, wait, attempt + 1, max_retries)
                    time.sleep(wait)
                    continue
                break

            except Exception as exc:
                last_error = str(exc)
                if attempt < max_retries - 1:
                    time.sleep(5)
                    continue
                break

        raise UserError(
            f'NVD request failed after {max_retries} attempt(s).\n\n'
            f'Last error: {last_error}\n\n'
            'Run Vulnerability → CVE Database → Test NVD Connectivity for a '
            'step-by-step diagnosis.'
        )

    @api.model
    def sync_from_nvd(self, days_back=7, max_pages=5):
        """Guarded wrapper — enforces NVD's 120-day window limit."""
        if days_back > MAX_WINDOW_DAYS:
            raise UserError(
                f'NVD allows a maximum {MAX_WINDOW_DAYS}-day window per query. '
                f'You asked for {days_back} days. Run several smaller syncs '
                'instead.'
            )
        return super().sync_from_nvd(days_back=days_back, max_pages=max_pages)

    # ══════════════════════════════════════════════════════════════════════
    # Offline import — for servers with no internet
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def import_from_json(self, json_text):
        """Import CVEs from a raw NVD API JSON response.

        Use when the Odoo server cannot reach NVD. On any machine that does
        have internet:

            curl -o cves.json \\
              "https://services.nvd.nist.gov/rest/json/cves/2.0?resultsPerPage=2000"

        then paste the file contents into the import wizard.
        """
        try:
            data = json.loads(json_text)
        except Exception as exc:
            raise UserError(f'That is not valid JSON: {exc}')

        vulns = data.get('vulnerabilities')
        if vulns is None:
            raise UserError(
                'JSON parsed, but no "vulnerabilities" key found.\n\n'
                'Expected a raw NVD API 2.0 response. Make sure you saved the '
                'complete response, not just a fragment.'
            )

        created = updated = 0
        for item in vulns:
            c, u = self._upsert_cve(item.get('cve', {}))
            created += c
            updated += u

        _logger.info('[CVE] Offline import — %s created, %s updated.',
                     created, updated)
        return {'created': created, 'updated': updated, 'total': len(vulns)}


class AssetCveImportWizard(models.TransientModel):
    _name = 'asset.cve.import.wizard'
    _description = 'Offline CVE Import'

    json_data = fields.Text(
        string='NVD JSON',
        required=True,
        help='Paste the complete JSON response from the NVD API.',
    )
    result_message = fields.Text(readonly=True)

    def action_import(self):
        self.ensure_one()
        res = self.env['asset.cve'].import_from_json(self.json_data)
        self.result_message = (
            f"Imported {res['total']} record(s): "
            f"{res['created']} new, {res['updated']} updated."
        )
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'asset.cve.import.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
