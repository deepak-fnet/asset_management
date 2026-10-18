{
    'name': 'Network Browser',
    'version': '19.0.1.0.0',
    'category': 'Tools',
    'summary': "A browser inside Odoo that opens pages from the server's own network",
    'author': 'Futurenet',
    'depends': ['base', 'web'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/data.xml',
        'views/views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'remote_browser/static/src/scss/browser.scss',
            'remote_browser/static/src/xml/browser.xml',
            'remote_browser/static/src/js/browser.js',
        ],
    },
    'application': True,
    'installable': True,
    'license': 'LGPL-3',
}
