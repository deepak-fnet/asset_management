# -*- coding: utf-8 -*-
{
    'name': '180-Day Payment Compliance Alert',
    'version': '19.0.1.0.0',
    'summary': 'Statutory compliance alert — MSME 45-day & General 180-day (Section 43B(h))',
    'description': """
        Automatically flags vendor bills pending payment.
        - MSME vendors (Udyog Aadhaar / Udyam number on the vendor): 45-day alert
        - All vendors: 180-day alert (Section 43B(h))

        Features:
        - MSME status auto-detected from the vendor's Udyog Aadhaar / Udyam number
        - Daily cron sets compliance flags
        - Systray badge with split MSME / General count
        - Pop-up dialog with MSME + 43B(h) statutory text
        - Separate menus: MSME 45-Day | 180-Day General | All Alerts
        - Audit log with timestamped acknowledgements
    """,
    'author': 'FutureNet',
    'website': 'https://futurenet.in',
    'category': 'Accounting/Accounting',
    'depends': ['account', 'mail', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/res_partner_views.xml',
        'views/compliance_alert_log_views.xml',
        'views/account_move_views.xml',
        'views/account_move_compliance_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'payment_compliance_alert/static/src/scss/compliance_alert.scss',
            'payment_compliance_alert/static/src/xml/compliance_systray.xml',
            'payment_compliance_alert/static/src/js/compliance_systray.js',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
