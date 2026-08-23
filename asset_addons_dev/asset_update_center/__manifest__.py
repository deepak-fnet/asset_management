# -*- coding: utf-8 -*-
{
    "name": "Asset Update Center & Vulnerability Report",
    "version": "19.0.1.1.0",
    "category": "Administration/Security",
    "summary": "Per-asset update centre, third-party app updates, CVE diagnostics",
    "description": """
Extends asset_patch_vuln with the pieces that were missing:

Application updates
-------------------
Tracks third-party apps that are out of date (Chrome, Firefox, Zoom…) using
winget / apt / brew, which already know what is outdated. Adds per-app
"Update" buttons and a per-machine "Update Everything".

Per-asset Update Center
-----------------------
One tab on the asset form answering "what does this machine need?" — OS
patches and application updates side by side.

Per-asset vulnerability report
------------------------------
Scan a single machine, see its findings and listening ports.

CVE sync diagnostics
--------------------
Step-by-step connectivity test that reports the actual HTTP status, retry with
backoff on NVD rate limits, and an offline JSON import for servers with no
internet access.

Platform-correct OS update endpoint
-----------------------------------
The original /api/asset/updates/report writes to asset.windows.update
regardless of the reporting machine's platform, so Linux agents store Ubuntu
packages as Windows KB numbers. /api/asset/os_updates/report routes by
platform instead.
    """,
    "author": "Axles India Ltd.",
    "license": "LGPL-3",
    "depends": [
        "asset_management",
        "asset_patch_vuln",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/asset_app_update_views.xml",
        "views/asset_cve_diagnostics_views.xml",
        "views/asset_update_center_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
