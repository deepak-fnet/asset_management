# -*- coding: utf-8 -*-
{
    "name": "Asset Security Console",
    "version": "19.0.1.0.1",
    "category": "Administration/Security",
    "summary": "Asset-wise patch and vulnerability status, setup checker, cleanup",
    "description": """
v1.0.1 — bug fix release
========================
Fixes: ValueError: Cannot convert field asset.asset.security_status to SQL

security_status and the counter fields were computed with store=False, but the
console groups and filters on them, which requires a real database column.
They are now stored, with three refresh mechanisms: write triggers on the
source models, a six-hourly cron, and a manual Refresh action.

Also adds a cleanup wizard for patch records filed under the wrong platform,
which previews exactly what it will delete before deleting anything.
    """,
    "author": "Axles India Ltd.",
    "license": "LGPL-3",
    "depends": [
        "asset_management",
        "asset_patch_vuln",
        "asset_update_center",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/cron_data.xml",
        "views/asset_security_console_views.xml",
        "views/asset_setup_check_views.xml",
        "views/asset_patch_cleanup_views.xml",
        "views/asset_demo_seeder_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
