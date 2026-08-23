from odoo.exceptions import ValidationError
from odoo import api, models,fields

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    is_partial_po = fields.Boolean(copy=False)

    @api.onchange('scheduled_date')
    def _onchange_scheduled_date(self):
        if self.scheduled_date:
            today = fields.Date.today()
            scheduled_date = self.scheduled_date.date() if hasattr(self.scheduled_date, 'date') else self.scheduled_date
            if scheduled_date < today:
                raise ValidationError("Scheduled cannot be earlier than today.")

    @api.onchange('date_done')
    def _onchange_date_done(self):
        if self.date_done:
            today = fields.Date.today()
            date_done = self.date_done.date() if hasattr(self.date_done, 'date') else self.date_done
            if date_done < today:
                raise ValidationError("Date Done cannot be earlier than today.")

    @api.constrains('move_ids')
    def _check_move_quantity(self):
        for record in self:
            if not record.purchase_id:
                continue
            for move in record.move_ids:
                po_line = record.purchase_id.order_line.filtered(
                    lambda l: l.product_id == move.product_id
                )
                if not po_line:
                    continue
                if record.is_partial_po:
                    if move.quantity > po_line[:1].partial_received_qty:
                        raise ValidationError(
                            f"You cannot enter quantity more than Partial PO quantity "
                            f"for product {move.product_id.display_name}."
                        )
                else:
                    if move.quantity > po_line[:1].product_qty:
                        raise ValidationError(
                            f"You cannot enter quantity more than Purchase Order quantity "
                            f"for product {move.product_id.display_name}."
                        )