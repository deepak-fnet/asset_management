{
    'name': 'Visitor Desk',
    'summary': 'Front-desk visitor registration with face capture, QR passes and one-tap check-in/out',
    'description': """
Visitor Desk
============

* Receptionist registers a visitor in seconds (returning visitors auto-fill by mobile number)
* Webcam face capture with preview / retake
* Printable visitor pass with photo and QR code
* Security scans the QR pass to check in / check out
* Live "Inside now" board with timers and overstay highlight
* Photo retention cron (privacy)
""",
    'version': '19.0.1.1.0',
    'category': 'Services',
    'author': 'Futurenet',
    'license': 'LGPL-3',
    'depends': ['base', 'web', 'mail'],
    'external_dependencies': {'python': ['qrcode']},
    'data': [
        'security/visitor_security.xml',
        'security/ir.model.access.csv',
        'data/visitor_data.xml',
        'data/visitor_purpose_data.xml',
        'report/visitor_pass_report.xml',
        'views/visitor_visit_views.xml',
        'views/visitor_visitor_views.xml',
        'views/visitor_purpose_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'visitor_desk/static/src/**/*',
        ],
    },
    'application': True,
    'installable': True,
}
