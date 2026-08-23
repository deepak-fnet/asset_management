# -*- coding: utf-8 -*-
"""Convert the legacy free-text `machine_type` into `asset.category` records."""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # Distinct non-empty legacy values that are not yet mapped to a category.
    cr.execute("""
        SELECT DISTINCT btrim(machine_type)
          FROM asset_addition
         WHERE machine_type IS NOT NULL
           AND btrim(machine_type) != ''
           AND category_id IS NULL
    """)
    names = [row[0] for row in cr.fetchall()]
    if not names:
        return

    for name in names:
        cr.execute("SELECT id FROM asset_category WHERE name = %s", (name,))
        row = cr.fetchone()
        if row:
            category_id = row[0]
        else:
            cr.execute("""
                INSERT INTO asset_category (name, active, create_uid, write_uid,
                                            create_date, write_date)
                VALUES (%s, TRUE, 1, 1, now(), now())
                RETURNING id
            """, (name,))
            category_id = cr.fetchone()[0]

        cr.execute("""
            UPDATE asset_addition
               SET category_id = %s
             WHERE category_id IS NULL
               AND btrim(machine_type) = %s
        """, (category_id, name))

    _logger.info("asset: migrated %s legacy machine_type value(s) into asset.category", len(names))
