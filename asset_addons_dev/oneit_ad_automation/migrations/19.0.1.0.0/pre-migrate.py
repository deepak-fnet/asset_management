# -*- coding: utf-8 -*-
"""Pre-migration for the Odoo 14 -> 19 upgrade.

Only relevant when upgrading a database that already ran the Odoo 14
version of this module. A fresh Odoo 19 install skips this entirely
(version is falsy on first install).
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # The name-uniqueness constraint was replaced by DN-uniqueness back in
    # 14.0.1.1.0. Kept here so a database that skipped that intermediate
    # version still lands in the right place.
    cr.execute("""
        ALTER TABLE oneit_ad_group
        DROP CONSTRAINT IF EXISTS oneit_ad_group_name_uniq
    """)

    cr.execute("""
        SELECT distinguished_name, count(*)
        FROM oneit_ad_group
        WHERE distinguished_name IS NOT NULL
        GROUP BY distinguished_name
        HAVING count(*) > 1
    """)
    duplicates = cr.fetchall()
    if duplicates:
        _logger.warning(
            "Duplicate distinguished names block the unique constraint: %s",
            ', '.join('%s (x%s)' % row for row in duplicates))

    _logger.info("OneIT pre-migration to 19.0 complete")
