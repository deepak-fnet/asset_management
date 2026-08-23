# -*- coding: utf-8 -*-
"""Odoo 17 -> 19 pre-migration for asset_management.

Odoo core's own upgrade scripts handle the res.groups -> res.groups.privilege
restructure and the ir.rule.groups -> ir.rule.group_ids rename. This script
only cleans up module-owned records that core cannot know about:

  * stale ir.ui.view rows whose arch still contains <tree>, which would raise
    during the module update before the new arch is loaded;
  * ir.actions.act_window.view_mode values still holding 'tree'.

Both are idempotent and safe to re-run.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # ------------------------------------------------------------------
    # 1. act_window.view_mode: 'tree' -> 'list'
    # ------------------------------------------------------------------
    cr.execute("""
        UPDATE ir_act_window a
           SET view_mode = regexp_replace(a.view_mode, '\\mtree\\M', 'list', 'g')
          FROM ir_model_data d
         WHERE d.model = 'ir.actions.act_window'
           AND d.res_id = a.id
           AND d.module = 'asset_management'
           AND a.view_mode ~ '\\mtree\\M'
    """)
    _logger.info("asset_management: normalised view_mode on %s action(s)", cr.rowcount)

    # ------------------------------------------------------------------
    # 2. Drop module-owned view arch still using <tree>. The updated
    #    definitions are reloaded from the XML during this same upgrade.
    # ------------------------------------------------------------------
    cr.execute("""
        DELETE FROM ir_ui_view v
         USING ir_model_data d
         WHERE d.model = 'ir.ui.view'
           AND d.res_id = v.id
           AND d.module = 'asset_management'
           AND v.arch_db::text LIKE '%<tree%'
    """)
    _logger.info("asset_management: dropped %s stale <tree> view(s)", cr.rowcount)
