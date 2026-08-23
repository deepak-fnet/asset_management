from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

class VendorQuote(models.Model):
    _name = 'vendor.quote'
    _description = 'Vendor Quotation'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    rfq_id = fields.Many2one('purchase.order', string="RFQ Reference", required=True, ondelete='cascade')
    vendor_id = fields.Many2one('res.partner', string="Vendor", required=True, domain="[('is_vendor','=',True)]", tracking=True)
    
    quote_date = fields.Date(string="Quotation Date", default=fields.Date.today)
    delivery_date = fields.Date(string="Proposed Delivery Date", tracking=True)
    
    total_amount = fields.Float(string="Total Amount", compute="_compute_total_amount", store=True)
    
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('selected', 'Selected'),
        ('rejected', 'Rejected'),
    ], default='draft', string="Status", tracking=True)
    
    note = fields.Text(string="Vendor Notes", tracking=True)
    
    submitted_revision = fields.Integer(string="Submitted Revision", default=0)
    last_submitted_at = fields.Datetime(string="Last Submitted At")
    
    any_quote_selected = fields.Boolean(compute='_compute_any_quote_selected', store=False)
    
    line_ids = fields.One2many('vendor.quote.line', 'quote_id', string="Lines")
    
    rating = fields.Float(string="Rating", compute="_compute_rating")
    rank = fields.Integer(string="Rank", compute="_compute_rank", store=True)
    
    @api.model_create_multi
    def create(self, vals_list):
        """Block quote creation if RFQ has expired"""
        for vals in vals_list:
            rfq = self.env['purchase.order'].browse(vals.get('rfq_id'))
            if rfq.is_rfq_expired:
                raise UserError(
                    _("This RFQ has expired. You can no longer submit or modify quotations.")
                )
        return super().create(vals_list)

    @api.depends('rfq_id.vendor_finalized')
    def _compute_any_quote_selected(self):
        for rec in self:
            rec.any_quote_selected = rec.rfq_id.vendor_finalized

    @api.depends('line_ids.vendor_price', 'line_ids.product_qty')
    def _compute_total_amount(self):
        for rec in self:
            rec.total_amount = sum(line.vendor_price * line.product_qty for line in rec.line_ids)

    @api.depends('total_amount', 'delivery_date', 'vendor_id.performance_overview_score', 'rfq_id.date_planned')
    def _compute_rating(self):
        for rec in self:
            price_score = 0.0
            delivery_score = 0.0
            performance_score = rec.vendor_id.performance_overview_score or 0.0
            
            # 1. Price Score (50%): Relative to cheapest quote in RFQ
            all_quotes = rec.rfq_id.quote_ids.filtered(lambda q: q.total_amount > 0)
            if all_quotes:
                min_price = min(all_quotes.mapped('total_amount'))
                if rec.total_amount > 0:
                    price_score = (min_price / rec.total_amount) * 100
            
            # 2. Delivery Score (30%): Based on requested date
            if rec.delivery_date and rec.rfq_id.date_planned:
                requested = rec.rfq_id.date_planned.date()
                proposed = rec.delivery_date
                if proposed <= requested:
                    delivery_score = 100.0
                else:
                    delay = (proposed - requested).days
                    delivery_score = max(0.0, 100.0 - (delay * 5.0)) # 5% penalty per day
            elif rec.delivery_date:
                delivery_score = 100.0 # No requested date to compare against
            
            rec.rating = (price_score * 0.5) + (delivery_score * 0.3) + (performance_score * 0.2)
    
    @api.depends('rfq_id.quote_ids.total_amount', 'total_amount')
    def _compute_rank(self):
        """Auto-rank quotes by lowest total amount (price-based only)"""
        for rec in self:
            if rec.rfq_id:
                # Get all quotes with valid amounts for this RFQ (ignore state completely)
                quotes = rec.rfq_id.quote_ids.filtered(lambda q: q.total_amount > 0)
                if quotes:
                    # Sort by total_amount and assign ranks purely based on price
                    sorted_quotes = quotes.sorted(key=lambda q: q.total_amount)
                    for i, quote in enumerate(sorted_quotes):
                        if quote.id == rec.id:
                            rec.rank = i + 1
                            break
                    else:
                        rec.rank = 0
                else:
                    rec.rank = 0
            else:
                rec.rank = 0

    def write(self, vals):
        """Block quote editing if RFQ has expired"""
        for rec in self:
            if rec.rfq_id.is_rfq_expired:
                raise UserError(
                    _("This RFQ has expired. You can no longer submit or modify quotations.")
                )
        
        # Feature: RFQ Revision Handling
        if vals.get('state') == 'submitted':
            for rec in self:
                vals['submitted_revision'] = rec.rfq_id.rfq_revision
                vals['last_submitted_at'] = fields.Datetime.now()

        res = super().write(vals)
        
        # Business logic only: Update PO comparison state when quote state changes
        if 'state' in vals:
            for rec in self:
                if rec.rfq_id and rec.state == 'submitted':
                    rec.rfq_id.comparison_state = 'received'
        
        return res

    def action_toggle_vendor(self):
        """Toggle vendor selection state"""
        # Block if RFQ expired
        if self.rfq_id.is_rfq_expired:
            raise UserError(
                _("This RFQ has expired. You can no longer submit or modify quotations.")
            )
        if self.state == 'selected':
            return self.action_unselect()
        else:
            return self.action_select()

    def action_select(self):
        """Select this quote as winner and update PO prices. Enforces single selection."""
        for rec in self:
            # Block if RFQ expired
            if rec.rfq_id.is_rfq_expired:
                raise UserError(
                    _("This RFQ has expired. You can no longer submit or modify quotations.")
                )
            
            # 1. Reject ALL other quotes of same RFQ First (ensure clean state)
            others = rec.rfq_id.quote_ids.filtered(lambda q: q.id != rec.id)
            others.write({'state': 'rejected'})
            
            # 2. Set current quote to selected
            rec.write({'state': 'selected'})
            
            # 3. Synchronize Purchase Order
            rec.rfq_id.write({
                'partner_id': rec.vendor_id.id,
                'vendor_ids': [(6, 0, [rec.vendor_id.id])],
                'vendor_finalized': True
            })

            # 4. Update PO line prices with vendor quote prices
            for line in rec.rfq_id.order_line:
                quote_line = rec.line_ids.filtered(lambda ql: ql.po_line_id == line)
                if quote_line:
                    vendor_price = quote_line[0].vendor_price
                    if not vendor_price:
                        # Fallback to seller price if not quoted
                        seller = line.product_id._select_seller(
                            partner_id=rec.vendor_id,
                            quantity=line.product_qty,
                            date=rec.rfq_id.date_order,
                            uom_id=line.product_uom_id,
                        )
                        vendor_price = seller.price if seller else (line.product_id.standard_price or 0.0)
                    line.write({'price_unit': vendor_price})
            
            # 5. Update comparison state
            if rec.rfq_id.comparison_state == 'sent':
                rec.rfq_id.comparison_state = 'received'
                
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_unselect(self):
        """Unselect vendor and reset all quotes to neutral state (submitted)"""
        for rec in self:
            po = rec.rfq_id
            # Reset all quotes of this PO to submitted
            po.quote_ids.filtered(lambda q: q.state in ['selected', 'rejected']).write({'state': 'submitted'})
            
            # Restore PO vendor_ids to include all vendors who have quotes
            all_quote_vendors = po.quote_ids.mapped('vendor_id').ids
            
            # Reset Purchase Order (Do NOT clear partner_id as it is required)
            po.write({
                'vendor_finalized': False,
                'vendor_ids': [(6, 0, all_quote_vendors)]
            })
            
        return {'type': 'ir.actions.client', 'tag': 'reload'}

class VendorQuoteLine(models.Model):
    _name = 'vendor.quote.line'
    _description = 'Vendor Quotation Line'

    quote_id = fields.Many2one('vendor.quote', string="Quotation", ondelete='cascade')
    po_line_id = fields.Many2one('purchase.order.line', string="Purchase Order Line", ondelete='cascade')
    
    product_id = fields.Many2one('product.product', string="Product", related='po_line_id.product_id', store=True)
    product_qty = fields.Float(string="Quantity", related='po_line_id.product_qty')
    uom_id = fields.Many2one('uom.uom', string="Unit of Measure", related='po_line_id.product_uom_id')

    vendor_price = fields.Float(string="Vendor Offer Price")
    internal_price = fields.Float(string="Internal Unit Price", readonly=True)  # Show internal price to vendor
    delivery_date = fields.Date(string="Delivery Date")
    bulk_qty = fields.Float(string="Bulk Quantity")
    bulk_price = fields.Float(string="Bulk Price")
    vendor_notes = fields.Text(string="Vendor Notes")

    price_subtotal = fields.Float(string="Subtotal", compute="_compute_price_subtotal")

    @api.depends('vendor_price', 'product_qty')
    def _compute_price_subtotal(self):
        for line in self:
            line.price_subtotal = line.vendor_price * line.product_qty
