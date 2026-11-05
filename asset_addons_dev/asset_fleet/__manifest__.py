{
    'name': 'Asset Agent - Install Link & Telemetry Policies',
    'version': '19.0.3.0.0',
    'summary': 'Install the asset agent from an Odoo link (no .deb per server) and configure what '
               'Agent Telemetry collects - sections, interval and custom commands - per policy',
    'category': 'Inventory',
    'author': 'Futurenet',
    'depends': ['asset_management', 'asset_telemetry', 'asset_workspace', 'general_asset', 'hr'],
    'data': [
        'security/ir.model.access.csv',
        'data/fleet_data.xml',
        'views/fleet_views.xml',
        'views/screentime_views.xml',
        'views/fixed_sync_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'asset_fleet/static/src/asset360/asset360.js',
            'asset_fleet/static/src/asset360/asset360.xml',
            'asset_fleet/static/src/asset360/asset360.scss',
            'asset_fleet/static/src/asset360/screen_tab.scss',
            'asset_fleet/static/src/asset360/fixed_card.scss',
        ],
    },
    'license': 'LGPL-3',
    'installable': True,
}
