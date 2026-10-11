# FNet Menu Hide (Odoo 19)

Hide any Odoo menu (`ir.ui.menu`), optionally together with its entire
submenu tree.

## Install
1. Copy `fnet_menu_hide` into your addons path.
2. Restart Odoo, enable Developer Mode.
3. Apps > Update Apps List > search "FNet Menu Hide" > Install.

## Use
Open **Menu Visibility > Hide Menus** > **New**:

* **Menu to Hide** — pick any menu, e.g. `Employees`.
* **Entire Menu**
  * `True`  -> hides `Employees`, its `Configuration` menu, and everything
    under `Configuration`, at any depth.
  * `False` -> hides only that single menu item.
* Save. The rule applies immediately (creating a rule auto-applies it).

Refresh the browser (Ctrl+Shift+R) to see menus disappear.

### Restore
Open the rule and click **Restore (Show)**, or simply delete the rule.
Restore re-enables *exactly* the menus this rule hid, tracked in
`hidden_menu_ids` — menus that were already archived for other reasons are
never touched.

### Shortcut from the menu list
Settings > Technical > User Interface > **Menu Items**, tick one or more rows,
then **Actions**:
* *Hide menu (entire tree)*
* *Hide menu (this item only)*

### Survives upgrades
Upgrading the app that owns a menu can reset its `active` flag. This module
re-applies all rules on every upgrade (`data/reapply_on_upgrade.xml`).

## Scope
Hiding sets `ir.ui.menu.active = False`, so the menu is hidden for **all
users**. If you need per-group visibility instead, restrict the menu's
**Groups** field rather than archiving it.
