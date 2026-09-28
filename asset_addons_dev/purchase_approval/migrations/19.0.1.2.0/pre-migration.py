# -*- coding: utf-8 -*-
"""payment_terms_id (Many2one to purchase.terms.condition.value) is being
removed from purchase.order in this version - replaced by relabeling the
existing terms_template_id field to "Payment Terms" instead. Odoo's
incremental view-diff during module update does not reliably resolve a
field-removal-plus-view-content-change in the same upgrade pass (same
pre-existing issue asset_management's own 19.0.1.0.0 migration works around,
see that file). Dropping the stale view row here lets it reload cleanly from
the updated XML instead of failing validation against a field that no longer
exists on the model.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        DELETE FROM ir_ui_view v
         USING ir_model_data d
         WHERE d.model = 'ir.ui.view'
           AND d.res_id = v.id
           AND d.module = 'purchase_approval'
           AND d.name = 'purchase_order_form_inh_terms'
    """)
    _logger.info("purchase_approval: dropped %s stale terms view(s) referencing "
                 "the removed payment_terms_id field", cr.rowcount)
