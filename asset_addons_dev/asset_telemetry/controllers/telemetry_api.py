# -*- coding: utf-8 -*-
"""
Agent endpoint for raw telemetry.

    POST /api/asset/telemetry/report

Body:
    {
      "serial_number": "PF1VN2YN",
      "telemetry": { ... anything ... }
    }

The `telemetry` object is stored verbatim. There is no schema and no
validation beyond size. That is the point — the agent can start sending a new
section without any Odoo change.
"""

import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class TelemetryAPI(http.Controller):

    @http.route('/api/asset/telemetry/report', type='http', auth='public',
                methods=['POST'], csrf=False)
    def telemetry_report(self, **kwargs):
        try:
            body = json.loads(request.httprequest.data or '{}')
            serial = (body.get('serial_number') or '').strip()

            if not serial:
                return self._json({'success': False,
                                   'message': 'serial_number is required'})

            asset = request.env['asset.asset'].sudo().search(
                [('serial_number', '=', serial)], limit=1)
            if not asset:
                _logger.warning('[Telemetry] Unknown serial: %s', serial)
                return self._json({'success': False,
                                   'message': f'Asset {serial} not found'})

            payload = body.get('telemetry')
            if payload is None:
                return self._json({'success': False,
                                   'message': 'telemetry object is required'})

            # keep_snapshot is opt-out so the agent can send high-frequency
            # updates without filling the history table.
            keep_snapshot = bool(body.get('keep_snapshot', True))

            asset.record_telemetry(payload, keep_snapshot=keep_snapshot)

            sections = list(payload.keys()) if isinstance(payload, dict) else []
            _logger.info('[Telemetry] %s — %s section(s): %s',
                         serial, len(sections), ', '.join(sections[:10]))

            return self._json({
                'success': True,
                'serial_number': serial,
                'sections': sections,
                'size_bytes': asset.agent_telemetry_size,
            })

        except Exception as exc:
            _logger.error('[Telemetry] report error: %s', exc, exc_info=True)
            return self._json({'success': False, 'message': str(exc)})

    @staticmethod
    def _json(data):
        return request.make_response(
            json.dumps(data), headers=[('Content-Type', 'application/json')]
        )
