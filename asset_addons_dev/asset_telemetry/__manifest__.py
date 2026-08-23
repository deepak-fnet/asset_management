# -*- coding: utf-8 -*-
{
    "name": "Asset Agent Telemetry",
    "version": "19.0.1.0.0",
    "category": "Administration",
    "summary": "Store and browse arbitrary agent telemetry as JSON",
    "description": """
Raw agent telemetry
===================
The agent posts whatever it collected as one JSON blob. Odoo stores it
verbatim and renders it as a collapsible list with filter, copy and download.

Adding a new section on the agent side needs zero Odoo changes — no field, no
migration, no view edit. It simply appears in the list.

Includes:
  * agent_telemetry JSON field on asset.asset (latest payload)
  * asset.telemetry.snapshot for history, pruned automatically
  * /api/asset/telemetry/report endpoint
  * json_tree field widget
  * agent_snippets/telemetry.py — collects CPU, memory, disks, processes,
    network, services, users, packages, hardware sensors, plus verbatim
    output of df -h, free -h, top, ss, dmesg and others

Trade-off: JSON in one column is not queryable like a real field. This is a
viewer and audit trail, not a reporting surface. If a value turns out to
matter operationally, promote that one value to a real field.
    """,
    "author": "Axles India Ltd.",
    "license": "LGPL-3",
    "depends": ["asset_management"],
    "data": [
        "security/ir.model.access.csv",
        "data/cron_data.xml",
        "views/asset_telemetry_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "asset_telemetry/static/src/css/json_tree.css",
            "asset_telemetry/static/src/xml/json_tree_widget.xml",
            "asset_telemetry/static/src/js/json_tree_widget.js",
        ],
    },
    "installable": True,
    "application": False,
}
