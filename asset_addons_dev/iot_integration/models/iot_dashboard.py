# -*- coding: utf-8 -*-
from odoo import api, fields, models, _

# Enough to plot a readable line without the request or the chart itself
# getting slow - a demo dashboard, not a historian.
CHART_POINT_LIMIT = 50
DOOR_EVENT_LIMIT = 10
SEARCH_LIMIT = 2000


class IotDashboard(models.AbstractModel):
    _name = 'iot.dashboard'
    _description = 'IoT Dashboard Data'

    @api.model
    def get_filter_options(self):
        rows = self.env['iot.data'].sudo()._read_group(
            [], ['device_id'], [])
        devices = sorted({r[0] for r in rows if r[0]})
        return {'devices': devices}

    @api.model
    def _build_domain(self, filters):
        filters = filters or {}
        domain = []
        if filters.get('device_id'):
            domain.append(('device_id', '=', filters['device_id']))
        if filters.get('date_from'):
            domain.append(('received_date', '>=', filters['date_from']))
        if filters.get('date_to'):
            domain.append(('received_date', '<=', filters['date_to']))
        return domain

    @api.model
    def get_dashboard_data(self, filters=None):
        Data = self.env['iot.data'].sudo()
        domain = self._build_domain(filters)
        # Fetch the most recent SEARCH_LIMIT readings (not the oldest), then
        # restore chronological order for the chart/latest-record logic below.
        records = Data.search(domain, order='received_date desc', limit=SEARCH_LIMIT)
        records = records.sorted('received_date')

        door_events = records.filtered('door_open')
        alert_records = records.filtered(lambda r: r.state == 'alert')
        latest = records[-1] if records else Data.browse()

        chart_records = records[-CHART_POINT_LIMIT:]
        temperature_series = {
            'labels': [fields.Datetime.to_string(r.received_date) for r in chart_records],
            'data': [r.temperature for r in chart_records],
        }

        recent_door_events = door_events.sorted('received_date', reverse=True)[:DOOR_EVENT_LIMIT]
        door_events_data = [{
            'device_id': e.device_id,
            'received_date': fields.Datetime.to_string(e.received_date),
        } for e in recent_door_events]

        rules = self.env['iot.alert.rule'].sudo().search([])

        return {
            'kpis': {
                'total_readings': len(records),
                'door_open_count': len(door_events),
                'alert_count': len(alert_records),
                'latest_temperature': latest.temperature if latest else None,
                'latest_device': latest.device_id if latest else '',
                'latest_received_date': (
                    fields.Datetime.to_string(latest.received_date) if latest else ''),
                'active_rules': len(rules.filtered('active')),
                'total_rules': len(rules),
            },
            'temperature_series': temperature_series,
            'door_events': door_events_data,
        }

    @api.model
    def open_iot_data(self, filters=None):
        return {
            'type': 'ir.actions.act_window',
            'name': _('IoT Data'),
            'res_model': 'iot.data',
            'views': [(False, 'list'), (False, 'form')],
            'domain': self._build_domain(filters),
        }

    @api.model
    def open_door_events(self, filters=None):
        domain = self._build_domain(filters) + [('door_open', '=', True)]
        return {
            'type': 'ir.actions.act_window',
            'name': _('Door Open Events'),
            'res_model': 'iot.data',
            'views': [(False, 'list'), (False, 'form')],
            'domain': domain,
        }

    @api.model
    def open_alert_records(self, filters=None):
        domain = self._build_domain(filters) + [('state', '=', 'alert')]
        return {
            'type': 'ir.actions.act_window',
            'name': _('Alert Readings'),
            'res_model': 'iot.data',
            'views': [(False, 'list'), (False, 'form')],
            'domain': domain,
        }

    @api.model
    def open_alert_rules(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('IoT Alert Rules'),
            'res_model': 'iot.alert.rule',
            'views': [(False, 'list'), (False, 'form')],
        }
