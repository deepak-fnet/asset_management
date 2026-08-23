# -*- coding: utf-8 -*-
{
    'name': 'OneIT - Active Directory Automation',
    'version': '19.0.1.0.0',
    'summary': 'Onboarding, offboarding and team-access requests with two-stage '
               'approval, pushed into Active Directory over LDAP.',
    'description': """
OneIT Active Directory Automation
=================================

Re-implementation of the OneIT (Django) AD lifecycle workflow for Odoo 19.

Flow
----
Request created (pending) -> Team Lead approves (selects AD access)
-> BU Head approves (approved) -> execution:

* Onboarding    : AD user created immediately on final approval  -> Done
* Team update   : scheduled action moves the user + regroups     -> Done
* Offboarding   : scheduled action disables the account at the
                  last working day (Ready to delete), then
                  deletes it after the retention period          -> Done

Any request can be rejected at either approval stage.

A Team is the unit of access: each team maps to a set of AD groups tagged
as AD Folder (the OU the user object lives in), Citrix or File Server.
""",
    'author': 'OneIT',
    'category': 'Human Resources',
    'license': 'LGPL-3',
    'depends': ['base', 'mail'],
    'external_dependencies': {
        'python': ['ldap3'],
    },
    'data': [
        'security/oneit_security.xml',
        'security/ir.model.access.csv',
        'security/oneit_record_rules.xml',
        'data/ir_sequence.xml',
        'data/mail_template.xml',
        'data/ir_cron.xml',
        'views/ad_group_views.xml',
        'views/team_views.xml',
        'views/approval_views.xml',
        'views/request_views.xml',
        'views/res_config_settings_views.xml',
        'views/ad_sync_views.xml',
        'views/menus.xml',
        'views/ad_person_views.xml',
        'views/dashboard_views.xml',
    ],
    # Odoo 15 replaced the 'qweb' key and the assets_backend template
    # inherit with this declarative bundle map. OWL component templates go
    # in the same bundle as the JS - they are no longer a separate key.
    'assets': {
        'web.assets_backend': [
            'oneit_ad_automation/static/src/dashboard/oneit_dashboard.scss',
            'oneit_ad_automation/static/src/dashboard/oneit_dashboard.js',
            'oneit_ad_automation/static/src/dashboard/oneit_dashboard.xml',
        ],
    },
    'demo': [
        'data/oneit_demo.xml',
        'data/oneit_demo_requests.xml',
    ],
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
}
