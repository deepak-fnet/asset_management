from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from .vendor_compat_utils import group_members, group_member_emails

class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # RFQ/PO numbering: a purchase order is created as an RFQ, and only
            # gets its PO number once actually approved (see button_approve
            # below) - until then it must not carry the PO sequence.
            if vals.get('name', 'New') in (False, 'New'):
                vals['name'] = self.env['ir.sequence'].sudo().next_by_code(
                    'vendor_management.purchase.rfq') or 'New'
            if not vals.get('partner_id') and vals.get('vendor_ids'):
                vendors = vals.get('vendor_ids')
                partner_id = False
                if isinstance(vendors, (list, tuple)):
                    for cmd in vendors:
                        if isinstance(cmd, (list, tuple)):
                            if cmd[0] == 6 and cmd[2]:
                                partner_id = cmd[2][0]
                                break
                            elif cmd[0] == 4:
                                partner_id = cmd[1]
                                break
                if partner_id:
                    vals['partner_id'] = partner_id

        pos = super().create(vals_list)

        # Task 7: Send PO created email
        template = self.env.ref('vendor_management.mail_template_po_created', raise_if_not_found=False)
        if template:
            for po in pos:
                if po.partner_id.email:
                    po._send_po_notification(template)
        return pos

    def _send_po_notification(self, template):
        self.ensure_one()
        md_group = self.env.ref('vendor_management.group_vendor_md')
        email_cc = group_member_emails(md_group)
        template.send_mail(self.id, force_send=True, email_values={'email_cc': email_cc})

    def write(self, vals):
        """Override write - vendor assignment moved to vendor_quote.action_select_line()"""
        # Feature: RFQ Revision Handling
        # Meaningful fields to track for revision
        meaningful_fields = ['order_line', 'product_id', 'product_qty', 'price_unit']
        if any(field in vals for field in meaningful_fields):
            # Avoid incrementing if already explicitly setting revision (e.g. from elsewhere)
            # and only if we are in RFQ stage (draft or sent)
            if 'rfq_revision' not in vals:
                for rec in self:
                    if rec.state in ('draft', 'sent'):
                        vals['rfq_revision'] = rec.rfq_revision + 1
                        vals['rfq_last_update'] = fields.Datetime.now()
                        break # One increment is enough for the whole set if multiple records

        res = super().write(vals)
        # No automatic partner_id assignment - handled by vendor selection flow
        return res

    @api.constrains('partner_id', 'vendor_ids')
    def _check_blocked_vendors(self):
        for rec in self:
            if rec.partner_id.vendor_state == 'blocked':
                raise ValidationError(_("Vendor %s is blocked and cannot be used.", rec.partner_id.name))
            for vendor in rec.vendor_ids:
                if vendor.vendor_state == 'blocked':
                    raise ValidationError(_("Vendor %s is blocked and cannot be used.", vendor.name))

    # ------------------------------------------------------------------
    # RFQ -> one purchase order per vendor
    #
    # Same model for both: the RFQ is the purchase.order the user created
    # (bids, comparison, approval all live on it) and is never confirmed
    # itself. "Confirm Order" on it creates one child purchase.order per
    # awarded vendor - single vendor, standard Odoo from there on (receipt,
    # bill) - and moves the RFQ to 'ordered'.
    # ------------------------------------------------------------------
    state = fields.Selection(
        selection_add=[('ordered', 'Ordered')],
        ondelete={'ordered': 'set default'},
    )
    rfq_id = fields.Many2one(
        'purchase.order', string="Source RFQ", readonly=True, copy=False, index=True,
        help="The RFQ this purchase order was created from on Confirm Order.")
    child_po_ids = fields.One2many('purchase.order', 'rfq_id', string="Purchase Orders")
    child_po_count = fields.Integer(compute='_compute_child_po_count')
    bill_ready = fields.Boolean(
        compute='_compute_bill_ready',
        help="Something has been received that is not billed yet.")

    @api.depends('child_po_ids')
    def _compute_child_po_count(self):
        for rec in self:
            rec.child_po_count = len(rec.child_po_ids)

    @api.depends('order_line.qty_received', 'order_line.qty_invoiced', 'state')
    def _compute_bill_ready(self):
        for rec in self:
            rec.bill_ready = rec.state == 'purchase' and any(
                line.qty_received > line.qty_invoiced
                for line in rec.order_line.filtered(lambda l: not l.display_type))

    def button_confirm(self):
        rfqs = self.filtered(lambda po: not po.rfq_id and po.state in ('draft', 'sent'))
        orders = self - rfqs
        if rfqs:
            rfqs._split_into_vendor_orders()
        if not orders:
            return True

        # Child (or legacy) single-vendor orders: standard confirm. The
        # vendor_finalized check still applies to any order confirmed this
        # way - a child always has its vendor set on every line.
        for rec in orders:
            if not rec.vendor_finalized:
                unresolved = rec.order_line.filtered(lambda l: not l.display_type and not l.vendor_id)
                raise ValidationError(
                    _("You must confirm a vendor for every line before confirming the Purchase Order. "
                      "Missing a vendor for: %s", ', '.join(unresolved.mapped('product_id.display_name')) or _('(all lines)'))
                )

        res = super(PurchaseOrder, orders).button_confirm()
        for rec in orders:
            threshold = float(self.env['ir.config_parameter'].sudo().get_param('vendor_management.transaction_review_threshold', 1000.0))
            if rec.amount_total > threshold:
                rec._trigger_transaction_review()
        return res

    def _split_into_vendor_orders(self):
        """Confirm Order on an RFQ: one confirmed purchase order per awarded
        vendor, carrying only that vendor's lines at the awarded price."""
        for rfq in self:
            if rfq.comparison_state in ('draft', 'sent'):
                raise UserError(_("Bids must be received and vendors selected before confirming."))
            if not rfq.vendor_finalized:
                unresolved = rfq.order_line.filtered(lambda l: not l.display_type and not l.vendor_id)
                raise ValidationError(_(
                    "Select a vendor for every product before confirming. Missing a vendor for: %s",
                    ', '.join(unresolved.mapped('product_id.display_name')) or _('(all lines)')))
            rfq._check_approval_before_confirm()

            lines_by_vendor = {}
            for line in rfq.order_line.filtered(lambda l: not l.display_type):
                lines_by_vendor.setdefault(line.vendor_id, self.env['purchase.order.line'])
                lines_by_vendor[line.vendor_id] |= line

            children = self.env['purchase.order']
            for vendor, lines in lines_by_vendor.items():
                children |= rfq._create_vendor_order(vendor, lines)

            rfq.write({'state': 'ordered', 'comparison_state': 'po_created'})
            # Confirm after the RFQ has left draft/sent: children are
            # ordinary orders from here on (picking, PO numbering, review).
            children.button_confirm()
            rfq.message_post(body=_(
                "Purchase orders created: %s",
                ", ".join("%s (%s)" % (c.name, c.partner_id.name) for c in children)))

    def _create_vendor_order(self, vendor, lines):
        self.ensure_one()
        order_vals = {
            'name': self.env['ir.sequence'].sudo().next_by_code('vendor_management.purchase.po') or _('New'),
            'rfq_id': self.id,
            'partner_id': vendor.id,
            'origin': self.name,
            'partner_ref': self.partner_ref,
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,
            'user_id': self.user_id.id,
            'picking_type_id': self.picking_type_id.id,
            'dest_address_id': self.dest_address_id.id,
            'payment_term_id': self.payment_term_id.id,
            'incoterm_id': self.incoterm_id.id,
            'note': self.note,
            'comparison_state': 'po_created',
            'order_line': [],
        }
        if 'asset_request_id' in self._fields:
            order_vals['asset_request_id'] = self.asset_request_id.id
        if 'terms_template_id' in self._fields:
            order_vals['terms_template_id'] = self.terms_template_id.id
            order_vals['terms_condition_line_ids'] = [
                (0, 0, {'term_id': t.term_id.id, 'value_id': t.value_id.id})
                for t in self.terms_condition_line_ids]
        for line in lines:
            # copy_data: carries every copyable line field, including ones
            # other modules add (fixed-asset flags, GST option, analytics)
            # without this module having to know about them.
            line_vals = line.copy_data({
                'order_id': False,
                'vendor_id': vendor.id,
                'winning_quote_line_id': line.winning_quote_line_id.id,
                'price_unit': line.price_unit,
                'product_qty': line.product_qty,
            })[0]
            line_vals.pop('order_id', None)
            order_vals['order_line'].append((0, 0, line_vals))
        return self.with_context(purchase_approval_skip_default=True).create(order_vals)

    def button_approve(self, force=False):
        # RFQ/PO numbering: this is the single place native Odoo actually
        # flips the order to state='purchase' - whether that happens via a
        # direct button_confirm() (amount under the approval threshold) or
        # later, manually, on an order left at 'to approve'. Hooking here
        # (rather than in button_confirm) covers both paths without
        # duplicating the rename. Orders split from an RFQ were already
        # given their PO number at creation.
        res = super().button_approve(force=force)
        for rec in self:
            if rec.state == 'purchase' and not rec.rfq_id:
                rec.name = self.env['ir.sequence'].sudo().next_by_code(
                    'vendor_management.purchase.po') or rec.name
        return res

    def action_view_child_pos(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('purchase.purchase_form_action')
        action.update({
            'name': _('Purchase Orders - %s', self.name),
            'domain': [('rfq_id', '=', self.id)],
            'context': {'create': False},
        })
        if self.child_po_count == 1:
            action.update({'view_mode': 'form', 'views': [(False, 'form')], 'res_id': self.child_po_ids.id})
        return action

    def action_open_source_rfq(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'res_id': self.rfq_id.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
        }

    def action_upload_bill(self):
        """Child order's Upload Bill: Odoo's own bill creation (bills what
        has been received and not yet billed), opening the draft bill so the
        document can be attached. The bill then waits for Stores Approve ->
        Finance Confirm (see account_move.py)."""
        self.ensure_one()
        if not self.bill_ready:
            raise UserError(_("Nothing has been received that is not already billed."))
        return self.action_create_invoice()

    # ------------------------------------------------------------------
    # Price Comparison dashboard (replaces core's plain purchase-history
    # list): per product, every vendor's confirmed purchases and submitted
    # bids, price trend, on-time delivery and rating.
    # ------------------------------------------------------------------
    @api.depends('order_line', 'order_line.product_id')
    def _compute_show_comparison(self):
        super()._compute_show_comparison()
        # Core only shows the button when other confirmed orders exist; bids
        # on this or earlier RFQs are just as much a price comparison.
        QuoteLine = self.env['vendor.quote.line']
        for rec in self.filtered(lambda r: not r.show_comparison):
            rec.show_comparison = bool(QuoteLine.search_count([
                ('product_id', 'in', rec.order_line.product_id.ids),
                ('quote_id.state', '!=', 'draft'),
                ('vendor_price', '>', 0),
            ], limit=1))

    def action_purchase_comparison(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'vendor_management.price_comparison_dashboard',
            'name': _('Price Comparison - %s', self.name),
            'context': {'active_id': self.id},
        }

    def get_price_comparison_data(self):
        self.ensure_one()
        products = self.order_line.filtered(lambda l: not l.display_type and l.product_id).product_id
        # Confirmed purchases only - drafts are not prices anyone paid, and
        # a split RFQ ('ordered') would duplicate its own child orders.
        # A zero unit price is not a price anyone was quoted (free sample,
        # price never filled in) - it would always "win" lowest price.
        order_lines = self.env['purchase.order.line'].sudo().search([
            ('product_id', 'in', products.ids),
            ('display_type', '=', False),
            ('order_id.state', 'in', ('purchase', 'done')),
            ('price_unit', '>', 0),
        ])
        bid_lines = self.env['vendor.quote.line'].sudo().search([
            ('product_id', 'in', products.ids),
            ('quote_id.state', '!=', 'draft'),
            ('vendor_price', '>', 0),
        ])

        vendors = (order_lines.order_id.partner_id | bid_lines.vendor_id).sorted('id')
        # Identity colour follows the vendor, never its price rank (fixed
        # order by id); past 8 a vendor gets the neutral accent, not a new hue.
        color_slot = {v.id: i if i < 8 else -1 for i, v in enumerate(vendors)}
        performance = {v.id: self._vendor_delivery_and_rating(v) for v in vendors}

        result = []
        for product in products:
            entries = []
            for line in order_lines.filtered(lambda l: l.product_id == product):
                entries.append({
                    'vendor_id': line.order_id.partner_id.id,
                    'date': fields.Date.to_string((line.order_id.date_approve or line.order_id.date_order).date()),
                    'ref': line.order_id.name,
                    'kind': 'order',
                    'qty': line.product_qty,
                    'price': line.price_unit,
                })
            for bid in bid_lines.filtered(lambda b: b.product_id == product):
                quote = bid.quote_id
                when = quote.last_submitted_at.date() if quote.last_submitted_at else quote.quote_date
                entries.append({
                    'vendor_id': bid.vendor_id.id,
                    'date': fields.Date.to_string(when) if when else '',
                    'ref': quote.rfq_id.name,
                    'kind': 'bid',
                    'qty': bid.product_qty,
                    'price': bid.vendor_price,
                })
            entries.sort(key=lambda e: e['date'] or '')

            orders = [e for e in entries if e['kind'] == 'order']
            ordered_qty = sum(e['qty'] for e in orders)
            if ordered_qty:
                average = sum(e['price'] * e['qty'] for e in orders) / ordered_qty
            else:
                prices = [e['price'] for e in entries]
                average = sum(prices) / len(prices) if prices else 0.0

            vendor_rows = []
            for vendor in vendors:
                mine = [e for e in entries if e['vendor_id'] == vendor.id]
                if not mine:
                    continue
                prices = [e['price'] for e in mine]
                vendor_rows.append({
                    'id': vendor.id,
                    'name': vendor.name,
                    'color_slot': color_slot[vendor.id],
                    'min': min(prices),
                    'max': max(prices),
                    'last': mine[-1]['price'],
                    'order_count': len({e['ref'] for e in mine if e['kind'] == 'order'}),
                    'bid_count': len([e for e in mine if e['kind'] == 'bid']),
                    'history': list(reversed(mine)),  # newest first for the table
                    **performance[vendor.id],
                })
            best = min((v['min'] for v in vendor_rows), default=None)
            for row in vendor_rows:
                row['best'] = len(vendor_rows) > 1 and row['min'] == best
            vendor_rows.sort(key=lambda v: v['min'])

            this_line = self.order_line.filtered(lambda l: l.product_id == product)[:1]
            result.append({
                'product_id': product.id,
                'name': product.display_name,
                'has_image': bool(product.image_128),
                'uom': this_line.product_uom_id.name or product.uom_id.name,
                'qty_requested': sum(self.order_line.filtered(lambda l: l.product_id == product).mapped('product_qty')),
                'lowest': min((e['price'] for e in entries), default=0.0),
                'average': average,
                'total_qty': ordered_qty,
                'order_count': len({e['ref'] for e in orders}),
                'vendors': vendor_rows,
            })
        return {
            'po_name': self.name,
            'currency_symbol': self.currency_id.symbol or '',
            'currency_position': self.currency_id.position or 'before',
            'products': result,
        }

    def _vendor_delivery_and_rating(self, vendor):
        """On-time % from this vendor's actual completed receipts (done on
        or before the scheduled date), and the vendor's performance score
        (0-100) from its ratings. None when there is nothing to measure yet."""
        receipts = self.env['stock.picking'].sudo().search([
            ('partner_id', '=', vendor.id),
            ('picking_type_code', '=', 'incoming'),
            ('state', '=', 'done'),
            ('date_done', '!=', False),
        ])
        on_time = len(receipts.filtered(
            lambda p: not p.scheduled_date or p.date_done.date() <= p.scheduled_date.date()))
        rating = vendor.sudo().performance_overview_score
        return {
            'on_time_pct': round(on_time * 100.0 / len(receipts)) if receipts else None,
            'receipt_count': len(receipts),
            'rating': round(rating) if rating else None,
        }

    def _trigger_transaction_review(self):
        self.ensure_one()
        # Create activity for Manager and MD
        groups = [
            'vendor_management.group_vendor_manager',
            'vendor_management.group_vendor_md'
        ]
        for group_xmlid in groups:
            group = self.env.ref(group_xmlid)
            for user in group_members(group):
                self.activity_schedule(
                    'mail.mail_activity_data_todo',
                    summary=_('Vendor Review Required: %s', self.partner_id.name),
                    note=_('Purchase Order %s exceeded threshold. Please review vendor performance.', self.name),
                    user_id=user.id
                )

        # Task 7: Send Transaction Review Triggered Email
        template = self.env.ref('vendor_management.mail_template_transaction_review', raise_if_not_found=False)
        if template:
            md_group = self.env.ref('vendor_management.group_vendor_md')
            email_to = group_member_emails(md_group)
            if email_to:
                template.send_mail(self.id, force_send=True, email_values={'email_to': email_to})

    # Relaxed from core's required=True: partner_id is no longer a real user
    # input in this workflow (vendor_ids is), just a best-effort reference
    # kept in sync from whichever vendor was most recently confirmed on a
    # line (see vendor.quote.line.action_select_line). It's always hidden in
    # the view (see purchase_order_views.xml) - required=True there would be
    # a save-blocking trap on a field nobody can see or fill in.
    partner_id = fields.Many2one(required=False)

    vendor_ids = fields.Many2many(
        'res.partner',
        'purchase_order_vendor_rel',
        'purchase_id',
        'partner_id',
        string="Selected Vendors",
        domain="[('is_vendor', '=', True)]"
    )

    quote_ids = fields.One2many('vendor.quote', 'rfq_id', string="Vendor Quotations")

    # Per-line vendor comparison grid: a flat, read-only view over every
    # invited vendor's quote lines for this RFQ (one row per product per
    # vendor), used by the "Vendor Comparison" page instead of the old
    # whole-quote list. Confirming a row (vendor.quote.line.action_select_line)
    # writes straight onto that product's own order_line.vendor_id.
    comparison_line_ids = fields.One2many(
        'vendor.quote.line', compute='_compute_comparison_line_ids', string="Comparison Lines")

    comparison_state = fields.Selection([
        ('draft', 'Draft'),
        ('sent', 'Sent to Vendors'),
        ('received', 'Quotes Received'),
        ('review', 'Manager Review'),
        ('approved', 'MD Approved'),
        ('po_created', 'PO Created'),
    ], default='draft', string="Quotation Status", tracking=True)

    # Feature 8: RFQ expiry date (optional - infinite if empty)
    expiry_date = fields.Date(
        string="RFQ Expiry Date",
        help="Optional expiry date for this RFQ. If empty, the RFQ remains valid indefinitely."
    )

    # Bill Reference: kept for backward compatibility with existing data.
    # No longer mandatory on confirm - vendor bills are per-vendor, created
    # after receipt (see vendor.bill), not a single PO-level reference
    # required up front.
    bill_reference = fields.Char(
        string="Bill Reference",
        help="Optional free-text billing reference."
    )

    # True iff every non-display order line already has a confirmed winning
    # vendor (order_line.vendor_id). Replaces the old manually-set flag -
    # there is no longer a single PO-wide "the vendor" decision to toggle,
    # since different lines can be awarded to different vendors.
    vendor_finalized = fields.Boolean(
        string="Vendor Finalized", compute='_compute_vendor_finalized', store=True,
        help="True once every order line has a confirmed vendor."
    )

    is_rfq_expired = fields.Boolean(
        compute='_compute_is_rfq_expired',
        string="RFQ Expired",
        help="True if RFQ expiry date has passed"
    )

    is_rated = fields.Boolean(compute='_compute_is_rated', store=False)

    rfq_revision = fields.Integer(string="RFQ Revision", default=0, tracking=True)
    rfq_last_update = fields.Datetime(string="RFQ Last Update")


    @api.depends('order_line.vendor_id', 'order_line.display_type')
    def _compute_vendor_finalized(self):
        for rec in self:
            lines = rec.order_line.filtered(lambda l: not l.display_type)
            # NOT lines.mapped('vendor_id'): mapped() on a Many2one drops
            # empty values instead of keeping them as falsy entries, so
            # all(lines.mapped(...)) is a tautology - True even when every
            # line lacks a vendor. Must check each line directly.
            rec.vendor_finalized = bool(lines) and all(line.vendor_id for line in lines)

    def _sync_quote_states(self):
        """Keep vendor.quote.state - the portal's "Congratulations, selected" /
        "Not selected" banner - in step with the per-line award outcome.
        Only touches quotes a vendor actually submitted (never flips a
        never-answered 'draft' quote to rejected), and reverts to
        'submitted' if a later unselect makes the order no longer fully
        decided (so nobody is shown a final answer prematurely)."""
        for rec in self:
            if rec.vendor_finalized:
                for quote in rec.quote_ids.filtered(lambda q: q.state in ('submitted', 'selected', 'rejected')):
                    new_state = 'rejected' if quote.line_result == 'none' else 'selected'
                    if quote.state != new_state:
                        quote.state = new_state
            else:
                decided = rec.quote_ids.filtered(lambda q: q.state in ('selected', 'rejected'))
                if decided:
                    decided.write({'state': 'submitted'})

    @api.depends('quote_ids.line_ids.vendor_price', 'quote_ids.line_ids.po_line_id.vendor_id',
                 'quote_ids.state')
    def _compute_comparison_line_ids(self):
        # Only bids a vendor actually submitted (via portal or entered on
        # their behalf) - a 'draft' quote is just the placeholder created
        # when the RFQ was sent, still holding default prices, and must not
        # be comparable/awardable as if it were a real bid.
        for rec in self:
            rec.comparison_line_ids = rec.quote_ids.filtered(
                lambda q: q.state != 'draft').line_ids

    bid_progress = fields.Char(string="Bids", compute='_compute_bid_progress')

    @api.depends('quote_ids.state')
    def _compute_bid_progress(self):
        for rec in self:
            total = len(rec.quote_ids)
            done = len(rec.quote_ids.filtered(lambda q: q.state != 'draft'))
            rec.bid_progress = _("%(done)s / %(total)s received", done=done, total=total) if total else ""

    def action_mark_bid_received(self):
        """Close bidding manually - e.g. when a vendor has not responded and
        the buyer decides to proceed with the bids already in. At least one
        bid must exist, otherwise there is nothing to compare or award."""
        for rec in self:
            if rec.comparison_state != 'sent':
                raise UserError(_("Bids can only be marked received while the RFQ is out to vendors."))
            submitted = rec.quote_ids.filtered(lambda q: q.state != 'draft')
            if not submitted:
                raise UserError(_(
                    "No vendor has submitted a bid yet. Enter a bid on a vendor's "
                    "behalf from the Vendor Bids tab, or wait for one to submit."))
            pending = rec.quote_ids - submitted
            rec.comparison_state = 'received'
            if pending:
                rec.message_post(body=_(
                    "Bids marked received manually. No bid from: %s.",
                    ", ".join(pending.vendor_id.mapped('name'))))
            else:
                rec.message_post(body=_("Bids marked received manually."))

    def _check_all_bids_received(self):
        """Auto-close bidding once every invited vendor has submitted."""
        for rec in self:
            if (rec.comparison_state == 'sent' and rec.quote_ids
                    and all(q.state != 'draft' for q in rec.quote_ids)):
                rec.comparison_state = 'received'
                rec.message_post(body=_("All vendors have submitted their bids - bids received."))

    def action_submit_for_approval(self):
        """Approval only after bidding is closed and every product has been
        awarded: the approval amount is the awarded total, and once an order
        is submitted its line prices/vendors are locked (purchase_approval's
        write guard), so awarding afterwards would be impossible anyway."""
        for rec in self:
            if rec.comparison_state in ('draft', 'sent'):
                raise UserError(_("Bids must be received before submitting for approval."))
            if not rec.vendor_finalized:
                raise UserError(_(
                    "Select a vendor for every product in Vendor Comparison "
                    "before submitting for approval."))
        return super().action_submit_for_approval()

    @api.depends('expiry_date')
    def _compute_is_rfq_expired(self):
        """Check if RFQ has expired based on expiry_date"""
        today = fields.Date.today()
        for rec in self:
            rec.is_rfq_expired = bool(rec.expiry_date and rec.expiry_date < today)

    def _compute_is_rated(self):
        for rec in self:
            rec.is_rated = self.env['vendor.performance.overview'].search_count([('purchase_id', '=', rec.id)]) > 0

    def action_send_to_vendors(self):
        """Notify all selected vendors about the RFQ."""
        for rec in self:
            if not rec.vendor_ids:
                continue

            for vendor in rec.vendor_ids:
                # Create a draft quote for each vendor
                if not self.env['vendor.quote'].search([('rfq_id', '=', rec.id), ('vendor_id', '=', vendor.id)]):
                    quote = self.env['vendor.quote'].create({
                        'rfq_id': rec.id,
                        'vendor_id': vendor.id,
                        'state': 'draft',
                    })
                    # Create lines based on RFQ lines
                    for line in rec.order_line:
                        # Feature 2: Default price fallback to product standard_price
                        default_price = line.product_id.standard_price if line.product_id else 0.0

                        self.env['vendor.quote.line'].create({
                            'quote_id': quote.id,
                            'po_line_id': line.id,
                            'internal_price': line.price_unit,  # Pass internal price for portal display
                            'vendor_price': default_price,  # Feature 2: Default to standard price
                        })

                # Send Invitation Email
                template = self.env.ref('vendor_management.mail_template_rfq_invitation', raise_if_not_found=False)
                if template and vendor.email:
                    # email_to alone is not enough: without recipient_ids the
                    # generated mail.mail still attributes itself to the RFQ's
                    # own partner_id (fixed to whichever vendor got picked as
                    # the default at creation), so every invitation - no
                    # matter which vendor's inbox it actually reaches - shows
                    # up in the portal/chatter as addressed to that ONE
                    # vendor. Each loop iteration needs its own recipient.
                    template.send_mail(rec.id, force_send=True, email_values={
                        'email_to': vendor.email,
                        'recipient_ids': [(6, 0, [vendor.id])],
                    })

            rec.comparison_state = 'sent'

    def action_compare_quotes(self):
        """Open the Vendor Comparison grid: products as rows, invited
        vendors as columns, each cell a checkbox (award this vendor this
        product) plus an editable price - one screen to either award one
        vendor everything (the column header checkbox) or mix per product
        (the cell checkbox), instead of scrolling a flat vendor-grouped
        list. A client action, not a window action: no standard Odoo view
        renders an editable product x vendor matrix, so this is a custom
        Owl component (see static/src/js/vendor_comparison_grid.js)."""
        self.ensure_one()
        self._compute_quote_ranks()
        return {
            'type': 'ir.actions.client',
            'tag': 'vendor_management.vendor_comparison_grid',
            'name': _('Vendor Comparison - %s', self.name),
            'context': {'active_id': self.id},
        }

    def get_comparison_grid_data(self):
        """Everything the Vendor Comparison grid needs, in one RPC - every
        invited vendor as a column, every real (non-section/note) order
        line as a row, and that row's quote-line cell per vendor (price +
        whether it currently holds the award), if that vendor actually
        quoted this product."""
        self.ensure_one()
        vendors = self.quote_ids.filtered(lambda q: q.state != 'draft').vendor_id
        lines = self.order_line.filtered(lambda l: not l.display_type)
        state_label = dict(self._fields['state']._description_selection(self.env)).get(self.state, '')
        return {
            'po_name': self.name,
            'po_state': state_label,
            # Read-only once purchase orders were created from this RFQ.
            'locked': self.state not in ('draft', 'sent'),
            'currency_symbol': self.currency_id.symbol or '',
            'currency_position': self.currency_id.position or 'before',
            'vendors': [{
                'id': v.id,
                'name': v.name,
                'subtitle': v.parent_id.name or v.email or '',
            } for v in vendors],
            'products': [{
                'line_id': line.id,
                'product_id': line.product_id.id,
                'product_name': line.product_id.display_name,
                'qty': line.product_qty,
                'uom': line.product_uom_id.name,
                'cells': [{
                    'quote_line_id': ql.id,
                    'vendor_id': ql.vendor_id.id,
                    'price': ql.vendor_price,
                    'is_winning': ql.is_winning_line,
                } for ql in self.comparison_line_ids.filtered(lambda q: q.po_line_id == line)],
            } for line in lines],
        }

    def action_award_all_to_vendor(self, vendor_id):
        """Column-header checkbox: award every product on this RFQ that
        this vendor actually quoted to them in one go, instead of ticking
        each row - "select one vendor for all" in the comparison grid.
        Products this vendor never quoted are left untouched (there is no
        price to award), so a partial vendor list still works correctly."""
        self.ensure_one()
        lines = self.comparison_line_ids.filtered(lambda ql: ql.vendor_id.id == vendor_id)
        lines.action_select_line()

    def action_unaward_all_from_vendor(self, vendor_id):
        """Column-header checkbox unticked: withdraw every award this vendor
        currently holds on this RFQ. Lines already reassigned to someone
        else are skipped rather than raising."""
        self.ensure_one()
        lines = self.comparison_line_ids.filtered(
            lambda ql: ql.vendor_id.id == vendor_id and ql.is_winning_line)
        lines.action_unselect_line()

    def _compute_quote_ranks(self):
        for rec in self:
            quotes = rec.quote_ids.filtered(lambda q: q.state in ('submitted', 'selected'))
            if not quotes:
                continue

            # Simple ranking by total_amount
            sorted_quotes = quotes.sorted(key=lambda q: q.total_amount)
            for i, quote in enumerate(sorted_quotes):
                quote.rank = i + 1

    def action_open_rating_wizard(self):
        self.ensure_one()
        return {
            'name': _('Rate Vendor'),
            'type': 'ir.actions.act_window',
            'res_model': 'vendor.rating.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_purchase_id': self.id,
            }
        }

class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    vendor_id = fields.Many2one(
        'res.partner', string="Confirmed Vendor", copy=False,
        domain="[('is_vendor', '=', True)]",
        help="The vendor awarded this specific line, confirmed from the comparison grid.")
    winning_quote_line_id = fields.Many2one(
        'vendor.quote.line', string="Winning Quote Line", copy=False,
        help="Audit trail: which vendor quote line this line's price/vendor came from.")
