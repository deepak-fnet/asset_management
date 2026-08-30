from odoo import api, models
from odoo.exceptions import ValidationError

from .np_mixin import check_not_past, check_order

# On account.move.line, product lines are flagged 'product'; the others are
# sections, notes and the accounting counterparts of the invoice.
NON_PRODUCT_TYPES = ('line_section', 'line_note')


class AccountMove(models.Model):
    _inherit = 'account.move'

    @api.constrains('invoice_date', 'invoice_date_due', 'date', 'state')
    def _np_check_dates(self):
        for move in self:
            if not move.is_invoice(include_receipts=True):
                continue
            if move.state == 'draft':
                check_not_past(move, 'invoice_date', "Invoice Date")
            check_order(move, 'invoice_date', 'invoice_date_due',
                        "Invoice Date", "Due Date")

    def _post(self, soft=True):
        for move in self:
            if move.is_invoice(include_receipts=True):
                if not move.invoice_line_ids.filtered(
                        lambda line: line.display_type not in NON_PRODUCT_TYPES):
                    raise ValidationError(
                        "%s cannot be posted without any invoice line."
                        % move.display_name)
                if move.currency_id.compare_amounts(move.amount_total, 0) < 0:
                    raise ValidationError(
                        "%s cannot be posted with a negative total."
                        % move.display_name)
        return super()._post(soft=soft)


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    @api.constrains('quantity', 'price_unit', 'discount')
    def _np_check_values(self):
        for line in self:
            if line.display_type in NON_PRODUCT_TYPES or \
                    not line.move_id.is_invoice(include_receipts=True):
                continue
            if line.quantity < 0:
                raise ValidationError(
                    "Quantity cannot be negative on line '%s'." % line.name)
            if not 0 <= line.discount <= 100:
                raise ValidationError(
                    "Discount must stay between 0 and 100 on line '%s'." % line.name)
