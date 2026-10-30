"""Asset categories become product categories.

Every asset.category row is merged into the product.category of the same
name (created under the root category when there is none), its settings are
copied onto that product category, and every column pointing at
asset_category is repointed. The old foreign keys are dropped here; the ORM
adds the new ones to product_category when the modules load. The emptied
asset.category model is removed by the module update itself."""
import logging

_logger = logging.getLogger(__name__)

# Plain stored fields of the old model that now live on product.category.
# asset_count (stored compute) is left for the ORM to create and compute.
COLUMNS = [
    ('code', 'varchar'), ('description', 'text'), ('color', 'integer'), ('icon', 'varchar'),
    ('is_peripheral', 'boolean'), ('sequence_id', 'integer'),
    ('is_general', 'boolean'), ('is_it_asset', 'boolean'), ('is_network_asset', 'boolean'),
    ('is_cctv_asset', 'boolean'), ('is_dynamic_form', 'boolean'),
    ('depreciation_method', 'varchar'), ('useful_life_years', 'integer'),
    ('depreciation_rate', 'double precision'), ('salvage_value_percent', 'double precision'),
]


def _xmlid_res_id(cr, module, name):
    cr.execute("SELECT res_id FROM ir_model_data WHERE module = %s AND name = %s", (module, name))
    row = cr.fetchone()
    return row[0] if row else None


def migrate(cr, version):
    cr.execute("SELECT to_regclass('asset_category')")
    if not cr.fetchone()[0]:
        return

    # 1. Columns on product_category, so the values can be copied now.
    for col, typ in COLUMNS:
        cr.execute(f'ALTER TABLE product_category ADD COLUMN IF NOT EXISTS "{col}" {typ}')
    cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'asset_category'")
    old_cols = {r[0] for r in cr.fetchall()}
    copy_cols = [c for c, _t in COLUMNS if c in old_cols]

    # 2. Old -> new id map: same name (case-insensitive), else a new category.
    parent = (_xmlid_res_id(cr, 'product', 'product_category_goods')
              or _xmlid_res_id(cr, 'product', 'product_category_all'))
    cr.execute("CREATE TEMP TABLE _asset_categ_map (old_id integer PRIMARY KEY, new_id integer NOT NULL)")
    cr.execute("SELECT id, name, create_uid, create_date FROM asset_category ORDER BY id")
    for old_id, name, cuid, cdate in cr.fetchall():
        cr.execute("""SELECT id FROM product_category WHERE lower(trim(name)) = lower(trim(%s))
                      ORDER BY id LIMIT 1""", (name,))
        row = cr.fetchone()
        if row:
            new_id = row[0]
        else:
            cr.execute("""INSERT INTO product_category (name, parent_id, create_uid, create_date, write_uid, write_date)
                          VALUES (%s, %s, %s, %s, %s, now() at time zone 'UTC') RETURNING id""",
                       (name, parent, cuid, cdate, cuid))
            new_id = cr.fetchone()[0]
            if parent:
                cr.execute("""UPDATE product_category c
                                 SET parent_path = p.parent_path || c.id || '/',
                                     complete_name = p.complete_name || ' / ' || c.name
                                FROM product_category p WHERE p.id = %s AND c.id = %s""", (parent, new_id))
            else:
                cr.execute("UPDATE product_category SET parent_path = id || '/', complete_name = name WHERE id = %s",
                           (new_id,))
        cr.execute("INSERT INTO _asset_categ_map VALUES (%s, %s)", (old_id, new_id))
        if copy_cols:
            sets = ", ".join(f'"{c}" = a."{c}"' for c in copy_cols)
            cr.execute(f"UPDATE product_category pc SET {sets} FROM asset_category a "
                       f"WHERE a.id = %s AND pc.id = %s", (old_id, new_id))

    # Defaults the ORM would have filled for the other product categories.
    cr.execute("""UPDATE product_category SET
                      color = COALESCE(color, 1),
                      depreciation_method = COALESCE(depreciation_method, 'slm'),
                      salvage_value_percent = COALESCE(salvage_value_percent, 0.5)""")

    # 3. Repoint every foreign key that targets asset_category.
    cr.execute("""SELECT c.conname, c.conrelid::regclass::text, a.attname
                    FROM pg_constraint c
                    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
                   WHERE c.contype = 'f' AND c.confrelid = 'asset_category'::regclass""")
    for conname, table, column in cr.fetchall():
        cr.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{conname}"')
        cr.execute(f'UPDATE {table} t SET "{column}" = m.new_id FROM _asset_categ_map m '
                   f'WHERE t."{column}" = m.old_id')
        _logger.info("asset.category -> product.category: %s.%s repointed (%s rows)", table, column, cr.rowcount)

    # 4. XML ids of the predefined categories, and the category images.
    cr.execute("""UPDATE ir_model_data d SET model = 'product.category', res_id = m.new_id
                    FROM _asset_categ_map m WHERE d.model = 'asset.category' AND d.res_id = m.old_id""")
    cr.execute("""UPDATE ir_attachment t SET res_model = 'product.category', res_id = m.new_id
                    FROM _asset_categ_map m WHERE t.res_model = 'asset.category' AND t.res_id = m.old_id""")

    cr.execute("SELECT count(*), count(DISTINCT new_id) FROM _asset_categ_map")
    _logger.info("asset.category -> product.category: %s categories merged into %s", *cr.fetchone())
    cr.execute("DROP TABLE _asset_categ_map")
