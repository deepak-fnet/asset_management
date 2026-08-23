from odoo import api, fields, models
from odoo.exceptions import UserError,ValidationError

class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    vendor_comparison_id = fields.Many2one('vendor.comparison')
    is_over_qty = fields.Boolean(string="Over Quantity", compute="_compute_is_over_qty")

    @api.depends('vendor_comparison_id', 'order_line.product_qty',
                 'order_line.product_id', 'state')
    def _compute_is_over_qty(self):
        for order in self:
            order.is_over_qty = False
            if not order.vendor_comparison_id:
                continue
            rfq_pos = self.env['purchase.order'].search([
                ('vendor_comparison_id', '=', order.vendor_comparison_id.id),
            ])
            if not rfq_pos:
                continue
            rfq_pos_with_id = rfq_pos.filtered(lambda p: p.rfq_id)
            if rfq_pos_with_id:
                confirmed_pos = self.env['purchase.order'].search([
                    ('rfq_id', 'in', rfq_pos_with_id.ids),
                    ('state', 'in', ['purchase']),
                ])
                for line in order.order_line:

                    comp_line = order.vendor_comparison_id.line_ids.filtered(
                        lambda l: l.product_id == line.product_id
                    )
                    if not comp_line:
                        continue

                    allowed = comp_line[:1].product_qty
                    product = line.product_id
                    matched_lines = confirmed_pos.mapped('order_line').filtered(
                        lambda l: l.product_id == product
                    )
                    ordered = sum(matched_lines.mapped('product_qty'))
                    remaining = allowed - ordered
                    if line.product_qty > remaining:
                        order.is_over_qty = True
                        break
                    else:
                        order.is_over_qty = False

            else:
                related_pos = self.env['purchase.order'].search([
                    ('vendor_comparison_id', '=', order.vendor_comparison_id.id),
                    ('state', 'in', ['partial_po', 'purchase']),
                ])

                for line in order.order_line:
                    comp_line = order.vendor_comparison_id.line_ids.filtered(
                        lambda l: l.product_id == line.product_id
                    )
                    if not comp_line:
                        continue

                    allowed = comp_line[:1].product_qty

                    matched_lines = related_pos.mapped('order_line').filtered(
                        lambda l: l.product_id == line.product_id
                    )
                    ordered = sum(matched_lines.mapped('partial_received_qty'))

                    remaining = allowed - ordered
                    if line.product_qty > remaining:
                        order.is_over_qty = True
                        break
                    else:
                        order.is_over_qty = False


    def action_view_vendor_comparison(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Vendor Comparison',
            'res_model': 'vendor.comparison',
            'view_mode': 'form',
            'res_id': self.vendor_comparison_id.id,
        }

    def action_check_product_quantity_limit(self):
        Wizard = self.env['purchase.qty.exceed']

        for order in self:
            vendor_comparison = order.vendor_comparison_id
            if not vendor_comparison:
                continue

            rfq_pos = self.env['purchase.order'].search([
                ('vendor_comparison_id', '=', vendor_comparison.id),
            ])
            if not rfq_pos:
                continue

            # Check if rfq_id is available on any related PO
            rfq_pos_with_id = rfq_pos.filtered(lambda p: p.rfq_id)

            if rfq_pos_with_id:
                # Case 1: rfq_id available → search confirmed POs via rfq_id
                confirmed_pos = self.env['purchase.order'].search([
                    ('rfq_id', 'in', rfq_pos_with_id.ids),
                    ('state', 'in', ['purchase']),
                ])
                all_related_lines = confirmed_pos.mapped('order_line')
                qty_field = 'product_qty'

            else:
                # Case 2: rfq_id not available → fallback to partial_po/purchase state
                related_pos = self.env['purchase.order'].search([
                    ('vendor_comparison_id', '=', vendor_comparison.id),
                    ('state', 'in', ['partial_po', 'purchase']),
                ])
                all_related_lines = related_pos.mapped('order_line')
                qty_field = 'partial_received_qty'

            products_with_pos = []
            for line in order.order_line:
                product = line.product_id
                related_product_lines = all_related_lines.filtered(
                    lambda l: l.product_id == product
                )

                if related_product_lines:
                    ordered = sum(related_product_lines.mapped(qty_field))
                    products_with_pos.append(f"{product.name} ({int(ordered)} units)")

            if products_with_pos:
                product_list = "<br/>".join([f"• {p}" for p in products_with_pos])

                full_message = f"""
                    <div style="font-family: Arial, sans-serif; font-size: 14px; color: #333;">
                        <p style="margin-bottom: 12px;">
                            The following products already have confirmed Purchase Orders:
                        </p>
                        <div style="margin: 16px 0; padding: 12px; background-color: #fff3cd; border-left: 4px solid #ffc107;">
                            {product_list}
                        </div>
                        <p style="margin-top: 16px; font-weight: bold; color: #d9534f;">
                            ⚠️ Are you sure you want to continue with this purchase?
                        </p>
                    </div>
                """

                wizard = Wizard.create({
                    'name': 'Confirm Purchase',
                    'message': full_message,
                    'purchase_order_id': order.id,
                })
                return {
                    'name': 'Confirm Purchase',
                    'type': 'ir.actions.act_window',
                    'res_model': 'purchase.qty.exceed',
                    'view_mode': 'form',
                    'res_id': wizard.id,
                    'target': 'new',
                }

        return True


