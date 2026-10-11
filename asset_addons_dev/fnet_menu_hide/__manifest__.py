# -*- coding: utf-8 -*-
{
    "name": "FNet Menu Hide (ir.ui.menu)",
    "version": "19.0.1.0.0",
    "summary": "Hide any Odoo menu, optionally with its entire submenu tree",
    "description": """
Hide Odoo menu items (ir.ui.menu) from a simple configuration screen.

Features
--------
* Pick any menu and hide it with one click.
* "Entire Menu" flag: also hides every descendant (children, grandchildren...).
  e.g. select "Employees" with Entire Menu = True and it hides Employees,
  its Configuration menu, and everything under Configuration.
* Restore puts back exactly what this module hid (nothing else is touched).
* Re-applies automatically on module upgrade, so menus stay hidden after
  upgrading the apps that own them.
* Right-click / Actions shortcut directly from the Menu Items list.
    """,
    "category": "Tools",
    "author": "Your Company",
    "license": "LGPL-3",
    "depends": ["base", "web"],
    "data": [
        "security/ir.model.access.csv",
        "views/fnet_menu_hide_views.xml",
        "data/reapply_on_upgrade.xml",
    ],
    "installable": True,
    "application": True,
}
