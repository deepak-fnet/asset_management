# -*- coding: utf-8 -*-
{
    "name": "General Asset",
    "version": "19.0.1.0.0",
    "category": "Administration",
    "summary": "Non-IT assets: plant, machinery, furniture - with transfer, scrap and verification",
    "description": """
General Asset
=============

Adds a register for assets that have no agent - machinery, furniture, plant
equipment - on top of asset_management.

Design
------
General assets are asset.asset records whose category is flagged
is_general. There is deliberately NO separate asset.addition model: keeping
one asset table means transfers, scrap, warranty, licences and the asset.list
procurement chain all work on general assets without a parallel
implementation of each.

Adds:
  * General Assets menu (Assets)
  * Asset Transfer and Asset Scrap (Lifecycle Processes) - submit/approve/done
  * Physical Verification (Asset Request)
  * Configuration: Plant, Asset Sub Category, Product Category Mapping,
    RFID Comparison

Physical Location, From Location and To Location all use asset_management's
asset.location - one hierarchical model rather than three flat ones.

Uninstalling this module removes the general-asset menus and its own models.
asset.asset records created through it remain, since they are ordinary assets.
""",
    "author": "Futurenet Technologies",
    "license": "LGPL-3",
    "depends": ["asset_management"],
    "data": [
        "security/ir.model.access.csv",
        "data/asset_general_sequence.xml",
        "views/asset_general_views.xml",
        "views/asset_general_lifecycle_views.xml",
        "views/asset_general_menus.xml",
        "views/asset_joining_domain_inherit.xml",
    ],
    "installable": True,
    "application": False,
}
