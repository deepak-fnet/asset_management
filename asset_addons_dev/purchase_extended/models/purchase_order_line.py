from odoo import models, fields, api
from odoo.exceptions import ValidationError


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    rfq_qty = fields.Float(string="Actual Qty", compute="_compute_rfq_qty", store=True)
    partial_received_qty = fields.Float(string="Po Released Qty")
    is_update = fields.Boolean(string="Is Update")
    is_selected = fields.Boolean(
        string="Select",
        default=True,
        copy=False,
        help="Untick to exclude this product from the Purchase Order being confirmed. "
             "Lines are unticked automatically once the product has been fully "
             "purchased from another vendor of the same Vendor Comparison.",
    )

    @api.depends('product_qty')
    def _compute_rfq_qty(self):
        for rec in self:
            if not rec.is_update and rec.state == 'draft':
                rec.rfq_qty = rec.product_qty

    # ------------------------------------------------------------------
    # Vendor comparison helpers
    # ------------------------------------------------------------------
    def _vc_comparison(self):
        """Resolve the vendor comparison of the line, through the parent RFQ
        for child POs created by ``action_partial_po``."""
        self.ensure_one()
        order = self.order_id
        return order.vendor_comparison_id or order.rfq_id.vendor_comparison_id

    @api.constrains('is_selected')
    def _check_is_selected_already_purchased(self):
        """Requirement 2: ticking a product that is already fully purchased from
        another vendor of the same comparison must be rejected."""
        for line in self:
            if not line.is_selected or not line.product_id:
                continue
            order = line.order_id
            if order.state not in ('draft', 'sent', 'partial_po'):
                continue
            comparison = line._vc_comparison()
            if not comparison:
                continue

            demand = comparison._vc_demand_qty(line.product_id)
            if not demand:
                continue
            # Exclude what this very RFQ already contributed, otherwise a
            # partially released RFQ would invalidate its own lines.
            purchased = comparison._vc_purchased_qty(
                line.product_id, exclude_rfq=order.rfq_id or order
            )
            if purchased >= demand:
                raise ValidationError(
                    "You already created a Purchase Order for this product.\n\n"
                    "Product: %s\n"
                    "Demand: %s | Already purchased: %s\n\n"
                    "This product cannot be ordered again from this Vendor Comparison."
                    % (line.product_id.display_name, demand, purchased)
                )

    @api.onchange('is_selected')
    def _onchange_is_selected(self):
        """Give the user immediate feedback in the o2m instead of waiting for save."""
        if not self.is_selected or not self.product_id:
            return
        comparison = self._vc_comparison()
        if not comparison:
            return
        demand = comparison._vc_demand_qty(self.product_id)
        if not demand:
            return
        order = self.order_id
        purchased = comparison._vc_purchased_qty(
            self.product_id, exclude_rfq=order.rfq_id or order
        )
        if purchased >= demand:
            self.is_selected = False
            return {
                'warning': {
                    'title': "Already Purchased",
                    'message': "You already created a Purchase Order for %s."
                               % self.product_id.display_name,
                }
            }

    # ------------------------------------------------------------------
    # Override of vendor_comparison's remaining_qty compute: a rejected line
    # elsewhere must not be counted as "confirmed" usage of the product, and a
    # rejected line on the current record has no meaningful remaining qty.
    # ------------------------------------------------------------------
    def _compute_remaining_qty(self):
        for line in self:
            comparison = line._vc_comparison()
            if not comparison or not line.product_id:
                line.remaining_qty = 0.0
                continue
            demand = comparison._vc_demand_qty(line.product_id)
            if not demand:
                line.remaining_qty = 0.0
                continue
            order = line.order_id
            purchased = comparison._vc_purchased_qty(
                line.product_id, exclude_rfq=order.rfq_id or order
            )
            line.remaining_qty = demand - purchased

    # ------------------------------------------------------------------
    # Requirement 3 (this conversation): keep the rejected line visible on the
    # order (so it stays on record and shows why it isn't in the GRN), but it
    # must never generate a stock move / delivery line. purchase_stock calls
    # `order.order_line._create_stock_moves(picking)` when building the
    # receipt — filtering self down to selected lines here means rejected
    # lines are simply skipped when moves are generated.
    # ------------------------------------------------------------------
    def _create_stock_moves(self, *args, **kwargs):
        selected = self.filtered(lambda l: l.is_selected)
        return super(PurchaseOrderLine, selected)._create_stock_moves(*args, **kwargs)

    def action_view_last_grn(self):
        self.ensure_one()

        product = self.product_id

        pickings = self.env['stock.picking'].search([
            ('state', '=', 'done'),
            ('picking_type_id.code', '=', 'incoming'),
            ('move_ids.product_id', '=', product.id),
        ], limit=1)

        if not pickings:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'No Records',
                    'message': 'No Goods Receipt (GRN) found for this product.',
                    'type': 'info',
                    'sticky': False,
                }
            }

        return {
            'type': 'ir.actions.act_window',
            'name': 'Last 3 GRN',
            'res_model': 'last.grn.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_product_id': product.id,
            }
        }
