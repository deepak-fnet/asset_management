from odoo import api, models
from odoo.exceptions import ValidationError

from .np_mixin import check_not_past, check_order, check_locked_lines

LOCKED_STATES = ('sale', 'cancel')


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.constrains('date_order', 'commitment_date', 'validity_date')
    def _np_check_dates(self):
        for order in self:
            if order.state in ('draft', 'sent'):
                check_not_past(order, 'date_order', "Order Date")
                check_not_past(order, 'validity_date', "Expiration")
            check_order(order, 'date_order', 'commitment_date',
                        "Order Date", "Delivery Date")

    def action_confirm(self):
        for order in self:
            if not order.order_line.filtered(lambda line: not line.display_type):
                raise ValidationError(
                    "%s cannot be confirmed without any product line."
                    % order.display_name)
            check_not_past(order, 'date_order', "Order Date")
        return super().action_confirm()

    def unlink(self):
        for order in self:
            if order.state == 'sale':
                raise ValidationError(
                    "%s is confirmed and cannot be deleted. Cancel it instead."
                    % order.display_name)
        return super().unlink()


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    _np_parent_field = 'order_id'

    @api.constrains('product_uom_qty', 'price_unit', 'discount')
    def _np_check_values(self):
        for line in self:
            if line.display_type or line.is_downpayment:
                continue
            if line.product_uom_id.compare(line.product_uom_qty, 0) <= 0:
                raise ValidationError(
                    "Quantity must be strictly positive on line '%s'."
                    % line.product_id.display_name)
            if line.currency_id.compare_amounts(line.price_unit, 0) < 0:
                raise ValidationError(
                    "Unit price cannot be negative on line '%s'."
                    % line.product_id.display_name)
            if not 0 <= line.discount <= 100:
                raise ValidationError(
                    "Discount must stay between 0 and 100 on line '%s'."
                    % line.product_id.display_name)

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if not self.env.context.get('np_bypass_lock'):
            check_locked_lines(
                lines.filtered(lambda line: not line.is_downpayment),
                LOCKED_STATES, doc_label="Sales order", operation="added")
        return lines

    def write(self, vals):
        watched = {'product_id', 'product_uom_qty', 'price_unit', 'product_uom_id',
                   'tax_ids', 'discount'}
        if watched & set(vals) and not self.env.context.get('np_bypass_lock'):
            check_locked_lines(
                self.filtered(lambda line: not line.is_downpayment),
                LOCKED_STATES, doc_label="Sales order", operation="modified")
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get('np_bypass_lock'):
            check_locked_lines(self, LOCKED_STATES,
                               doc_label="Sales order", operation="deleted")
        return super().unlink()
