from odoo import models, fields, api


class PurchaseQtyExceedWizard(models.TransientModel):
    _name = 'purchase.qty.exceed'
    _description = 'Qty Exceed Wizard'

    name = fields.Text(string='Title', default='Quantity Exceeded', required=True)
    message = fields.Html(string='Message', readonly=True,
                          sanitize=False)
    purchase_order_id = fields.Many2one('purchase.order', string='Purchase Order')

    def action_confirm(self):
        if self.purchase_order_id:
            order = self.purchase_order_id
            order.is_check_over_qty = True
            self.purchase_order_id.button_confirm()

    def action_cancel(self):
        return {'type': 'ir.actions.act_window_close'}