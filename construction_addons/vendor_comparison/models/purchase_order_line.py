from odoo import models, fields, api
from odoo.exceptions import ValidationError


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    remaining_qty = fields.Float(string="Remaining Qty", compute="_compute_remaining_qty", )

    # @api.model_create_multi
    # def create(self, vals_list):
    #     for vals in vals_list:
    #         order_id = vals.get('order_id')
    #         if order_id:
    #             order = self.env['purchase.order'].browse(order_id)
    #             if order.state in ['sent', 'partial_po']:
    #                 raise ValidationError(
    #                     "You cannot add new products when the RFQ is Sent or in Partial PO state."
    #                 )
    #             if order.vendor_comparison_id:
    #                 raise ValidationError(
    #                     "You cannot add new products when the RFQ is Created from the vendor comparison."
    #                 )
    #     return super().create(vals_list)
    #
    # def unlink(self):
    #     for line in self:
    #         if line.order_id.state != 'draft':
    #             raise ValidationError(
    #                 "You cannot delete products after RFQ is sent or confirmed."
    #             )
    #         if line.order_id.vendor_comparison_id:
    #             raise ValidationError(
    #                 "You cannot delete products when the RFQ is Created from the vendor comparison."
    #             )
    #     return super().unlink()

    @api.depends('order_id.vendor_comparison_id', 'product_id', 'product_qty',
                 'order_id.state')
    def _compute_remaining_qty(self):
        for line in self:
            vendor_comparison = line.order_id.vendor_comparison_id
            if not vendor_comparison:
                line.remaining_qty = 0.0
                continue
            comp_line = vendor_comparison.line_ids.filtered(
                lambda l: l.product_id == line.product_id
            )
            if not comp_line:
                line.remaining_qty = 0.0
                continue
            allowed = comp_line[:1].product_qty
            get_purchase = self.env['purchase.order'].search([
                ('vendor_comparison_id', '=', vendor_comparison.id),
            ])
            rfq_pos_with_id = get_purchase.filtered(lambda p: p.rfq_id)

            if rfq_pos_with_id:
                all_related_pos = self.env['purchase.order'].search([
                    ('rfq_id', 'in', rfq_pos_with_id.ids),
                    ('state', 'in', ['purchase']),
                ])
                if line.order_id in all_related_pos:
                    line.remaining_qty = 0.0
                    continue

                matched_lines = all_related_pos.mapped('order_line').filtered(
                    lambda l: l.product_id == line.product_id
                )
                total_confirmed = sum(matched_lines.mapped('product_qty'))

            else:
                related_pos = self.env['purchase.order'].search([
                    ('vendor_comparison_id', '=', vendor_comparison.id),
                    ('state', 'in', ['purchase']),
                ])
                if line.order_id in related_pos:
                    line.remaining_qty = 0.0
                    continue
                matched_lines = related_pos.mapped('order_line').filtered(
                    lambda l: l.product_id == line.product_id
                )
                total_confirmed = sum(matched_lines.mapped('partial_received_qty'))
            line.remaining_qty = allowed - total_confirmed

    @api.constrains('product_qty')
    def _check_remaining_qty(self):
        # This check only makes sense for orders raised through a vendor comparison,
        # against the quantity that comparison allowed for this product - it must not
        # run (or crash) for ordinary purchase orders with no comparison behind them.
        for rec in self:
            order = rec.order_id
            if not order.vendor_comparison_id or order.rfq_id:
                continue
            all_related_pos = self.env['purchase.order'].search([
                ('rfq_id', '=', order.id),
                ('state', '=', 'purchase'),
            ])
            if not all_related_pos:
                continue
            comp_line = order.vendor_comparison_id.line_ids.filtered(
                lambda l: l.product_id == rec.product_id
            )
            if not comp_line:
                continue
            new_qty = rec.product_qty or 0.0
            allowed_qty = comp_line[:1].product_qty
            matched_lines = all_related_pos.mapped('order_line').filtered(
                lambda l: l.product_id.id == rec.product_id.id
            )
            total_confirmed = sum(matched_lines.mapped('product_qty'))
            remaining_qty = allowed_qty - total_confirmed
            if remaining_qty < new_qty:
                raise ValidationError(
                    (
                        "The requested quantity exceeds the remaining allowable quantity.\n\n"
                        "Remaining: %s\n"
                        "Requested: %s\n\n"
                        "Please adjust the quantity."
                    ) % (remaining_qty, new_qty)
                )

