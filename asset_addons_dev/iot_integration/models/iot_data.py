import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class IotData(models.Model):
    _name = 'iot.data'
    _description = 'IoT Sensor Data'
    _order = 'received_date desc'
    _rec_name = 'device_id'

    device_id = fields.Char(string='Device ID', required=True, index=True)
    sensor_type = fields.Char(string='Sensor Type', default='refrigerator_temperature')
    temperature = fields.Float(string='Temperature (°C)')
    humidity = fields.Float(string='Humidity (%)')
    battery_level = fields.Float(string='Battery Level (%)')
    door_open = fields.Boolean(string='Door Open')
    received_date = fields.Datetime(string='Received On', default=fields.Datetime.now, required=True)
    state = fields.Selection([
        ('normal', 'Normal'),
        ('alert', 'Alert'),
    ], string='Status', default='normal', compute='_compute_state', store=True)
    raw_payload = fields.Text(string='Raw JSON Payload')
    source_ip = fields.Char(string='Source IP')

    @api.depends('temperature')
    def _compute_state(self):
        for record in self:
            record.state = 'alert' if record.temperature and record.temperature > 8 else 'normal'

    @api.model
    def create_from_payload(self, payload, source_ip=None):
        """Create an iot.data record from a JSON payload received via the API.

        :param payload: dict decoded from the incoming JSON request.
        :param source_ip: IP address of the caller, for traceability.
        """
        vals = {
            'device_id': payload.get('device_id') or 'unknown',
            'sensor_type': payload.get('sensor_type', 'refrigerator_temperature'),
            'temperature': payload.get('temperature'),
            'humidity': payload.get('humidity'),
            'battery_level': payload.get('battery_level'),
            'door_open': bool(payload.get('door_open')),
            'raw_payload': str(payload),
            'source_ip': source_ip,
        }
        record = self.create(vals)
        _logger.info('IoT data received from device %s: %s', vals['device_id'], vals)
        return record
