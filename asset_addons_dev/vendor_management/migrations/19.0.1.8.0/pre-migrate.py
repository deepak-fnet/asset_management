"""The advance views that mention the removed advance_percent field are
dropped first (they are recreated from the module's XML), otherwise the
purchase order view fails to validate during the upgrade."""


def migrate(cr, version):
    for name in ('view_payment_term_form_inherit_advance', 'purchase_order_form_inherit_advance'):
        cr.execute("""
            DELETE FROM ir_ui_view WHERE id IN (
                SELECT res_id FROM ir_model_data
                 WHERE module = 'vendor_management' AND model = 'ir.ui.view' AND name = %s)
        """, (name,))
        cr.execute("""DELETE FROM ir_model_data
                       WHERE module = 'vendor_management' AND model = 'ir.ui.view' AND name = %s""", (name,))
