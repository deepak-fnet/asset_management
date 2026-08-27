{
    'name': 'Asset Purchase Integration',
    'version': '19.0.2.0.0',
    'summary': 'Create fixed asset records automatically when a purchase receipt is validated',
    'description': """
Asset Purchase Integration
==========================

Bridges the purchase/receipt flow with the asset register:

- "Is Fixed Asset" flag on receipt lines and purchase order lines, auto-ticked
  from the product category.
- On receipt validation, one asset.asset record is created per unit
  received (qty 10 -> 10 assets). These are general assets, so they appear
  under Assets > General Assets.
- Product category to asset defaults mapping (machine type, plant, location,
  department).
- Traceability from each asset back to its receipt, purchase order and serial.
""",
    'author': 'Futurenet Technologies',
    'category': 'Inventory/Purchase',
    'license': 'LGPL-3',
    # general_asset owns asset.asset's general-asset extension and the views
    # this module inherits. vendor_management was declared but never used by
    # any code here, so it is dropped.
    'depends': [
        'general_asset',
        'stock',
        'purchase',
        'purchase_stock',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/asset_category_mapping_views.xml',
        'views/product_asset_views.xml',
        'views/asset_addition_views.xml',
        'views/stock_picking_views.xml',
        'views/purchase_order_views.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
