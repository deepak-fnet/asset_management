# -*- coding: utf-8 -*-
"""
PATCH — validate the NVD API key format before using it.

Why
---
An NVD API key is a UUID: 8-4-4-4-12 hex characters, 36 total.

If you send an INVALID apiKey header, NVD responds 403 Forbidden. Sending NO
key works fine — you are just rate-limited to 5 requests per 30 seconds.

So a wrong key is strictly worse than no key, and the resulting 403 looks
identical to a firewall block or rate limiting. That cost real debugging time.

This patch:
  1. Validates the key format when it is read
  2. Ignores a malformed key rather than sending it and getting a 403
  3. Says so plainly in the log and in the connectivity test

Install
-------
Add this file to asset_patch_vuln/models/ and register it in
models/__init__.py AFTER asset_cve_diagnostics:

    from . import asset_cve_diagnostics
    from . import asset_cve_key_validation   # must come after

Then upgrade the module.
"""

import logging
import re

from odoo import models, api

_logger = logging.getLogger(__name__)

# NVD keys are UUIDs, e.g. 3f2a1b4c-5d6e-7f80-91a2-b3c4d5e6f708
NVD_KEY_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)
NVD_KEY_LENGTH = 36


class AssetCveKeyValidation(models.Model):
    _inherit = 'asset.cve'

    @api.model
    def _get_validated_nvd_key(self):
        """Return the API key only if it looks like a real one.

        Returns (key_or_empty, problem_description).
        """
        raw = self.env['ir.config_parameter'].sudo().get_param(
            'asset_vulnerability.nvd_api_key', '') or ''
        key = raw.strip()

        if not key:
            return '', ''

        # Common paste accidents
        if key != raw:
            _logger.warning(
                '[CVE] NVD API key had surrounding whitespace — trimmed.')

        if len(key) != NVD_KEY_LENGTH:
            problem = (
                f'The stored NVD API key is {len(key)} characters. A real key '
                f'is a {NVD_KEY_LENGTH}-character UUID '
                f'(8-4-4-4-12 hex digits).\n\n'
                'Sending an invalid key makes NVD return HTTP 403 — which is '
                'worse than sending no key at all, because without a key the '
                'API still works, just rate-limited.\n\n'
                'Either delete the system parameter '
                'asset_vulnerability.nvd_api_key, or replace it with a valid '
                'key from https://nvd.nist.gov/developers/request-an-api-key'
            )
            _logger.warning('[CVE] Ignoring malformed NVD API key (%s chars).',
                            len(key))
            return '', problem

        if not NVD_KEY_RE.match(key):
            problem = (
                'The stored NVD API key is the right length but not valid '
                'UUID format (expected hex digits and hyphens as '
                '8-4-4-4-12).\n\n'
                'It is being ignored. Delete or correct the system parameter '
                'asset_vulnerability.nvd_api_key'
            )
            _logger.warning('[CVE] Ignoring NVD API key — not UUID format.')
            return '', problem

        return key, ''

    # ══════════════════════════════════════════════════════════════════════
    # Use the validated key everywhere
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def _nvd_request(self, params, timeout=90, max_retries=3):
        """Same as the base implementation, but never sends a malformed key."""
        key, problem = self._get_validated_nvd_key()
        if problem:
            _logger.info('[CVE] Proceeding without an API key. %s',
                         problem.split('\n')[0])

        # Temporarily blank the parameter so the parent implementation does
        # not re-read the malformed value, then restore it afterwards.
        Param = self.env['ir.config_parameter'].sudo()
        original = Param.get_param('asset_vulnerability.nvd_api_key', '')
        try:
            Param.set_param('asset_vulnerability.nvd_api_key', key)
            return super()._nvd_request(params, timeout=timeout,
                                        max_retries=max_retries)
        finally:
            if original != key:
                Param.set_param('asset_vulnerability.nvd_api_key', original)

    @api.model
    def test_nvd_connectivity(self):
        """Add key-format validation to the diagnostic report."""
        result = super().test_nvd_connectivity()

        key, problem = self._get_validated_nvd_key()

        # Replace the parent's naive "key present" step with a real check.
        steps = []
        for step in result.get('steps', []):
            if step.get('step') == 'API key configured':
                if problem:
                    steps.append({
                        'step': 'API key valid',
                        'ok': False,
                        'detail': problem.split('\n')[0],
                    })
                elif key:
                    steps.append({
                        'step': 'API key valid',
                        'ok': True,
                        'detail': 'Valid UUID format.',
                    })
                else:
                    steps.append({
                        'step': 'API key valid',
                        'ok': True,
                        'detail': 'No key set — rate limited to 5 requests '
                                  'per 30 seconds, which is fine for a small '
                                  'fleet.',
                    })
            else:
                steps.append(step)

        result['steps'] = steps

        # If the only problem was a bad key, say so prominently.
        if problem and not result.get('ok'):
            result['verdict'] = (
                'MALFORMED API KEY.\n\n' + problem +
                '\n\nThis is the most likely cause of the sync failure. '
                'NVD rejects an invalid key with HTTP 403, which looks '
                'identical to a firewall block.'
            )

        return result