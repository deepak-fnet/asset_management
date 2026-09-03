import json
import logging

import requests

from odoo import fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DEFAULT_PAYLOAD = """{
    "device_id": "FRIDGE-001",
    "sensor_type": "refrigerator_temperature",
    "temperature": 4.5,
    "humidity": 38.0,
    "battery_level": 92,
    "door_open": false
}"""


class IotTemplate(models.Model):
    _name = 'iot.template'
    _description = 'IoT JSON Template'

    name = fields.Char(string='Name', required=True, default='Refrigerator Sensor Payload')
    target_url = fields.Char(
        string='Target API URL', required=True,
        help='Endpoint that will receive this payload, e.g. '
             'http://localhost:8170/iot/api/receive')
    json_payload = fields.Text(string='JSON Payload', required=True, default=DEFAULT_PAYLOAD)
    last_response = fields.Text(string='Last Response', readonly=True)
    last_status = fields.Char(string='Last Status', readonly=True)
    last_sent_date = fields.Datetime(string='Last Sent On', readonly=True)

    def action_send_to_api(self):
        self.ensure_one()
        try:
            payload = json.loads(self.json_payload)
        except ValueError as exc:
            raise UserError('The JSON payload is not valid: %s' % exc)

        try:
            response = requests.post(self.target_url, json=payload, timeout=10)
            self.write({
                'last_status': str(response.status_code),
                'last_response': response.text,
                'last_sent_date': fields.Datetime.now(),
            })
        except requests.RequestException as exc:
            self.write({
                'last_status': 'error',
                'last_response': str(exc),
                'last_sent_date': fields.Datetime.now(),
            })
            raise UserError('Failed to send payload to API: %s' % exc)
        return True
