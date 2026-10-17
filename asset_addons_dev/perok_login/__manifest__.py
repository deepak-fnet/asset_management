{
    'name': 'Perok.ai Login Page',
    'version': '19.0.3.2.0',
    'category': 'Website',
    'summary': 'Modern animated login page inspired by Perok.ai design',
    'description': 'Replaces the default Odoo login page with a Perok.ai-inspired design '
                   'and an animated 3D parrot that reacts to the login form.',
    'author': 'Custom',
    'depends': ['web', 'website'],
    'data': [
        'security/ir.model.access.csv',
        'data/animation_data.xml',
        'views/animation_views.xml',
        'views/login_templates.xml',
    ],
    # CSS/JS are linked straight from the login layout (see login_templates.xml)
    # so they only load on the login pages.
    'assets': {
        # the animation form: live preview and slider fields
        'web.assets_backend': [
            'perok_login/static/src/backend/live_preview.js',
            'perok_login/static/src/backend/live_preview.xml',
            'perok_login/static/src/backend/live_preview.scss',
        ],
    },
    'installable': True,
    'auto_install': False,
    'application': False,
    'license': 'LGPL-3',
}
