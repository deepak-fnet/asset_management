def migrate(cr, version):
    """
    Migration: Remove database columns for deleted Asset Governance fields.
    
    Removed columns:
    - asset_condition
    - risk_level
    - compliance_tag
    - barcode
    """
    if not version:
        return
    
    table_name = 'asset_asset'
    fields_to_remove = ['asset_condition', 'risk_level', 'compliance_tag', 'barcode']
    
    try:
        # Drop columns if they exist
        for field_name in fields_to_remove:
            try:
                cr.execute(f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS {field_name}")
            except Exception:
                # Column doesn't exist or already dropped, skip
                pass
        
        # Commit this batch
        cr.commit()
    except Exception as e:
        # If anything fails, rollback to clean state
        cr.rollback()
        # Continue anyway - the views will be recompiled without the fields
