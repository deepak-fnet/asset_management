{
    'name': 'Asset',
    'version': '19.0.1.1.0',
    'summary': 'Monitor employee laptop RAM and alert admin on changes',
    'author': 'Your Name',
    'license': 'LGPL-3',
    'depends': ['web', 'mail', 'hr'],
    'data': [
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'views/asset.xml',
        'views/transfer_view.xml',
        'views/removal.xml',
        'views/assignment.xml',
        'views/asset_category.xml',
        'views/config.xml',
        'views/rfid_comparison_views.xml',
        'views/dashboard.xml',
        'views/menu_item.xml'
    ],
    'assets': {
        'web.assets_backend': [
            'asset/static/src/css/style.css',
            'asset/static/src/js/unified_asset_graphs.js',
            'asset/static/src/xml/unified_asset_graphs.xml',
            # 'asset/static/src/xml/asset_dashboard.xml',
            # 'asset/static/src/js/dashboard.js',
        ],
    },
    'installable': True,
    'application': True,
}
