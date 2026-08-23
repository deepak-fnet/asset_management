{
    'name': 'Asset',
    'version': '1.0',
    'summary': 'Monitor employee laptop RAM and alert admin on changes',
    'author': 'Your Name',
    'depends': ['base', 'mail', 'hr', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'views/asset.xml',
        'views/transfer_view.xml',
        'views/removal.xml',
        'views/config.xml',
        'views/dashboard.xml',
        'views/menu_item.xml'
    ],
    'assets': {
        'web.assets_backend': [
            'asset/static/src/css/style.css',
            'asset/static/src/xml/asset_dashboard.xml',
            'asset/static/src/js/dashboard.js',
        ],
    },
    'installable': True,
    'application': True,
}
