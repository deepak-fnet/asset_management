{
    'name': 'Asset Smart Notifications',
    'version': '19.0.1.0.0',
    'category': 'Productivity',
    'summary': 'Asset workflows talk through Smart Notifications: approvals that close themselves, '
               'hand-over notices, repair, spare, warranty / AMC / licence and verification alerts',
    'depends': ['smart_notification', 'asset_management', 'general_asset', 'asset_purchase',
                'asset_warranty_license', 'purchase_approval', 'vendor_management', 'asset_helpdesk'],
    'data': ['data/cron.xml'],
    'license': 'LGPL-3',
    'installable': True,
    'auto_install': False,
}
