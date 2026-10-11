# -*- coding: utf-8 -*-
{
    'name': 'Asset Helpdesk',
    'version': '19.0.1.4.0',  # mail entries (From/To/Cc/Subject/Body) with email-style form, mailbox list, Send Mail / Reply / Reply All / Forward; ticket New -> Created -> Closed, no ticket-level assign (assigned on the task), Conversation tab + Reply; All Mails (incoming mailbox + Raise Ticket) with auto acknowledgement, Create Ticket, Create Task, task close closes ticket and mail; Raise Ticket, Assign, diagnosis, not-repairable replace decision (replace / keep standby / no replacement), requester emails
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
        'security/helpdesk_mail_rules.xml',
        'data/sequence.xml',
        'data/helpdesk_mail_data.xml',
        'data/helpdesk_type_data.xml',
        'data/mail_template_ticket_confirmation.xml',
        'views/helpdesk_team_views.xml',
        'views/helpdesk_config_views.xml',
        'views/helpdesk_views.xml',
        'views/helpdesk_raise_views.xml',
        'views/helpdesk_mail_line_views.xml',
        'views/repair_management_views_inherit.xml',
        'views/portal_ticket_form_templates.xml',
        'views/helpdesk_send_mail_templates.xml',
        'views/menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'asset_helpdesk/static/src/css/asset_helpdesk_kanban.css',
            'asset_helpdesk/static/src/scss/helpdesk_mail.scss',
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
