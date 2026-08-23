{
    "name": "Purchase Approval",
    "version": "19.0.1.0.0",
    "category": "Inventory/Purchase",
    "summary": "Multi-approver sign-off required before a Purchase Order can be confirmed",
    "description": """
Adds a multi-approver approval step ahead of Purchase Order confirmation.

    - Pick one or more Approvers on the RFQ.
    - "Submit for Approval" creates one approval line per approver and an
      activity assigned to each of them.
    - Each approver sees Approve / Reject only on their own line.
    - The Confirm Order button stays hidden until every approver has approved.
    - Enforced at the model level too (not just the view), so the order
      cannot reach the 'purchase' state while any approval is outstanding,
      regardless of which button or code path is used to confirm it.

This module is standalone: it only depends on 'purchase' and 'mail', and does
not assume any other custom purchase module is installed.
""",
    "author": "",
    "license": "LGPL-3",
    "depends": ["purchase", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "views/purchase_approval_config_views.xml",
        "views/purchase_order_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
