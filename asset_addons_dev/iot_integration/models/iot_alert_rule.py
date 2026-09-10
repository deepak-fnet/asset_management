# -*- coding: utf-8 -*-
"""Temperature-threshold email alerts for incoming IoT readings.

Deliberately its own small model rather than reusing asset_telemetry_alert's
generic rule/log/engine trio: that engine is built around asset.asset's own
telemetry JSON and a hard dependency on asset_management, whereas iot.data
is a standalone demo model with no relation to any asset. Same idea (a
threshold, a cooldown, a plain mail.mail sent directly) at a fifth of the
size - right-sized for what this module actually needs.
"""
import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class IotAlertRule(models.Model):
    _name = 'iot.alert.rule'
    _description = 'IoT Temperature Alert Rule'
    _order = 'id desc'

    name = fields.Char(required=True, default='Temperature Alert')
    device_id = fields.Char(
        string='Device ID',
        help="Leave blank to watch every device's readings.",
    )
    min_temperature = fields.Float(
        string='Minimum Temperature (°C)', required=True, default=2.0,
        help="A reading below this value triggers the alert.",
    )
    mail_to = fields.Char(
        string='Notify (emails)', required=True,
        help="Comma-separated email addresses to notify when triggered.",
    )
    active = fields.Boolean(default=True)
    cooldown_minutes = fields.Integer(
        string='Cooldown (minutes)', default=60,
        help="Minimum time between two alert emails for this rule, so one "
             "cold snap does not send a mail per reading.",
    )
    last_triggered_date = fields.Datetime(readonly=True, copy=False)
    trigger_count = fields.Integer(readonly=True, copy=False, default=0)

    def _applies_to(self, iot_data_record):
        self.ensure_one()
        return not self.device_id or self.device_id == iot_data_record.device_id

    def _cooldown_elapsed(self):
        self.ensure_one()
        if not self.last_triggered_date:
            return True
        elapsed = fields.Datetime.now() - self.last_triggered_date
        return elapsed.total_seconds() >= self.cooldown_minutes * 60

    @api.model
    def _check_and_notify(self, iot_data_record):
        """Called right after a new iot.data reading is stored.

        Wrapped in try/except by the caller (iot.data.create_from_payload)
        so a problem here - a bad rule, a mail server hiccup - never breaks
        ingestion of the reading itself.
        """
        if iot_data_record.temperature is None:
            return
        rules = self.sudo().search([('active', '=', True)])
        for rule in rules:
            if not rule._applies_to(iot_data_record):
                continue
            if iot_data_record.temperature >= rule.min_temperature:
                continue
            if not rule._cooldown_elapsed():
                continue
            rule._send_alert_mail(iot_data_record)
            rule.write({
                'last_triggered_date': fields.Datetime.now(),
                'trigger_count': rule.trigger_count + 1,
            })

    def _send_alert_mail(self, iot_data_record):
        self.ensure_one()
        recipients = [e.strip() for e in (self.mail_to or '').split(',') if e.strip()]
        if not recipients:
            _logger.warning(
                'IoT alert rule %s triggered but has no recipients configured.',
                self.name)
            return
        subject = _('Temperature alert: %(device)s is at %(temp).1f°C') % {
            'device': iot_data_record.device_id,
            'temp': iot_data_record.temperature,
        }
        body = _(
            '<p>Device <strong>%(device)s</strong> reported a temperature of '
            '<strong>%(temp).1f°C</strong>, below the configured minimum of '
            '<strong>%(min)s°C</strong>.</p>'
            '<p>Reading received on %(date)s.</p>'
        ) % {
            'device': iot_data_record.device_id,
            'temp': iot_data_record.temperature,
            'min': self.min_temperature,
            'date': iot_data_record.received_date,
        }
        self.env['mail.mail'].sudo().create({
            'subject': subject,
            'body_html': body,
            'email_to': ','.join(recipients),
        }).send()
        _logger.info(
            'IoT alert "%s" triggered for device %s (%.1f°C) -> %s',
            self.name, iot_data_record.device_id, iot_data_record.temperature,
            recipients)
