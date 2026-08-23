# -*- coding: utf-8 -*-
"""Agent endpoint for reporting listening ports."""

import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class PortsAPI(http.Controller):

    @http.route('/api/asset/ports/report', type='http', auth='public',
                methods=['POST'], csrf=False)
    def ports_report(self, **kwargs):
        """Body: {"serial_number": "...", "ports": [22, 80, 443, 3389]}"""
        try:
            payload = json.loads(request.httprequest.data or '{}')
            serial = (payload.get('serial_number') or '').strip()
            asset = request.env['asset.asset'].sudo().search(
                [('serial_number', '=', serial)], limit=1)
            if not asset:
                return self._json({'success': False, 'message': 'Asset not found'})

            count = request.env['asset.listening.port'].sudo().record_ports(
                asset, payload.get('ports') or []
            )
            _logger.info('[Ports] %s reported %s listening port(s)',
                         asset.display_name, count)
            return self._json({'success': True, 'ports_recorded': count})

        except Exception as exc:
            _logger.error('[Ports] report error: %s', exc, exc_info=True)
            return self._json({'success': False, 'message': str(exc)})

    @staticmethod
    def _json(data):
        return request.make_response(
            json.dumps(data), headers=[('Content-Type', 'application/json')]
        )
