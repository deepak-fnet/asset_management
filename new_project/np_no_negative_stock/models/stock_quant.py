from odoo import api, models
from odoo.exceptions import ValidationError

# Location usages on which negative stock is forbidden.
CHECKED_USAGES = ('internal', 'transit')


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    def _np_check_negative(self):
        """Raise if any quant of a checked location ends up below zero."""
        for quant in self:
            company = quant.company_id or self.env.company
            if company.np_allow_negative_stock:
                continue
            if quant.location_id.usage not in CHECKED_USAGES:
                continue
            if quant.product_id.is_storable is False:
                continue
            if quant.product_uom_id.compare(quant.quantity, 0) < 0:
                raise ValidationError(
                    "Negative stock is not allowed.\n\n"
                    "Product: %s\nLocation: %s\nResulting quantity: %s %s\n\n"
                    "Receive or adjust the stock before performing this operation."
                    % (
                        quant.product_id.display_name,
                        quant.location_id.complete_name,
                        quant.quantity,
                        quant.product_uom_id.name or '',
                    )
                )

    @api.constrains('quantity', 'location_id', 'product_id')
    def _np_constrains_negative(self):
        self._np_check_negative()

    @api.model_create_multi
    def create(self, vals_list):
        quants = super().create(vals_list)
        quants._np_check_negative()
        return quants

    def write(self, vals):
        res = super().write(vals)
        if 'quantity' in vals or 'inventory_quantity' in vals or 'inventory_diff_quantity' in vals:
            self._np_check_negative()
        return res
