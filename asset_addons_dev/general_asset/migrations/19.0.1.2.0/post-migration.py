"""Restore the pre-upgrade salvage_value amount snapshotted in
pre-migration.py, and back-derive salvage_value_percent from it so the
depreciation schedule (which reads salvage_value directly) keeps producing
the exact same numbers it did before this upgrade - only the input field
users edit going forward has changed, not any existing asset's figures.

Directly SQL-restores both columns rather than relying on the ORM to
recompute salvage_value from the new percent field - the mass recompute the
registry already ran during this same upgrade (using the new field's 0.5%
default) does not get automatically retriggered by this script updating
salvage_value_percent afterwards, so salvage_value would otherwise stay
wrong until something else next touches each record.
"""


def migrate(cr, version):
    cr.execute("""
        SELECT to_regclass('x_general_asset_salvage_backup')
    """)
    if not cr.fetchone()[0]:
        return

    cr.execute("""
        UPDATE asset_asset a
        SET salvage_value = b.old_salvage_value,
            salvage_value_percent = CASE
                WHEN b.purchase_cost > 0
                THEN (b.old_salvage_value / b.purchase_cost) * 100
                ELSE a.salvage_value_percent
            END
        FROM x_general_asset_salvage_backup b
        WHERE b.asset_id = a.id
    """)

    cr.execute("DROP TABLE x_general_asset_salvage_backup")
