# -*- coding: utf-8 -*-
"""
Install hooks for the workspace reorganisation.

Why a hook rather than pure XML
-------------------------------
The new menu structure needs to reference actions from modules that may or may
not be installed — helpdesk, ticketing, remote sessions, configuration. An XML
<menuitem action="..."> pointing at a missing action makes the whole install
fail with "External ID not found", which is a poor failure mode for something
optional.

So: the skeleton is XML, and anything uncertain is wired here by looking the
action up and reporting what could not be found instead of raising.

The hook also hides the old top-level menus. It hides rather than deletes, so
uninstalling this module restores the previous navigation exactly.
"""

import logging

_logger = logging.getLogger(__name__)


# Old top-level menus to hide. These are replaced by the new grouped
# structure; hiding is reversible, deleting is not.
LEGACY_MENUS_TO_HIDE = [
    'asset_management.menu_system_overview',
    'asset_management.menu_windows_root',
    'asset_management.menu_ubuntu_root',
    'asset_management.menu_mac_root',
    'asset_management.menu_cctv_root',
    'asset_management.menu_network_root',
    'asset_management.menu_asset_categories',
    'asset_management.menu_repair_management',
    'asset_management.menu_lifecycle_root',
    'asset_management.menu_asset_request_root',
    'asset_management.menu_asset_list_root',
    'asset_helpdesk.menu_asset_helpdesk_main',
    'asset_telemetry.menu_telemetry_snapshots',
    'asset_telemetry_alert.menu_telemetry_alerts_root',
    'asset_management.menu_asset_remote_sessions',
    'asset_helpdesk.menu_asset_helpdesk_configuration',

    # general_asset builds its own top-level "General Assets" entry and its
    # own "Configuration" menu. Left alone, the user sees General Assets
    # twice and Configuration twice, with overlapping contents. This module
    # owns the menu layout, so general_asset's versions are hidden and its
    # items re-wired below into the workspace's own menus.
    'general_asset.menu_asset_general',
    'general_asset.menu_asset_configuration',
]

# (new menu xml_id, [candidate action xml_ids], menu name, parent xml_id, sequence)
# The first candidate that resolves wins. Candidates exist because these
# actions come from modules whose naming I cannot verify without them
# installed.
OPTIONAL_MENUS = [
    (
        'menu_ws_helpdesk_tickets',
        ['asset_helpdesk.action_asset_helpdesk',
         'asset_ticket.action_asset_ticket',
         'asset_helpdesk.action_helpdesk_ticket'],
        'Tickets', 'asset_workspace.menu_ws_helpdesk', 10,
    ),
    (
        'menu_ws_helpdesk_repair',
        ['asset_management.action_repair_management',
         'asset_ticket.action_asset_repair_order'],
        'Repair Management', 'asset_workspace.menu_ws_helpdesk', 20,
    ),
    (
        'menu_ws_config_team',
        ['asset_helpdesk.action_helpdesk_team',
         'asset_ticket.action_asset_ticket_team',
         'asset_helpdesk.action_asset_helpdesk_team'],
        'Teams', 'asset_workspace.menu_ws_config', 10,
    ),
    (
        'menu_ws_config_ticket_type',
        ['asset_helpdesk.action_helpdesk_type',
         'asset_ticket.action_asset_ticket_category',
         'asset_helpdesk.action_asset_helpdesk_type'],
        'Ticket Types', 'asset_workspace.menu_ws_config', 20,
    ),
    (
        'menu_ws_config_category',
        ['asset_management.action_asset_category'],
        'Asset Categories', 'asset_workspace.menu_ws_config', 30,
    ),
    (
        'menu_ws_config_remote',
        ['asset_management.action_remote_session',
         'asset_management.action_asset_remote_session',
         'asset_remote.action_remote_session'],
        'Remote Sessions', 'asset_workspace.menu_ws_config', 40,
    ),

    # ── general_asset movement & disposal ────────────────────────────────
    # This module hides asset_management.menu_lifecycle_root, and
    # general_asset hangs Asset Transfer / Asset Scrap underneath it - so
    # hiding the parent hid these too, and they appeared nowhere. Rewiring
    # them under the workspace's own Lifecycle menu puts them back.
    #
    # Declared here rather than as static menuitems in menus.xml because
    # general_asset is not a dependency: referencing its actions in XML would
    # break installation anywhere it is absent, whereas this list is resolved
    # at runtime and simply skips what it cannot find.
    (
        'menu_ws_lifecycle_transfer',
        ['general_asset.action_asset_transfer'],
        'Asset Transfer', 'asset_workspace.menu_ws_lifecycle', 40,
    ),
    (
        'menu_ws_lifecycle_scrap',
        ['general_asset.action_asset_scrap'],
        'Asset Scrap', 'asset_workspace.menu_ws_lifecycle', 50,
    ),
    (
        # Under Assets, not at the root. The root-level placement was
        # duplicating general_asset's own top-level entry.
        'menu_ws_general_assets',
        ['general_asset.action_asset_general'],
        'General Assets', 'asset_workspace.menu_ws_assets', 40,
    ),
    (
        'menu_ws_physical_verification',
        ['general_asset.action_physical_verification'],
        'Physical Verification', 'asset_workspace.menu_ws_assets', 50,
    ),

    # ── general_asset configuration, merged into the workspace's own ─────
    # Asset Category and Physical Location are deliberately NOT repeated
    # here: menu_ws_config already carries "Asset Categories"
    # (asset_management.action_asset_category) and "Locations"
    # (asset_management.action_asset_location), which are the same two
    # actions general_asset points at. Adding them again is what produced
    # the duplicate pairs.
    (
        'menu_ws_config_sub_category',
        ['general_asset.action_asset_sub_category'],
        'Asset Sub Categories', 'asset_workspace.menu_ws_config', 35,
    ),
    (
        'menu_ws_config_plant',
        ['general_asset.action_plant_master'],
        'Plants', 'asset_workspace.menu_ws_config', 50,
    ),
    (
        'menu_ws_config_prod_categ_map',
        ['general_asset.action_product_category_mapping'],
        'Product Category Mapping', 'asset_workspace.menu_ws_config', 60,
    ),
    (
        'menu_ws_config_asset_purchase_map',
        ['asset_purchase.action_asset_category_mapping'],
        'Asset Purchase Mapping', 'asset_workspace.menu_ws_config', 65,
    ),
    (
        'menu_ws_config_rfid',
        ['general_asset.action_asset_rfid_comparison'],
        'RFID Comparison', 'asset_workspace.menu_ws_config', 70,
    ),
]


def _hide_legacy_menus(env):
    """Set the old top-level menus inactive. Reversible."""
    hidden, missing = [], []
    for xml_id in LEGACY_MENUS_TO_HIDE:
        menu = env.ref(xml_id, raise_if_not_found=False)
        if menu:
            menu.sudo().write({'active': False})
            hidden.append(xml_id)
        else:
            missing.append(xml_id)

    _logger.info('[Workspace] Hid %s legacy menu(s).', len(hidden))
    if missing:
        _logger.info('[Workspace] Not present, skipped: %s', ', '.join(missing))
    return hidden


def _wire_optional_menus(env):
    """Create menus for actions whose XML IDs vary between installs."""
    created, unresolved = [], []

    for menu_xml_id, candidates, name, parent_xml_id, sequence in OPTIONAL_MENUS:
        # Already created by a previous install?
        existing = env.ref(f'asset_workspace.{menu_xml_id}',
                           raise_if_not_found=False)
        if existing:
            continue

        parent = env.ref(parent_xml_id, raise_if_not_found=False)
        if not parent:
            _logger.warning('[Workspace] Parent menu missing: %s',
                            parent_xml_id)
            continue

        action = None
        for candidate in candidates:
            action = env.ref(candidate, raise_if_not_found=False)
            if action:
                break

        if not action:
            unresolved.append((name, candidates))
            continue

        menu = env['ir.ui.menu'].sudo().create({
            'name': name,
            'parent_id': parent.id,
            'sequence': sequence,
            'action': f'{action._name},{action.id}',
        })
        env['ir.model.data'].sudo().create({
            'name': menu_xml_id,
            'module': 'asset_workspace',
            'model': 'ir.ui.menu',
            'res_id': menu.id,
            'noupdate': True,
        })
        created.append(name)

    if created:
        _logger.info('[Workspace] Wired optional menus: %s', ', '.join(created))
    for name, candidates in unresolved:
        _logger.warning(
            '[Workspace] Could not resolve an action for "%s". Tried: %s. '
            'Create the menu manually under Settings > Technical > User '
            'Interface > Menu Items, or add the correct XML ID to '
            'OPTIONAL_MENUS in hooks.py.',
            name, ', '.join(candidates))

    return created, unresolved


def post_init_hook(env):
    """Odoo 17 passes the environment directly."""
    _logger.info('[Workspace] Reorganising menus…')
    _hide_legacy_menus(env)
    created, unresolved = _wire_optional_menus(env)

    if unresolved:
        _logger.warning(
            '[Workspace] %s optional menu(s) could not be wired. The module '
            'is installed and working; those items simply do not appear. See '
            'the warnings above for which actions were not found.',
            len(unresolved))


def rewire_menus(env):
    """Re-run the menu wiring on an ALREADY INSTALLED database.

    post_init_hook only fires on first install, not on upgrade. So adding a
    new entry to OPTIONAL_MENUS - which is exactly what happens when another
    module like general_asset is installed later - has no effect until this
    module is uninstalled and reinstalled, which is not something anyone
    wants to do to a live database.

    Run from the shell after installing a new module:

        env['ir.module.module']._workspace_rewire_menus()
        env.cr.commit()

    It is safe to run repeatedly: _wire_optional_menus() skips anything that
    already exists.
    """
    _logger.info('[Workspace] Re-wiring menus on demand…')
    _hide_legacy_menus(env)
    created, unresolved = _wire_optional_menus(env)
    _logger.info('[Workspace] Re-wire complete. Created: %s. Unresolved: %s.',
                 len(created), len(unresolved))
    return created, unresolved


def uninstall_hook(env):
    """Restore the legacy menus."""
    restored = 0
    for xml_id in LEGACY_MENUS_TO_HIDE:
        menu = env.ref(xml_id, raise_if_not_found=False)
        if menu:
            menu.sudo().write({'active': True})
            restored += 1
    _logger.info('[Workspace] Restored %s legacy menu(s).', restored)