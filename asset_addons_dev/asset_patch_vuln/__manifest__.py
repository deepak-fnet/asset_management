# -*- coding: utf-8 -*-
{
    "name": "Asset Patch & Vulnerability Management",
    "version": "19.0.1.0.0",
    "category": "Administration/Security",
    "summary": "Fleet patch compliance and CVE vulnerability correlation",
    "description": """
Patch Management
================
* Unified patch view across Windows, Linux and macOS
* Named deployment batches with per-target tracking
* Fleet compliance percentage with daily history for trending
* Dashboard: missing by severity, by platform, compliance over time

Vulnerability Management
========================
* Local CVE cache synced from the NVD API
* Correlates agent-reported software inventory against CVE data
* Confidence-scored findings — low-confidence matches queue for human review
  rather than being asserted as fact
* Dashboard: severity breakdown, trend across scans, top vulnerable assets

Note: vulnerability scanning is database-side correlation, not a network
probe. Nothing is sent to or executed on the endpoint.
    """,
    "author": "Axles India Ltd.",
    "license": "LGPL-3",
    "depends": [
        "base",
        "mail",
        "asset_management",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/sequence_data.xml",
        "data/cron_data.xml",
        "views/asset_patch_deployment_views.xml",
        "views/asset_cve_views.xml",
        "views/asset_vulnerability_views.xml",
        "views/asset_patch_dashboard_views.xml",
        "views/asset_vulnerability_dashboard_views.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "asset_patch_vuln/static/src/css/patch_vuln_dashboard.css",
            "asset_patch_vuln/static/src/xml/patch_dashboard.xml",
            "asset_patch_vuln/static/src/js/patch_dashboard.js",
            "asset_patch_vuln/static/src/xml/vulnerability_dashboard.xml",
            "asset_patch_vuln/static/src/js/vulnerability_dashboard.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
