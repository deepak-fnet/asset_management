# -*- coding: utf-8 -*-
{
    "name": "Asset Telemetry Alerts",
    "version": "19.0.1.0.0",
    "category": "Administration",
    "summary": "Email alerts on any value inside the agent telemetry JSON",
    "description": """
Telemetry alert rules
=====================
custom_asset_alert_mail watches real Odoo fields, which means every value you
want to alert on must first become a column, and MONITORED_FIELDS has to be
edited and the module upgraded.

This works against the telemetry JSON instead, so anything the agent collects
is alertable with no schema change:

    memory.virtual.percent      >  90
    disks.mounts[*].percent     >  85    (any mount)
    cpu.load_average.5min       >  4
    services.failed_count       >  0
    hardware.battery.percent    <  60
    packages.reboot_required    == True

Includes a path browser that reads a real payload, so rules are written
against paths that actually exist rather than guessed.

Noise control
-------------
Telemetry arrives every 15 minutes. Alerts are modelled as a STATE, not an
event: one mail when a condition starts, an optional one when it clears, and
a configurable cooldown in between. Without that, one machine at 91% disk
would mail someone 96 times a day.
    """,
    "author": "Axles India Ltd.",
    "license": "LGPL-3",
    "depends": ["asset_management", "asset_telemetry", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "data/cron_data.xml",
        "data/sample_rules.xml",
        "views/telemetry_alert_rule_views.xml",
        "views/telemetry_alert_log_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
}
