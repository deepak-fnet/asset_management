# -*- coding: utf-8 -*-
"""Backfill order_line.vendor_id for orders finalized under the old,
whole-PO single-winner flow.

Before this version, vendor_finalized was a manually-set flag meaning "the
single PO-wide winning vendor (partner_id) has been chosen." It is now a
computed field: True iff every order line already has its own vendor_id set
(different lines can go to different vendors). Only draft/sent orders need
backfilling here - anything already confirmed generated its (single-vendor)
picking/moves under the old paradigm already and is never revisited by the
new per-vendor picking/billing logic.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        UPDATE purchase_order_line l
           SET vendor_id = o.partner_id
          FROM purchase_order o
         WHERE l.order_id = o.id
           AND o.vendor_finalized IS TRUE
           AND o.state IN ('draft', 'sent')
           AND l.display_type IS NULL
           AND l.vendor_id IS NULL
    """)
    _logger.info(
        "vendor_management: backfilled order_line.vendor_id on %s line(s) "
        "from old-flow vendor_finalized orders", cr.rowcount)
