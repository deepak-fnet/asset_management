from odoo import api, models
from odoo.exceptions import ValidationError

from .np_mixin import check_not_past, check_order, check_locked_lines

# States in which a purchase order is considered closed for line edition.
LOCKED_STATES = ('purchase', 'done', 'cancel')


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    @api.constrains('date_order', 'date_planned', 'date_approve')
    def _np_check_dates(self):
        for order in self:
            if order.state in ('draft', 'sent'):
                check_not_past(order, 'date_order', "Order Deadline")
            check_order(order, 'date_order', 'date_planned',
                        "Order Deadline", "Expected Arrival")

    @api.constrains('order_line')
    def _np_check_has_line(self):
        for order in self:
            if order.state in ('purchase', 'done') and not order.order_line:
                raise ValidationError(
                    "A confirmed purchase order must keep at least one line.")

    def button_confirm(self):
        for order in self:
            if not order.order_line.filtered(lambda line: not line.display_type):
                raise ValidationError(
                    "%s cannot be confirmed without any product line."
                    % order.display_name)
            check_not_past(order, 'date_order', "Order Deadline")
        return super().button_confirm()

    def unlink(self):
        for order in self:
            if order.state in ('purchase', 'done'):
                raise ValidationError(
                    "%s is confirmed and cannot be deleted. Cancel it instead."
                    % order.display_name)
        return super().unlink()


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    _np_parent_field = 'order_id'

    @api.constrains('product_qty', 'price_unit', 'date_planned')
    def _np_check_values(self):
        for line in self:
            if line.display_type:
                continue
            if line.product_uom_id.compare(line.product_qty, 0) <= 0:
                raise ValidationError(
                    "Quantity must be strictly positive on line '%s'."
                    % line.product_id.display_name)
            if line.currency_id.compare_amounts(line.price_unit, 0) < 0:
                raise ValidationError(
                    "Unit price cannot be negative on line '%s'."
                    % line.product_id.display_name)

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if not self.env.context.get('np_bypass_lock'):
            check_locked_lines(lines, LOCKED_STATES,
                               doc_label="Purchase order", operation="added")
        return lines

    def write(self, vals):
        watched = {'product_id', 'product_qty', 'price_unit', 'product_uom_id',
                   'tax_ids', 'discount'}
        if watched & set(vals) and not self.env.context.get('np_bypass_lock'):
            check_locked_lines(self, LOCKED_STATES,
                               doc_label="Purchase order", operation="modified")
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get('np_bypass_lock'):
            check_locked_lines(self, LOCKED_STATES,
                               doc_label="Purchase order", operation="deleted")
        return super().unlink()
