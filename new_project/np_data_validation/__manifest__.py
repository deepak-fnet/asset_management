{
    'name': 'Data Validation and Restrictions',
    'version': '19.0.1.0.0',
    'category': 'Extra Tools',
    'summary': 'Backdating checks and post-confirmation locking on CRM, Sales, Purchase, Invoicing and Stock',
    'description': """
Data Validation and Restrictions
================================
* No back-dating: order / invoice / delivery dates cannot be set before today.
* Coherent dates: expected arrival, delivery, due date and deadlines must not
  precede the document date.
* No negative or zero quantities and no negative prices on order lines.
* Once a document is confirmed, its lines can no longer be added, deleted or
  re-priced -- the "Add a line" and delete buttons disappear from the view.
* Confirmed / posted documents cannot be deleted, only cancelled.
""",
    'author': 'Futurenet',
    'depends': ['crm', 'sale_management', 'sale_stock', 'purchase', 'purchase_stock',
                'account', 'stock'],
    'data': [
        'views/purchase_views.xml',
        'views/sale_views.xml',
    ],
    'license': 'LGPL-3',
    'installable': True,
    'application': False,
}
