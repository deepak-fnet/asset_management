from odoo import models


class VendorComparison(models.Model):
    _inherit = "vendor.comparison"

    # ------------------------------------------------------------------
    # Structure helpers
    # ------------------------------------------------------------------
    def _vc_rfqs(self):
        """The parent RFQs of this comparison (children created by
        action_partial_po carry rfq_id and are excluded)."""
        self.ensure_one()
        return self.env['purchase.order'].search([
            ('vendor_comparison_id', '=', self.id),
            ('rfq_id', '=', False),
        ])

    def _vc_demand_qty(self, product):
        """Demand declared on the comparison for a product."""
        self.ensure_one()
        comp_lines = self.line_ids.filtered(lambda l: l.product_id == product)
        return sum(comp_lines.mapped('product_qty'))

    def _vc_purchased_qty(self, product, exclude_rfq=None):
        """Quantity of ``product`` already committed within this comparison.

        Counts fully confirmed RFQs (state ``purchase``) and the child POs
        spawned by partial releases. ``exclude_rfq`` drops one RFQ and its
        children from the total, so an RFQ never invalidates itself.
        """
        self.ensure_one()
        rfqs = self._vc_rfqs()
        if exclude_rfq:
            rfqs -= exclude_rfq
        confirmed = rfqs.filtered(lambda o: o.state == 'purchase')
        children = self.env['purchase.order'].search([
            ('rfq_id', 'in', rfqs.ids),
            ('state', '=', 'purchase'),
        ])
        lines = (confirmed | children).mapped('order_line').filtered(
            lambda l: l.product_id == product and l.is_selected
        )
        return sum(lines.mapped('product_qty'))

    # ------------------------------------------------------------------
    # Requirement 1 + 2: propagate a confirmation to the sibling RFQs
    # ------------------------------------------------------------------
    def _vc_sync_sibling_rfqs(self, source_rfq=None):
        """Untick every line whose product is now fully purchased, then cancel
        any sibling RFQ that has no ticked line left.

        Requirement 2 is the untick step; requirement 1 is the degenerate case
        where every line of the sibling gets unticked.
        """
        for comparison in self:
            siblings = comparison._vc_rfqs().filtered(
                lambda o: o.state in ('draft', 'sent')
            )
            if source_rfq:
                siblings -= source_rfq

            for sibling in siblings:
                for line in sibling.order_line:
                    if not line.is_selected or not line.product_id:
                        continue
                    demand = comparison._vc_demand_qty(line.product_id)
                    if not demand:
                        continue
                    purchased = comparison._vc_purchased_qty(
                        line.product_id, exclude_rfq=sibling
                    )
                    if purchased >= demand:
                        line.is_selected = False

                if sibling.order_line and not sibling.order_line.filtered('is_selected'):
                    sibling.write({
                        'state': 'cancel',
                        'is_cancel_bid': True,
                        'reason': "Automatically cancelled: all products of this RFQ "
                                  "were purchased from another vendor of %s."
                                  % comparison.name,
                    })
                    sibling.message_post(
                        body="RFQ cancelled automatically. Every product was purchased "
                             "from another vendor in Vendor Comparison %s."
                             % comparison.name
                    )
        return True
