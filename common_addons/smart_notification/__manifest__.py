{
    'name': 'Smart Notifications',
    'version': '19.0.1.0.0',
    'category': 'Productivity',
    'summary': 'Rich in-app notifications for any model: systray, floating bubble, live toasts, '
               'approval tracking (any one / all must act) that closes itself when the action is done',
    'depends': ['base', 'web', 'bus', 'mail'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/notification_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'smart_notification/static/src/scss/smart_notification.scss',
            'smart_notification/static/src/js/notify_service.js',
            'smart_notification/static/src/js/notify_components.js',
            'smart_notification/static/src/xml/notify_templates.xml',
        ],
    },
    'license': 'LGPL-3',
    'installable': True,
    'application': False,
}
