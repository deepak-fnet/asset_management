{
    'name': 'Asset Helpdesk LLM',
    'version': '19.0.1.0.0',
    'summary': 'Send helpdesk notes to a local LLM and store the AI response',
    'description': """
Asset Helpdesk LLM
==================
On creating an asset.helpdesk ticket, the ticket notes are sent to a
locally running LLM (e.g. http://localhost:8001) together with a default
prompt. The LLM response is stored back on the ticket.

Configuration (Settings > Asset Helpdesk LLM):
    * LLM URL
    * Model name
    * Default prompt
    * Timeout
    * Enable / disable
    * Test Connection button
""",
    'author': 'Your Company',
    'category': 'Services/Helpdesk',
    'depends': ['asset_management','asset_helpdesk'],
    'data': [
        'views/res_config_settings_views.xml',
        'views/asset_helpdesk_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
