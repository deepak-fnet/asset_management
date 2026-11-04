{
    'name': 'Asset Budget',
    'version': '19.0.1.1.0',
    'summary': 'Budget allocation by product category and department for requests and their purchases',
    'description': """
Budget per product category + department + period (NewGen budget allocation).

* Request: department from the requester; each line shows its budget and the
  estimated cost against the balance (warning only).
* RFQ / purchase order raised from a request: checked on Submit for Approval.
  Over budget either blocks, or adds the Budget Approver as the last approval
  level (Settings > Purchase > Budget).
* Reserved (In Process) on submission, moved to the vendor orders when the RFQ
  is split, Spent when the vendor bill is posted, released on cancel / reset.
* Transfers between budgets, additional and surrendered budget, with history.
* Purchases that do not come from a request do not touch any budget.
""",
    'author': 'FutureNet Technologies',
    'category': 'Purchase',
    'depends': ['asset_management', 'vendor_management', 'purchase_approval', 'account', 'hr'],
    'data': [
        'security/ir.model.access.csv',
        'wizard/budget_transfer_views.xml',
        'views/asset_budget_views.xml',
        'views/asset_request_views.xml',
        'views/purchase_order_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
    'license': 'LGPL-3',
}
