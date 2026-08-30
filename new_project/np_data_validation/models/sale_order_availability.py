from odoo import api, fields, models

# Order states in which warning the salesperson still makes sense.
DRAFT_STATES = ('draft', 'sent')


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    np_shortage_qty = fields.Float(
        string="Missing Quantity", compute='_compute_np_shortage',
        digits='Product Unit',
        help="Quantity ordered above the quantity on hand in the warehouse.")
    np_has_shortage = fields.Boolean(compute='_compute_np_shortage')

    @api.depends('product_id', 'product_uom_qty', 'product_uom_id', 'state',
                 'order_id.warehouse_id')
    def _compute_np_shortage(self):
        for line in self:
            line.np_shortage_qty = 0.0
            line.np_has_shortage = False
            if line.display_type or not line.product_id.is_storable:
                continue
            available = line._np_available_qty()
            missing = line.product_uom_qty - available
            if line.product_uom_id.compare(missing, 0) > 0:
                line.np_shortage_qty = missing
                line.np_has_shortage = True

    def _np_available_qty(self):
        """Quantity on hand for the line's product, in the line's UoM.

        On hand -- not free -- quantity is used on purpose: it is the same
        figure the delivery checks, so the warning and the block always agree.
        """
        self.ensure_one()
        product = self.product_id.with_company(self.company_id or self.env.company)
        if self.order_id.warehouse_id:
            product = product.with_context(warehouse_id=self.order_id.warehouse_id.id)
        available = product.qty_available
        if self.product_uom_id and self.product_uom_id != product.uom_id:
            available = product.uom_id._compute_quantity(available, self.product_uom_id)
        return available

    def _np_shortage_message(self):
        """One human-readable line per product that is short."""
        msgs = []
        for line in self:
            if not line.np_has_shortage:
                continue
            msgs.append(
                "%s: %s %s ordered, only %s on hand (%s missing)."
                % (line.product_id.display_name,
                   line.product_uom_qty,
                   line.product_uom_id.name or '',
                   line._np_available_qty(),
                   line.np_shortage_qty))
        return msgs

    @api.onchange('product_id', 'product_uom_qty', 'product_uom_id')
    def _np_onchange_warn_availability(self):
        """Warn -- but do not block -- when the line goes beyond the stock on hand."""
        if self.display_type or not self.product_id.is_storable:
            return
        if self.order_id.state not in DRAFT_STATES:
            return
        available = self._np_available_qty()
        if self.product_uom_id.compare(self.product_uom_qty, available) <= 0:
            return
        return {
            'warning': {
                'title': "Not enough stock",
                'message': (
                    "%s\n\nOnly %s %s of %s are on hand%s.\n\n"
                    "You can still record this order, but the delivery cannot be "
                    "validated until the stock is there -- negative stock is not "
                    "allowed."
                    % ("You are selling more than what you have in stock.",
                       available, self.product_uom_id.name or '',
                       self.product_id.display_name,
                       self.order_id.warehouse_id
                       and " in %s" % self.order_id.warehouse_id.name or '')),
            }
        }


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    np_shortage_warning = fields.Text(compute='_compute_np_shortage_warning')

    @api.depends('order_line.np_has_shortage', 'order_line.np_shortage_qty')
    def _compute_np_shortage_warning(self):
        for order in self:
            msgs = order.order_line._np_shortage_message()
            order.np_shortage_warning = "\n".join(msgs) if msgs else False

    def action_confirm(self):
        res = super().action_confirm()
        # Keep a trace in the chatter: the order was confirmed short of stock.
        for order in self:
            msgs = order.order_line._np_shortage_message()
            if msgs:
                order.message_post(body=(
                    "<b>Confirmed with insufficient stock.</b> The delivery will "
                    "stay blocked until the goods are received:<ul>%s</ul>"
                    % "".join("<li>%s</li>" % m for m in msgs)))
        return res
