# The rejection history (purchase.approval.rejection) is gone. Its PO form
# extension still references action_view_rejections, which no longer exists,
# and would fail validation while the new views load - drop those views first.
def migrate(cr, version):
    cr.execute("""
        SELECT res_id FROM ir_model_data
         WHERE module = 'purchase_approval' AND model = 'ir.ui.view'
           AND name IN ('purchase_order_form_inh_rejections',
                        'view_purchase_approval_rejection_list',
                        'view_purchase_approval_rejection_form',
                        'view_purchase_approval_reject_wizard_form')
    """)
    ids = [r[0] for r in cr.fetchall()]
    if ids:
        cr.execute("DELETE FROM ir_ui_view WHERE id IN %s", (tuple(ids),))
        cr.execute("DELETE FROM ir_model_data WHERE model = 'ir.ui.view' AND res_id IN %s", (tuple(ids),))
