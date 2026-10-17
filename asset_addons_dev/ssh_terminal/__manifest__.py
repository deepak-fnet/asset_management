# -*- coding: utf-8 -*-
{
    'name': 'Secure SSH Terminal',
    'version': '19.0.1.0.0',
    'category': 'Tools',
    'summary': 'Secure Web-based SSH Terminal & SFTP File Manager with Approval Workflow',
        'author': 'Futurenet',
    'depends': ['base', 'web', 'mail'],
    'data': [
        # Security first
        'security/security.xml',
        'security/ir.model.access.csv',

        # Data
        'data/ir_sequence.xml',
        'data/ir_cron.xml',
        'data/mail_template.xml',

        # Views
        'views/views.xml',
        'views/templates.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ssh_terminal/static/src/css/terminal.css',
            'ssh_terminal/static/src/css/terminal_ui.css',
            'ssh_terminal/static/lib/xterm/xterm.css',
            'ssh_terminal/static/lib/xterm/xterm.js',
            ('after', 'ssh_terminal/static/lib/xterm/xterm.js',
             'ssh_terminal/static/src/js/ssh_terminal.js'),
            'ssh_terminal/static/src/xml/sftp_filemanager.xml',
            'ssh_terminal/static/src/js/sftp_filemanager.js',
            'ssh_terminal/static/src/xml/ssh_terminal.xml',
        ],
    },
    'external_dependencies': {
        'python': ['paramiko', 'cryptography'],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}