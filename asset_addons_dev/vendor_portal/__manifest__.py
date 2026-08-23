# -*- coding: utf-8 -*-
{
    "name": "Vendor Portal",
    "version": "19.0.1.0.0",
    "category": "Inventory/Purchase",
    "summary": "Let vendors view their RFQs and submit bids from the portal",
    "description": """
Vendor Portal
=============

Vendors log in as portal users, see the RFQs addressed to them, enter a unit
price per line, and submit. Submitting runs the same action_bid_received()
that the internal Bid Received button uses, so the RFQ ends up in exactly the
same state whether the bid came from the portal or was keyed in by a buyer.

Only draft/sent RFQs are visible and editable. Once a bid is submitted the
page becomes read-only - otherwise a vendor could keep revising prices on an
order that is already being evaluated.
    """,
    "author": "Axles India Ltd.",
    "license": "LGPL-3",
    "depends": ["purchase", "portal", "purchase_extended"],
    "data": [
        "security/ir.model.access.csv",
        "views/portal_templates.xml",
        "views/purchase_order_views.xml",
    ],
    "installable": True,
    "application": False,
}
