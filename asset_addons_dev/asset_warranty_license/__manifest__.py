# -*- coding: utf-8 -*-
{
    "name": "Asset Warranty & License Management",
    "version": "19.0.1.0.0",
    "category": "Administration",
    "summary": "Warranty claims and software license compliance for assets",
    "description": """
Asset Warranty & License Management
===================================

Adds two things asset_management did not cover, under one menu.

Warranty Management
-------------------
Deliberately does NOT introduce a warranty contract model. asset_management
already stores warranty dates on asset.asset, and the agent and asset.request
pipelines already write to them. This module only adds the detail that was
missing - provider, warranty type, contract number, support contact, coverage
notes - plus a full claims history per asset.

License Management
------------------
Manual entry, always linked to an asset. The point is seat compliance:
assigned seats are counted from real assignment rows rather than typed in, so
over-deployment is visible before an audit finds it. Seats cannot be assigned
beyond the licensed count.
    """,
    "author": "Axles India Ltd.",
    "license": "LGPL-3",
    "depends": [
        "base",
        "mail",
        "hr",
        "asset_management",
        # Needed to inherit general_asset.view_asset_general_form - the
        # General Asset form gets its Warranty/Licence pages wired to this
        # module's real flows (warranty claims, seat-tracked licenses)
        # instead of the simple, disconnected fields general_asset shipped
        # with on its own.
        "general_asset",
    ],
    "data": [
        "security/warranty_license_security.xml",
        "security/ir.model.access.csv",
        "data/warranty_license_sequence.xml",
        "data/warranty_license_cron.xml",
        "views/asset_warranty_views.xml",
        "views/asset_license_views.xml",
        "views/asset_asset_views_inherit.xml",
        "views/asset_general_form_inherit.xml",
        "views/warranty_license_menus.xml",
    ],
    "installable": True,
    "application": False,
}
