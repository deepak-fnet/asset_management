{
    'name': 'IoT Integration',
    'version': '19.0.1.0.0',
    'category': 'Internet of Things',
    'summary': 'Demo module: receive IoT sensor data via API and send test payloads to an external API',
    'description': """
IoT Integration (Demo)
=======================
Simulates a real world IoT scenario: a sensor (e.g. inside a refrigerator)
periodically sends readings to an API endpoint. This module provides:

* **IoT Data**: records created automatically when data is received on the
  inbound API endpoint (``/iot/api/receive``).
* **IoT JSON Template**: a pre-built JSON payload that a user can edit and
  send (via the "Send to API" button) to any configured API URL - including
  back to this same Odoo instance, to simulate the sensor.
""",
    'author': 'FutureNet',
    'depends': ['base', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'views/iot_data_views.xml',
        'views/iot_template_views.xml',
        'views/iot_menus.xml',
        'data/iot_template_demo.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
