# -*- coding: utf-8 -*-
"""
Agent endpoint for Windows 11 eligibility reporting.

Follows the same conventions as the existing Windows Update endpoints in
asset_agent_api.py: type='http', auth='public', identified by serial_number
in the JSON body, JSON response.
"""

import json
import logging

from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)


class Win11EligibilityAPI(http.Controller):

    @http.route('/api/asset/win11/report', type='http', auth='public',
                methods=['POST'], csrf=False)
    def win11_report(self, **kwargs):
        """Agent posts the machine's Windows 11 compatibility facts.

        Expected body:
            {
              "serial_number": "PF2Q10DW",
              "tpm_present": true,
              "tpm_version": "2.0",
              "secure_boot": true,
              "uefi_mode": true,
              "cpu_supported": true,
              "verdict": "eligible"          # optional free-text
            }
        """
        try:
            payload = json.loads(request.httprequest.data or '{}')
            serial_number = (payload.get('serial_number') or '').strip()

            if not serial_number:
                return self._json({'success': False,
                                   'message': 'serial_number is required'})

            Asset = request.env['asset.asset'].sudo()
            asset = Asset.search([('serial_number', '=', serial_number)], limit=1)
            if not asset:
                _logger.warning('[Win11] Asset not found: %s', serial_number)
                return self._json({'success': False, 'message': 'Asset not found'})

            tpm_version = (payload.get('tpm_version') or '').strip()

            asset.write({
                'win11_tpm_present': bool(payload.get('tpm_present')),
                'win11_tpm_version': tpm_version or False,
                'win11_secure_boot': bool(payload.get('secure_boot')),
                'win11_uefi_mode': bool(payload.get('uefi_mode')),
                'win11_cpu_supported': bool(payload.get('cpu_supported')),
                'win11_agent_verdict': (payload.get('verdict') or '')[:255] or False,
                'win11_reported_date': fields.Datetime.now(),
            })

            _logger.info(
                '[Win11] %s reported — TPM:%s SecureBoot:%s UEFI:%s CPU:%s → %s',
                serial_number,
                tpm_version or 'none',
                payload.get('secure_boot'),
                payload.get('uefi_mode'),
                payload.get('cpu_supported'),
                asset.win11_status,
            )

            return self._json({
                'success': True,
                'serial_number': serial_number,
                'status': asset.win11_status,
            })

        except Exception as exc:
            _logger.error('[Win11] Report error: %s', exc, exc_info=True)
            return self._json({'success': False, 'message': str(exc)})

    @staticmethod
    def _json(data):
        return request.make_response(
            json.dumps(data),
            headers=[('Content-Type', 'application/json')],
        )
