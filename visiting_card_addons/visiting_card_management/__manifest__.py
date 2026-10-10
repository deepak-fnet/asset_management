{
    'name': 'Visiting Card Management',
    'version': '19.0.1.0.0',
    'category': 'Sales/CRM',
    'summary': 'Scan customer visiting cards, build a master contact record, and convert it into a CRM lead/opportunity',
    'description': """
Visiting Card Management
=========================
Sales executives meet customers in the field and collect visiting/business cards.
This module lets them capture the card in Odoo (photo + extracted details), builds
a "Visiting Card" master record, and from that master:

* Creates/links a Contact (res.partner) master
* Converts the card into a CRM Lead/Opportunity in one click

Flow: Scan Card -> Master (Visiting Card) -> Contact Master -> CRM Lead
""",
    'author': 'FutureNet',
    'depends': ['base', 'mail', 'contacts', 'crm', 'hr', 'partner_creation'],
    'data': [
        'security/visiting_card_security.xml',
        'security/ir.model.access.csv',
        'data/visiting_card_sequence.xml',
        'data/ai_config.xml',
        'wizard/visiting_card_scan_wizard_views.xml',
        'views/visiting_card_views.xml',
        'views/res_partner_views.xml',
        'views/crm_lead_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'visiting_card_management/static/src/scss/visiting_card.scss',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
