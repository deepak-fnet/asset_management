# -*- coding: utf-8 -*-
{
    'name': 'Asset Helpdesk',
    'version': '19.0.1.0.0',
    'summary': 'Basic Asset Helpdesk ticketing module',
    'description': """
Asset Helpdesk
==============
A basic helpdesk module to manage asset related support tickets.

Features:
---------
- Create and track helpdesk tickets with auto-generated ticket numbers.
- Organize tickets by team, priority, type and category.
- Follow the ticket lifecycle: Open -> In Progress -> Solved / Cancelled.
- Manage helpdesk teams and their members.
""",
    'category': 'Services/Helpdesk',
    'author': 'Your Company',
    'website': 'https://www.yourcompany.com',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'asset_management', 'stock', 'website'],
    'data': [
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'data/helpdesk_type_data.xml',
        'data/mail_template_ticket_confirmation.xml',
        'views/helpdesk_team_views.xml',
        'views/helpdesk_config_views.xml',
        'views/helpdesk_views.xml',
        'views/repair_management_views_inherit.xml',
        'views/portal_ticket_form_templates.xml',
        'views/menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'asset_helpdesk/static/src/css/asset_helpdesk_kanban.css',
        ],
        'web.assets_frontend': [
            'asset_helpdesk/static/src/css/portal_ticket_form.css',
            'asset_helpdesk/static/src/js/portal_ticket_form.js',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
}
