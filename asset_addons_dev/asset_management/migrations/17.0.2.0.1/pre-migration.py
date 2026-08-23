def migrate(cr, version):
    """
    Pre-migration: Clean up stale inherited view definitions that reference removed fields.

    This runs BEFORE data files are loaded, preventing view validation errors such as:
      "action_open_repairs is not a valid action on asset.asset"

    The inherited view 'view_asset_asset_form_inherit_extra' may exist in the database
    with old content (repair smart button, repair_ids page) from a previous module version.
    Deleting it here allows the upgrade to recreate it cleanly from the current XML file.
    """
    if not version:
        return

    try:
        # Find and delete the stale inherited view that references removed repair fields.
        # The view will be recreated with correct content when asset_asset_extended_views.xml
        # is processed during the upgrade.
        cr.execute("""
            DELETE FROM ir_model_data
            WHERE model = 'ir.ui.view'
              AND module = 'asset_management'
              AND name = 'view_asset_asset_form_inherit_extra'
        """)

        cr.execute("""
            DELETE FROM ir_ui_view
            WHERE model = 'asset.asset'
              AND name = 'asset.asset.form.inherit.extra'
              AND arch_db::text LIKE '%action_open_repairs%'
        """)

        cr.commit()
    except Exception:
        try:
            cr.rollback()
        except Exception:
            pass
