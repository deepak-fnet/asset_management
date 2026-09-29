"""salvage_value on asset.asset is turning from a plain stored amount into
a compute(store=True) field derived from a new salvage_value_percent field.
Once that happens, the registry's automatic recompute pass overwrites every
existing row's salvage_value using the new field's default (0.5%) - silently
discarding whatever amount was actually typed in before this upgrade (e.g.
a manually entered 5,000.00).

Snapshot the pre-upgrade amount into a throwaway table now, before the
column becomes a compute target, so post-migration can restore it.
"""


def migrate(cr, version):
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'asset_asset' AND column_name = 'salvage_value'
    """)
    if not cr.fetchone():
        return

    cr.execute("""
        CREATE TABLE IF NOT EXISTS x_general_asset_salvage_backup (
            asset_id integer PRIMARY KEY,
            old_salvage_value double precision,
            purchase_cost double precision
        )
    """)
    cr.execute("""
        INSERT INTO x_general_asset_salvage_backup (asset_id, old_salvage_value, purchase_cost)
        SELECT id, salvage_value, purchase_cost
        FROM asset_asset
        WHERE salvage_value IS NOT NULL AND salvage_value != 0
        ON CONFLICT (asset_id) DO NOTHING
    """)
