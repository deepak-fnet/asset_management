"""The old asset_category table is empty of references once the categories
live on product.category (see pre-migrate); drop it."""


def migrate(cr, version):
    cr.execute("""SELECT 1 FROM pg_constraint
                   WHERE contype = 'f' AND confrelid = to_regclass('asset_category')""")
    if not cr.fetchone():
        cr.execute("DROP TABLE IF EXISTS asset_category")
