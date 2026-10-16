{
    'name': 'DMS - Document Management',
    'version': '19.0.1.0.0',
    'category': 'Productivity/Documents',
    'summary': 'Controlled document repository: approved uploads, revisions, cut-off, archive and approved disposal',
    'author': 'Futurenet Technologies India Private Limited',
    'depends': ['base', 'mail', 'hr', 'web'],
    'external_dependencies': {'python': ['paramiko']},
    'data': [
        'security/dms_security.xml',
        'security/ir.model.access.csv',
        'data/dms_data.xml',
        'views/dms_config_views.xml',
        'views/dms_views.xml',
        'views/dms_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'dms/static/src/scss/dms.scss',
            'dms/static/src/js/dms_sound.js',
            'dms/static/src/js/dms_app.js',
            'dms/static/src/xml/dms_app.xml',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'application': True,
    'license': 'LGPL-3',
}
