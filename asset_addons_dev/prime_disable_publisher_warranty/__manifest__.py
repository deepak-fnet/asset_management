{
    'name': 'Disable Publisher Warranty',
    'version': '19.0.1.0.0',
    'category': 'Technical',
    'summary': 'Disables Odoo publisher warranty phone-home and enterprise notifications',
    'description': """
Blocks the weekly publisher_warranty.contract cron from sending any data
to services.odoo.com and prevents enterprise-related messages from being
posted to the All Employees channel.
    """,
    'depends': ['mail'],
    'data': [
        'data/ir_cron_data.xml',
    ],
    'installable': True,
    'auto_install': True,
    'author': 'FNET',
    'license': 'LGPL-3',
}
