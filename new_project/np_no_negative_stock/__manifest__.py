{
    'name': 'No Negative Stock',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Block any operation that would drive on-hand quantity below zero',
    'description': """
No Negative Stock
=================
Prevents stock quantities in internal (and transit) locations from going
negative. Enforced at the quant level, so it covers deliveries, manufacturing,
scraps, inventory adjustments and any other stock move.

A per-company switch is provided to allow negative stock again if ever needed.
""",
    'author': 'Futurenet',
    'depends': ['stock'],
    'data': [
        'views/res_config_settings_views.xml',
    ],
    'license': 'LGPL-3',
    'installable': True,
    'application': False,
}
