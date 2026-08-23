# -*- coding: utf-8 -*-
{
    "name": "Mobile Device Management",
    "version": "19.0.1.0.0",
    "category": "Administration",
    "summary": "Enrol and monitor Android and iOS devices alongside laptops and VMs",
    "description": """
Mobile Device Management
========================

Extends the asset fleet to phones and tablets, using the same enrol → token →
heartbeat pattern as the desktop agents.

What the agent reports
----------------------
* Hardware: manufacturer, model, serial, IMEI (where policy permits), storage,
  RAM, battery
* OS: version, build, security patch level
* Installed applications, with version and system/user classification
* Location, when the user has granted permission
* Network: carrier, connection type, IP

How OS update detection works
-----------------------------
Mobile platforms do not let an ordinary app ask "is there an update?" in a way
that works across vendors. Android exposes it only to Device Owner apps; iOS
exposes it only through MDM.

So the agent reports what it *can* see - OS version and security patch level -
and the server compares that against the OS release catalogue
(mobile.os.release) to decide whether the device is behind. This works on every
vendor without privileged enrolment, and the same records are populated by real
MDM later without changing the model.

Enrolment
---------
An enrolment token is generated in Odoo and shown as a QR code. The agent scans
it, calls /api/mdm/enrol once, and receives a per-device token used for all
later calls. Tokens can be revoked, which blocks the device immediately.
    """,
    "author": "Axles India Ltd.",
    "license": "LGPL-3",
    "depends": [
        "base",
        "mail",
        "hr",
        "asset_management",
    ],
    "data": [
        "security/mdm_security.xml",
        "security/ir.model.access.csv",
        "data/mdm_sequence.xml",
        "data/mdm_cron.xml",
        "data/mobile_os_release_data.xml",
        "views/mobile_device_views.xml",
        "views/mobile_application_views.xml",
        "views/mobile_os_update_views.xml",
        "views/mobile_location_views.xml",
        "views/mobile_command_views.xml",
        "views/mobile_enrollment_views.xml",
        "views/mobile_os_release_views.xml",
        "views/mobile_storage_volume_views.xml",
        "views/mobile_metric_views.xml",
        "views/mobile_app_change_views.xml",
        "views/mdm_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "mobile_device_management/static/src/css/mdm_dashboard.css",
        ],
    },
    "installable": True,
    "application": True,
}
