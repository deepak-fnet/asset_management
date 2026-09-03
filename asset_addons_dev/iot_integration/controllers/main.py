import json
import logging

from odoo import http
from odoo.http import Response, request

_logger = logging.getLogger(__name__)


class IotIntegrationController(http.Controller):

    @http.route('/iot/api/receive', type='http', auth='public', methods=['POST'], csrf=False)
    def receive_iot_data(self, **kwargs):
        """Inbound endpoint an IoT device (e.g. a fridge sensor) calls to push a reading.

        Accepts a plain JSON body (not JSON-RPC wrapped), as a real IoT device would send.
        """
        try:
            payload = json.loads(request.httprequest.data or b'{}')
        except ValueError:
            return Response(
                json.dumps({'result': 'error', 'message': 'invalid JSON body'}),
                status=400, content_type='application/json')

        record = request.env['iot.data'].sudo().create_from_payload(
            payload, source_ip=request.httprequest.remote_addr)
        return Response(
            json.dumps({'result': 'ok', 'record_id': record.id}),
            status=200, content_type='application/json')
