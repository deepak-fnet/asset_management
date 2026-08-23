import base64
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    is_bid_received = fields.Boolean(copy=False)
    is_cancel_bid = fields.Boolean(copy=False)
    reason = fields.Char(copy=False)
    bill_to_id = fields.Many2one('res.partner', string='Bill To', related='partner_id', store=True, readonly=True)
    ship_to_id = fields.Many2one('res.partner', string='Ship To', related='partner_id', store=True, readonly=True)
    bill_to_contact_id = fields.Many2one('res.users', string='Bill To Contact', ondelete='set null')
    ship_to_contact_id = fields.Many2one('res.users', string='Ship To Contact', ondelete='set null')
    state = fields.Selection(
        selection_add=[
            ('sent', 'RFQ Sent'),
            ('partial_po', 'Partial PO Released'),
            ('to approve', 'To Approve'),
        ],
        ondelete={'partial_po': 'set default'}
    )
    ready_to_confirm = fields.Boolean(string='Is Update', compute='_compute_ready_to_confirm')
    is_check_over_qty = fields.Boolean(copy=False)
    is_partial_po = fields.Boolean(copy=False)
    rfq_id = fields.Many2one('purchase.order')
    purchase_count = fields.Integer(string="Purchase Count", compute='_compute_purchase_order')
    is_confirm_done = fields.Boolean(copy=False)

    # ------------------------------------------------------------------
    # Vendor comparison helpers
    # ------------------------------------------------------------------
    def _vc_comparison(self):
        """Comparison of this order, resolved through the parent RFQ for the
        child POs created by action_partial_po."""
        self.ensure_one()
        return self.vendor_comparison_id or self.rfq_id.vendor_comparison_id

    def _vc_selected_lines(self):
        """Lines actually being ordered. Outside a vendor comparison every line
        is ordered, so the flag is ignored there."""
        self.ensure_one()
        if not self._vc_comparison():
            return self.order_line
        return self.order_line.filtered('is_selected')

    def _vc_sync_after_confirm(self):
        """Push the effect of this confirmation onto the sibling RFQs."""
        for order in self:
            comparison = order._vc_comparison()
            if comparison:
                comparison._vc_sync_sibling_rfqs(source_rfq=order.rfq_id or order)
        return True

    def _vc_check_has_selection(self):
        """Block confirming an order where every product has been deselected —
        there would be nothing left to actually purchase from this vendor."""
        for order in self:
            if not order._vc_comparison() or order.rfq_id:
                continue
            if order.order_line and not order._vc_selected_lines():
                raise ValidationError(
                    "No product is selected on this Purchase Order.\n\n"
                    "Tick at least one product before confirming, or cancel the RFQ "
                    "using 'Cancel Bid'."
                )
        return True

    # ------------------------------------------------------------------
    def button_confirm(self):
        self._vc_check_has_selection()

        for order in self:
            error_lines = []
            for line in order._vc_selected_lines():
                new_qty = line.product_qty or 0.0
                all_related_pos = self.env['purchase.order'].search([
                    ('rfq_id', '=', order.id),
                    ('state', '=', 'purchase'),
                ])
                matched_lines = all_related_pos.mapped('order_line').filtered(
                    lambda l: l.product_id.id == line.product_id.id
                )
                total_confirmed = sum(matched_lines.mapped('product_qty'))
                remaining_qty = (line.rfq_qty or 0.0) - total_confirmed
                if remaining_qty < new_qty and all_related_pos:
                    error_lines.append(
                        "• %s → Remaining: %s | Requested: %s"
                        % (
                            line.product_id.display_name,
                            remaining_qty,
                            new_qty,
                        )
                    )
            if error_lines:
                raise ValidationError(
                    "The requested quantities exceed the remaining allowable quantities "
                    "for the following products:\n\n%s\n\n"
                    "Please adjust the quantities before confirming."
                    % ("\n".join(error_lines))
                )

        if not self.ready_to_confirm or self.is_over_qty and not self.is_check_over_qty:
            return self.action_open_confirmation_wizard()
        self.name = self.env['ir.sequence'].next_by_code('purchase.order.seq2') or '/'
        for order in self:
            for line in order._vc_selected_lines():
                line.partial_received_qty += line.product_qty
        for order in self:
            if order.state not in ['draft', 'sent', 'partial_po']:
                continue
            order.order_line._validate_analytic_distribution()
            order._add_supplier_to_product()
            if order._approval_allowed():
                order.button_approve()
            else:
                order.write({'state': 'to approve'})
        # Requirements 1 & 2 — propagate this confirmation to the sibling RFQs.
        self._vc_sync_after_confirm()
        return True

    def action_view_purchase(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Related POs',
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('rfq_id', '=', self.id)],
        }

    def action_view_rfq_po(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Purchase Order',
            'res_model': 'purchase.order',
            'view_mode': 'form',
            'res_id': self.rfq_id.id,
        }

    def _compute_purchase_order(self):
        for rec in self:
            rec.purchase_count = self.env['purchase.order'].search_count([('rfq_id', '=', rec.id)])

    @api.depends('state', 'vendor_comparison_id',
                 'order_line.product_qty', 'order_line.rfq_qty',
                 'order_line.partial_received_qty', 'order_line.remaining_qty',
                 'order_line.is_selected')
    def _compute_ready_to_confirm(self):
        for order in self:
            # Rejected products are excluded from every coverage check below —
            # they are not being purchased on this order, so they must not
            # count for or against "is this order ready to confirm".
            lines = order._vc_selected_lines()

            rfq_qty = all(
                line.rfq_qty <= line.product_qty + line.partial_received_qty
                for line in lines
            )
            remaining_qty = all(
                line.rfq_qty <= line.product_qty + line.partial_received_qty
                for line in lines
            )
            if order.state == 'sent' and order.vendor_comparison_id and remaining_qty:
                order.ready_to_confirm = all(
                    line.product_qty >= line.remaining_qty
                    for line in lines
                )
            elif order.state == 'sent' and rfq_qty:
                order.ready_to_confirm = True
            elif order.state == 'sent':
                order.ready_to_confirm = all(
                    line.product_qty >= line.rfq_qty
                    for line in lines
                )
            elif order.state == 'partial_po' and order.vendor_comparison_id and remaining_qty:
                order.ready_to_confirm = True
            elif order.state == 'partial_po' and order.vendor_comparison_id:
                order.ready_to_confirm = all(
                    (
                            (line.rfq_qty or 0.0) -
                            sum(
                                self.env['purchase.order']
                                .search([
                                    ('rfq_id', '=', order.id),
                                    ('state', '=', 'purchase'),
                                ])
                                .mapped('order_line')
                                .filtered(lambda l: l.product_id.id == line.product_id.id and l.is_selected)
                                .mapped('product_qty')
                            )
                    ) < (line.product_qty or 0.0)
                    for line in lines
                )
            elif order.state == 'partial_po' and rfq_qty:
                order.ready_to_confirm = True
            elif order.state == 'partial_po':
                order.ready_to_confirm = all(
                    line.rfq_qty == line.product_qty + line.partial_received_qty
                    for line in lines
                )
            else:
                order.ready_to_confirm = False

    def action_open_confirmation_wizard(self):
        if self.is_over_qty and not self.is_check_over_qty:
            return self.action_check_product_quantity_limit()
        if not self.state == 'sent' and not self.is_confirm_done:
            return {
                'name': 'Purchase Confirmation',
                'type': 'ir.actions.act_window',
                'res_model': 'po.confirm.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {
                    'default_purchase_id': self.id,
                }
            }
        self.is_confirm_done = False
        if not self.ready_to_confirm:
            return self.action_partial_po()

    def action_partial_po(self):
        # NOTE: this path handles genuine quantity-based partial fulfillment
        # (ordering less than the full remaining demand of a product now,
        # topping it up later). It is unrelated to product exclusion via the
        # "Select" checkbox: rejected lines stay on the parent RFQ untouched
        # (still visible, still deselected) and are simply never copied into
        # the child PO below.
        for order in self:
            for line in order._vc_selected_lines():
                line.partial_received_qty += line.product_qty
        PurchaseOrder = self.env['purchase.order']
        PurchaseOrderLine = self.env['purchase.order.line']
        for order in self:
            order.write({
                'state': 'partial_po',
                'is_partial_po': True,
                'date_approve': fields.Datetime.now(),
            })
            picking = PurchaseOrder.create({
                'state': 'draft',
                'rfq_id': order.id,
                'partner_id': order.partner_id.id,
                'partner_ref': order.partner_ref,
                'date_order': order.date_order,
                'date_planned': order.date_planned,
                'bill_to_id': order.bill_to_id.id,
                'bill_to_contact_id': order.bill_to_contact_id.id,
                'ship_to_contact_id': order.ship_to_contact_id.id,
                'origin': order.name,
                'picking_type_id': order.picking_type_id.id,
                'user_id': order.user_id.id,
                'incoterm_id': order.incoterm_id.id,
                'incoterm_location': order.incoterm_location,
                'payment_term_id': order.payment_term_id.id,
                'fiscal_position_id': order.fiscal_position_id.id,
            })
            for line in order._vc_selected_lines():
                PurchaseOrderLine.create({
                    'order_id': picking.id,
                    'product_id': line.product_id.id,
                    'product_uom_id': line.product_uom_id.id,
                    'product_qty': line.product_qty,
                    'rfq_qty': line.product_qty,
                    'price_unit': line.price_unit,
                    'tax_ids': [(6, 0, line.tax_ids.ids)],
                    'propagate_cancel': line.propagate_cancel,
                    'date_planned': line.date_planned,
                    'is_selected': True,
                })
            # Route through button_approve() (not a direct state write) so
            # Odoo's _create_picking() actually runs and the delivery for this
            # child PO gets generated — previously the child was created
            # straight into 'purchase' state, which _create_picking() never
            # saw, so no delivery was made for it.
            picking.order_line._validate_analytic_distribution()
            picking._add_supplier_to_product()
            if picking._approval_allowed():
                picking.button_approve()
            else:
                picking.write({'state': 'to approve'})
            # Requirements 1 & 2 — now that this vendor has a confirmed PO,
            # sync the sibling RFQs of the same Vendor Comparison.
            order._vc_sync_after_confirm()
        return True

    def action_bid_received(self):
        for order in self:
            for line in order._vc_selected_lines():
                if not line.price_unit:
                    raise ValidationError(f'Please Fill the Unit Price - {line.product_id.name}')
                if not line.product_qty:
                    raise ValidationError(f'Please Fill the Product Quantity - {line.product_id.name}')

            order.write({'is_bid_received': True})

        return True

    # ------------------------------------------------------------------
    # Overrides of vendor_comparison's over-qty logic: a rejected ("Select"
    # unticked) line is not being purchased on this order, so it must not be
    # treated as over-quantity, and it must not trigger the "already have a
    # confirmed PO for this product" warning either.
    # ------------------------------------------------------------------
    @api.depends('vendor_comparison_id', 'rfq_id', 'state',
                 'order_line.product_id', 'order_line.product_qty',
                 'order_line.is_selected', 'order_line.partial_received_qty')
    def _compute_is_over_qty(self):
        for order in self:
            order.is_over_qty = False
            comparison = order._vc_comparison()
            if not comparison:
                continue
            for line in order._vc_selected_lines():
                if not line.product_id:
                    continue
                demand = comparison._vc_demand_qty(line.product_id)
                if not demand:
                    continue
                purchased = comparison._vc_purchased_qty(
                    line.product_id, exclude_rfq=order.rfq_id or order
                )
                remaining = demand - purchased
                if line.product_qty > remaining:
                    order.is_over_qty = True
                    break

    def action_check_product_quantity_limit(self):
        Wizard = self.env['purchase.qty.exceed']

        for order in self:
            comparison = order._vc_comparison()
            if not comparison:
                continue

            products_with_pos = []
            for line in order._vc_selected_lines():
                if not line.product_id:
                    continue
                purchased = comparison._vc_purchased_qty(
                    line.product_id, exclude_rfq=order.rfq_id or order
                )
                if purchased:
                    products_with_pos.append(f"{line.product_id.name} ({int(purchased)} units)")

            if products_with_pos:
                product_list = "<br/>".join([f"• {p}" for p in products_with_pos])

                full_message = f"""
                    <div style="font-family: Arial, sans-serif; font-size: 14px; color: #333;">
                        <p style="margin-bottom: 12px;">
                            The following products already have confirmed Purchase Orders:
                        </p>
                        <div style="margin: 16px 0; padding: 12px; background-color: #fff3cd; border-left: 4px solid #ffc107;">
                            {product_list}
                        </div>
                        <p style="margin-top: 16px; font-weight: bold; color: #d9534f;">
                            ⚠️ Are you sure you want to continue with this purchase?
                        </p>
                    </div>
                """

                wizard = Wizard.create({
                    'name': 'Confirm Purchase',
                    'message': full_message,
                    'purchase_order_id': order.id,
                })
                return {
                    'name': 'Confirm Purchase',
                    'type': 'ir.actions.act_window',
                    'res_model': 'purchase.qty.exceed',
                    'view_mode': 'form',
                    'res_id': wizard.id,
                    'target': 'new',
                }

        return True

    def action_open_cancel_wizard(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cancel Bid',
            'res_model': 'cancel.bid.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_purchase_id': self.id,
            }
        }

    @api.onchange('date_order')
    def _onchange_date_order(self):
        if self.date_order:
            today = fields.Date.today()
            date_order = self.date_order.date() if hasattr(self.date_order, 'date') else self.date_order
            if date_order < today:
                raise ValidationError("Order Deadline cannot be earlier than today.")

    @api.onchange('date_planned')
    def _onchange_date_planned(self):
        if self.date_planned:
            today = fields.Date.today()
            date_planned = self.date_planned.date() if hasattr(self.date_planned, 'date') else self.date_planned
            if date_planned < today:
                raise ValidationError("Expected Arrival cannot be earlier than today.")

    @api.onchange('effective_date')
    def _onchange_effective_date(self):
        if self.effective_date:
            today = fields.Date.today()
            effective_date = self.effective_date.date() if hasattr(self.effective_date, 'date') else self.effective_date
            if effective_date < today:
                raise ValidationError("Arrival cannot be earlier than today.")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('purchase.order.seq1') or 'New'
        return super().create(vals_list)

    def unlink(self):
        raise UserError("You cannot delete Purchase records.")

    def action_rfq_send(self):
        for order in self:
            if not order.order_line:
                raise ValidationError(
                    "Please add product lines before sending the RFQ."
                )
            if not order.partner_id.email:
                raise ValidationError(
                    ("Vendor '%s' does not have an email address. "
                     "Please add an email address before sending the RFQ.")
                    % order.partner_id.name
                )

        res = super(PurchaseOrder, self).action_rfq_send()

        if self.state in ['draft', 'sent']:
            report = self.env.ref('report_management.action_report_request_for_quotation')
        else:
            report = self.env.ref('report_management.action_purchase_order_report')

        pdf_content, _ = report._render_qweb_pdf(
            report.report_name,
            res_ids=[self.id]
        )
        if self.state in ['draft', 'sent']:
            attachment_name = f"Request for Quotation - {self.name}.pdf"
        else:
            attachment_name = f"Purchase Order - {self.name}.pdf"
        attachment = self.env['ir.attachment'].create({
            'name': attachment_name,
            'type': 'binary',
            'datas': base64.b64encode(pdf_content),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'application/pdf',
        })

        if not res.get('context'):
            res['context'] = {}
        existing_attachments = res['context'].get('default_attachment_ids', [])
        if existing_attachments and isinstance(existing_attachments, list) and existing_attachments[0][0] == 6:
            res['context']['default_attachment_ids'][0][2].append(attachment.id)
        else:
            res['context']['default_attachment_ids'] = [(6, 0, [attachment.id])]

        res['context'].update({
            'default_partner_ids': [(6, 0, [self.partner_id.id])]
        })

        return res

    def amount_total_in_words(self):
        try:
            amount_in_words = self.currency_id.amount_to_text(self.amount_total).replace(',', '')
            return amount_in_words
        except Exception as e:
            return f"Error converting amount to words: {e}"
