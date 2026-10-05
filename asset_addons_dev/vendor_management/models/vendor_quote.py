from odoo import models, fields, api, _
from odoo.exceptions import UserError

class VendorQuote(models.Model):
    _name = 'vendor.quote'
    _description = 'Vendor Quotation'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    rfq_id = fields.Many2one('purchase.order', string="RFQ Reference", required=True, ondelete='cascade')
    vendor_id = fields.Many2one('res.partner', string="Vendor", required=True, domain="[('is_vendor','=',True)]", tracking=True)
    
    quote_date = fields.Date(string="Quotation Date", default=fields.Date.today)
    # Computed, not a plain input: the vendor portal only ever collects a
    # delivery date PER LINE (one per product), there has never been an
    # overall-delivery-date box on the form. A plain field here just sat
    # permanently blank - which, since _compute_rating below also reads it,
    # silently zeroed out 30% of every quote's rating too. Rolling it up
    # from the lines (the vendor's actual commitment for every item to be
    # ready) gives it a real value instead.
    delivery_date = fields.Date(string="Proposed Delivery Date", tracking=True,
                                 compute="_compute_delivery_date", store=True)

    total_amount = fields.Float(string="Total Amount", compute="_compute_total_amount", store=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('selected', 'Selected'),
        ('rejected', 'Rejected'),
    ], default='draft', string="Status", tracking=True)

    note = fields.Text(string="Vendor Notes", tracking=True)
    # Read-only rollup of the per-line notes, for internal comparison lists.
    # note (above) is the vendor's own separate "General Notes / Terms" box
    # on the portal form and stays a plain field for that; this just makes
    # per-line remarks (like the ones actually used in practice) visible
    # without requiring a click into each line.
    line_notes_summary = fields.Char(string="Line Notes", compute="_compute_line_notes_summary")
    
    submitted_revision = fields.Integer(string="Submitted Revision", default=0)
    last_submitted_at = fields.Datetime(string="Last Submitted At")

    # Per-line result of the comparison grid: how many of this quote's lines
    # actually won their product's award, since different products on the
    # same RFQ can go to different vendors. Replaces any_quote_selected
    # (a whole-order concept that no longer applies).
    line_result = fields.Selection([
        ('none', 'No Lines Won'),
        ('partial', 'Partially Won'),
        ('all', 'All Lines Won'),
    ], compute='_compute_line_result', store=True, string="Result")

    line_ids = fields.One2many('vendor.quote.line', 'quote_id', string="Lines")
    # After a rejection the purchase user may renegotiate with a vendor and
    # correct their bid - the form allows editing a submitted bid then.
    rfq_approval_status = fields.Selection(related='rfq_id.approval_status', string="RFQ Approval Status")

    # store=True: needed so quote_rating (below, related to this field) can
    # be aggregated in the vendor-grouped comparison screen's group header
    # row - a non-stored field has no DB column for the aggregation query to
    # read, which fails outright rather than just displaying blank.
    rating = fields.Float(string="Rating", compute="_compute_rating", store=True)
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

    def action_submit_bid(self):
        """Buyer enters a bid on a vendor's behalf (vendor cannot use the
        portal, sends prices by phone/email instead) and submits it - same
        end state as the vendor submitting it themselves on the portal."""
        for quote in self:
            if quote.state != 'draft':
                raise UserError(_("This bid has already been submitted."))
            if quote.rfq_id.comparison_state != 'sent':
                raise UserError(_("Bidding is closed for this RFQ."))
            if not any(line.vendor_price > 0 for line in quote.line_ids):
                raise UserError(_("Enter the vendor's price for at least one product."))
            # write() stamps submitted_revision/last_submitted_at itself
            quote.write({'state': 'submitted'})
            quote.rfq_id.message_post(body=_(
                "Bid for %(vendor)s entered manually by %(user)s.",
                vendor=quote.vendor_id.name, user=self.env.user.name))

    def action_open_bid(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Bid - %s', self.vendor_id.name),
            'res_model': 'vendor.quote',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(self.env.ref('vendor_management.view_vendor_quote_form').id, 'form')],
            'target': 'current',
        }

    @api.depends('line_ids.po_line_id.vendor_id', 'vendor_id')
    def _compute_line_result(self):
        for rec in self:
            lines = rec.line_ids.filtered('po_line_id')
            if not lines:
                rec.line_result = 'none'
                continue
            won = lines.filtered(lambda l: l.po_line_id.vendor_id == rec.vendor_id)
            if not won:
                rec.line_result = 'none'
            elif len(won) == len(lines):
                rec.line_result = 'all'
            else:
                rec.line_result = 'partial'

    @api.depends('line_ids.vendor_price', 'line_ids.product_qty')
    def _compute_total_amount(self):
        for rec in self:
            rec.total_amount = sum(line.vendor_price * line.product_qty for line in rec.line_ids)

    @api.depends('line_ids.delivery_date')
    def _compute_delivery_date(self):
        for rec in self:
            dates = [d for d in rec.line_ids.mapped('delivery_date') if d]
            rec.delivery_date = max(dates) if dates else False

    @api.depends('line_ids.vendor_notes')
    def _compute_line_notes_summary(self):
        for rec in self:
            notes = [n for n in rec.line_ids.mapped('vendor_notes') if n]
            rec.line_notes_summary = '; '.join(notes)

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

        # Bids received only once EVERY invited vendor has submitted - not on
        # the first one (that is what this used to do, which closed bidding
        # on everyone else as soon as one vendor answered). The buyer can
        # still close early with the PO's "Bid Received" button.
        if vals.get('state') == 'submitted':
            self.rfq_id._check_all_bids_received()

        return res

class VendorQuoteLine(models.Model):
    _name = 'vendor.quote.line'
    _description = 'Vendor Quotation Line'

    quote_id = fields.Many2one('vendor.quote', string="Quotation", ondelete='cascade')
    po_line_id = fields.Many2one('purchase.order.line', string="Purchase Order Line", ondelete='cascade')

    # Denormalized for the flat cross-vendor comparison grid (domain filtering
    # by PO, group-by product/vendor) - vendor_id/rfq_id on vendor.quote
    # itself require an extra join every list simply can't group by.
    vendor_id = fields.Many2one('res.partner', related='quote_id.vendor_id', store=True, string="Vendor")
    rfq_id = fields.Many2one('purchase.order', related='quote_id.rfq_id', store=True, string="RFQ")

    # Quote-level info, surfaced on the line so the vendor-grouped comparison
    # screen (action_compare_quotes) can show it once per vendor group
    # instead of only on a separate vendor.quote record. aggregator='avg' is
    # what makes rank/rating/total_amount actually render in a list's group
    # header row - the value is identical across every line for a given
    # vendor, so averaging just displays it (not a real average across
    # different numbers). quote_state has no sensible aggregation (Selection
    # field), so it only shows on the per-product detail rows, not the group
    # header - a native Odoo list-view constraint, not a bug.
    quote_rank = fields.Integer(related='quote_id.rank', string="Rank", aggregator='avg')
    quote_rating = fields.Float(related='quote_id.rating', string="Vendor Rating", aggregator='avg')
    quote_total_amount = fields.Float(related='quote_id.total_amount', string="Total Amount", aggregator='avg')
    quote_state = fields.Selection(related='quote_id.state', string="Vendor Status")

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

    # Computed, not stored: whether THIS vendor currently holds the award for
    # this product line. Deliberately non-stored - po_line_id.vendor_id is a
    # plain Many2one (single winner), so confirming a different vendor for
    # the same line simply overwrites it; every other quote line's
    # is_winning_line self-corrects the instant it's read, with no explicit
    # "unwin" bookkeeping and no stale-state class of bug possible.
    is_winning_line = fields.Boolean(string="Winning", compute='_compute_is_winning_line')

    @api.depends('vendor_price', 'product_qty')
    def _compute_price_subtotal(self):
        for line in self:
            line.price_subtotal = line.vendor_price * line.product_qty

    @api.depends('po_line_id.vendor_id', 'vendor_id')
    def _compute_is_winning_line(self):
        for line in self:
            line.is_winning_line = bool(line.po_line_id) and line.po_line_id.vendor_id == line.vendor_id

    def write(self, vals):
        if 'vendor_price' in vals:
            for line in self:
                rfq = line.quote_id.rfq_id
                if line.quote_id.state != 'draft' and (
                        rfq.state not in ('draft', 'sent')
                        or rfq.approval_status in ('submitted', 'approved')):
                    raise UserError(_(
                        "Bid prices can't be changed while %s is waiting for or has approval.",
                        rfq.name))
        res = super().write(vals)
        if 'vendor_price' in vals:
            for line in self.filtered('is_winning_line'):
                if line.po_line_id.price_unit != line.vendor_price:
                    line.po_line_id.price_unit = line.vendor_price
        return res

    def action_select_line(self):
        """Award this product line to this quote's vendor at this price."""
        rfqs = self.env['purchase.order']
        for line in self:
            if line.quote_id.rfq_id.is_rfq_expired:
                raise UserError(
                    _("This RFQ has expired. You can no longer confirm a vendor for it.")
                )
            if line.quote_id.rfq_id.comparison_state in ('draft', 'sent'):
                raise UserError(_("Mark bids as received before selecting vendors."))
            if line.quote_id.rfq_id.state not in ('draft', 'sent'):
                raise UserError(_("Purchase orders have already been created from this RFQ."))
            if line.quote_id.state == 'draft':
                raise UserError(_("%s has not submitted a bid.", line.vendor_id.name))
            if not line.po_line_id:
                continue
            line.po_line_id.write({
                'vendor_id': line.vendor_id.id,
                'price_unit': line.vendor_price,
                'winning_quote_line_id': line.id,
            })
            # Best-effort only: the PO header partner_id has no real meaning
            # once lines can go to different vendors - keep it pointed at
            # whichever vendor was most recently confirmed, purely as a
            # reference default (e.g. for portal/report display), never
            # relied on for picking/billing logic (that keys off
            # order_line.vendor_id instead).
            line.quote_id.rfq_id.partner_id = line.vendor_id
            rfqs |= line.quote_id.rfq_id
        rfqs._sync_quote_states()
        rfqs.filtered('comparison_confirmed').write({'comparison_confirmed': False})
        # No explicit action returned: the web client already re-reads every
        # field on the current record after any button call, which is
        # enough to refresh comparison_line_ids/is_winning_line/
        # vendor_finalized/Approval Status/Progress. Returning the
        # 'ir.actions.client'/'reload' tag (as this used to) forces a full
        # browser reload - it fixed the same staleness but also reset the
        # active notebook tab and scroll position on every click.

    def action_unselect_line(self):
        """Withdraw this vendor's award for this product line, if still theirs."""
        rfqs = self.env['purchase.order']
        for line in self:
            if line.quote_id.rfq_id.state not in ('draft', 'sent'):
                raise UserError(_("Purchase orders have already been created from this RFQ."))
            po_line = line.po_line_id
            if not po_line or po_line.vendor_id != line.vendor_id:
                raise UserError(
                    _("This line has already been reassigned to a different vendor.")
                )
            po_line.write({'vendor_id': False, 'winning_quote_line_id': False})
            rfqs |= line.quote_id.rfq_id
        rfqs._sync_quote_states()
        rfqs.filtered('comparison_confirmed').write({'comparison_confirmed': False})
