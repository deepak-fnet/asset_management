from odoo import models
from odoo.exceptions import UserError


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _action_done(self, cancel_backorder=False):
        """Give a readable, move-level error before the quants are touched."""
        for move in self:
            company = move.company_id or self.env.company
            if company.np_allow_negative_stock or not move.product_id.is_storable:
                continue
            src = move.location_id
            if src.usage not in ('internal', 'transit'):
                continue
            qty = sum(move.move_line_ids.mapped('quantity')) or move.product_uom_qty
            if move.product_uom:
                qty = move.product_uom._compute_quantity(qty, move.product_id.uom_id)
            available = move.product_id.with_context(location=src.id).qty_available
            if move.product_id.uom_id.compare(qty, available) > 0:
                raise UserError(
                    "Not enough stock to validate %s.\n\n"
                    "Product: %s\nSource location: %s\n"
                    "Requested: %s / On hand: %s\n\n"
                    "Negative stock is not allowed for this company."
                    % (
                        move.reference or move.picking_id.display_name or '',
                        move.product_id.display_name,
                        src.complete_name,
                        qty,
                        available,
                    )
                )
        return super()._action_done(cancel_backorder=cancel_backorder)
