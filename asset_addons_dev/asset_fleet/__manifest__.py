{
    'name': 'Asset Agent - Install Link & Telemetry Policies',
    'version': '19.0.2.5.0',
    'summary': 'Install the asset agent from an Odoo link (no .deb per server) and configure what '
               'Agent Telemetry collects - sections, interval and custom commands - per policy',
    'category': 'Inventory',
    'author': 'Futurenet',
    'depends': ['asset_management', 'asset_telemetry', 'asset_workspace'],
    'data': [
        'security/ir.model.access.csv',
        'data/fleet_data.xml',
        'views/fleet_views.xml',
        'views/screentime_views.xml',
    ],
    'license': 'LGPL-3',
    'installable': True,
}
