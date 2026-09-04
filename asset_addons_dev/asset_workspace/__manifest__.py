# -*- coding: utf-8 -*-
{
    "name": "Asset Workspace",
    "version": "19.0.1.0.0",
    "category": "Administration",
    "summary": "Unified dashboard and reorganised navigation",
    "description": """
Asset Workspace
===============

Two things.

1. A single dashboard
---------------------
One entry point with a platform filter. Picking a platform reveals its views —
Dashboard, Assets, Live Monitoring, Antivirus, App Deployment — and opens the
existing screen for it.

The default state is a consolidated fleet summary: totals by platform,
online/offline split, and the handful of cross-platform numbers that matter
(pending patches, failed patches, app updates, critical CVEs, active alerts,
compliance).

It does NOT re-implement the fifteen existing dashboards. They work; rebuilding
their rendering would mean every future change had to be made twice. This is a
router with a summary on the front.

2. Reorganised navigation
-------------------------
    Dashboard
    Lifecycle Process  Joining / Assign-Replace / Exit / Asset Transfer / Asset Scrap
    Help Desk        Tickets / Repair Management
    Assets           Windows / Linux / macOS / CCTV / Network
    Asset Request    Requests / Purchase Orders / Asset List
    Purchase > Asset Requests   Approved requests only, for the buyer
    Security         (unchanged)
    Agent Telemetry  Telemetry Data / Active Alerts / Alert Rules
    Configuration    Teams / Ticket Types / Categories / Remote Sessions

Old top-level menus are HIDDEN, not deleted. Uninstalling restores the
previous navigation exactly.

Menus whose actions come from modules with unverifiable XML IDs are wired by a
post-install hook that reports what it could not find rather than failing the
install.
    """,
    "author": "Axles India Ltd.",
    "license": "LGPL-3",
    "depends": [
        "asset_management",
        "asset_telemetry",
        "asset_telemetry_alert",
    ],
    "data": [
        "views/hub_dashboard_views.xml",
        "views/menus.xml",
        "views/telemetry_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "asset_workspace/static/src/css/hub_dashboard.css",
            "asset_workspace/static/src/xml/hub_dashboard.xml",
            "asset_workspace/static/src/js/hub_dashboard.js",
            "asset_workspace/static/src/css/all_asset_dashboard.css",
            "asset_workspace/static/src/xml/all_asset_dashboard.xml",
            "asset_workspace/static/src/js/all_asset_dashboard.js",
        ],
    },
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": False,
}
