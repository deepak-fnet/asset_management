from collections import defaultdict

from odoo import models, fields, api, _, SUPERUSER_ID
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

    def button_confirm(self):
        # Enforce that every order line has a confirmed winning vendor before
        # the PO can be confirmed. vendor_finalized (computed, see below) is
        # true iff every non-display line already has order_line.vendor_id set.
        for rec in self:
            if not rec.vendor_finalized:
                unresolved = rec.order_line.filtered(lambda l: not l.display_type and not l.vendor_id)
                raise ValidationError(
                    _("You must confirm a vendor for every line before confirming the Purchase Order. "
                      "Missing a vendor for: %s", ', '.join(unresolved.mapped('product_id.display_name')) or _('(all lines)'))
                )

        res = super().button_confirm()
        for rec in self:
            threshold = float(self.env['ir.config_parameter'].sudo().get_param('vendor_management.transaction_review_threshold', 1000.0))
            if rec.amount_total > threshold:
                rec._trigger_transaction_review()
        return res

    def button_approve(self, force=False):
        # RFQ/PO numbering: this is the single place native Odoo actually
        # flips the order to state='purchase' - whether that happens via a
        # direct button_confirm() (amount under the approval threshold) or
        # later, manually, on an order left at 'to approve'. Hooking here
        # (rather than in button_confirm) covers both paths without
        # duplicating the rename.
        res = super().button_approve(force=force)
        for rec in self:
            if rec.state == 'purchase':
                rec.name = self.env['ir.sequence'].sudo().next_by_code(
                    'vendor_management.purchase.po') or rec.name
        return res

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

    vendor_bill_ids = fields.One2many('vendor.bill', 'order_id', string="Vendor Bills")

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

    @api.depends('quote_ids.line_ids.vendor_price', 'quote_ids.line_ids.po_line_id.vendor_id')
    def _compute_comparison_line_ids(self):
        for rec in self:
            rec.comparison_line_ids = rec.quote_ids.line_ids

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
        """Open a dedicated, vendor-grouped comparison screen for this RFQ:
        expand a vendor's group to see just their product lines and
        Confirm/Unselect them (same action_select_line/action_unselect_line
        as the flat grid embedded on the PO form's own Vendor Comparison
        tab - this is just a focused, grouped way into the same data)."""
        self.ensure_one()
        self._compute_quote_ranks()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'vendor_management.action_vendor_quote_line_comparison')
        action['name'] = _('Vendor Comparison - %s', self.name)
        action['domain'] = [('rfq_id', '=', self.id)]
        return action

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

    # ------------------------------------------------------------------
    # Per-vendor receiving: core purchase_stock builds exactly one picking
    # per order, keyed off order.partner_id. Once a PO has more than one
    # distinct order_line.vendor_id, that no longer makes sense - split into
    # one picking per vendor instead. Single-vendor orders (including every
    # pre-existing one) fall straight through to super() unchanged.
    # ------------------------------------------------------------------

    def _get_vendor_line_groups(self):
        self.ensure_one()
        groups = defaultdict(lambda: self.env['purchase.order.line'])
        for line in self.order_line.filtered(lambda l: not l.display_type):
            groups[line.vendor_id] += line
        return groups

    def _create_picking(self):
        multi_vendor_orders = self.filtered(
            lambda po: po.state == 'purchase' and len(
                set(po.order_line.filtered(lambda l: not l.display_type).mapped('vendor_id').ids) - {False}
            ) > 1
        )
        single_vendor_orders = self - multi_vendor_orders
        result = super(PurchaseOrder, single_vendor_orders)._create_picking()

        # single_vendor_orders (the common case - every line went to the
        # same vendor) fell straight through to core's own picking logic
        # above, which knows nothing about vendor.bill. Without this, a
        # single-vendor order never gets one at all - Upload Bill would
        # never have anything to open. Multi-vendor orders get theirs
        # inside the loop below instead, per vendor group.
        for order in single_vendor_orders.filtered(lambda po: po.state == 'purchase'):
            for vendor, lines in order._get_vendor_line_groups().items():
                if vendor and any(p.type == 'consu' for p in lines.product_id):
                    order._get_or_create_vendor_bill(vendor)

        StockPicking = self.env['stock.picking']
        for order in multi_vendor_orders:
            order = order.with_company(order.company_id)
            for vendor, lines in order._get_vendor_line_groups().items():
                if not vendor or not any(p.type == 'consu' for p in lines.product_id):
                    continue
                picking = order.picking_ids.filtered(
                    lambda x: x.partner_id == vendor and x.state not in ('done', 'cancel'))[:1]
                if not picking:
                    if not vendor.property_stock_supplier:
                        raise UserError(_("You must set a Vendor Location for %s", vendor.name))
                    vals = order._prepare_picking()
                    vals.update({'partner_id': vendor.id, 'location_id': vendor.property_stock_supplier.id})
                    picking = StockPicking.with_user(SUPERUSER_ID).create(vals)

                moves = lines._create_stock_moves(picking)
                moves = moves.filtered(lambda x: x.state not in ('done', 'cancel'))._action_confirm()
                seq = 0
                for move in sorted(moves, key=lambda m: m.date):
                    seq += 5
                    move.sequence = seq
                moves._action_assign()
                forward_pickings = self.env['stock.picking']._get_impacted_pickings(moves)
                (picking | forward_pickings).action_confirm()
                picking.message_post_with_source(
                    'mail.message_origin_link',
                    render_values={'self': picking, 'origin': order},
                    subtype_xmlid='mail.mt_note',
                )
                order._get_or_create_vendor_bill(vendor)
        return result

    def _get_or_create_vendor_bill(self, vendor):
        self.ensure_one()
        bill = self.vendor_bill_ids.filtered(lambda b: b.vendor_id == vendor)
        if not bill:
            bill = self.env['vendor.bill'].create({'order_id': self.id, 'vendor_id': vendor.id})
        return bill

class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    vendor_id = fields.Many2one(
        'res.partner', string="Confirmed Vendor", copy=False,
        domain="[('is_vendor', '=', True)]",
        help="The vendor awarded this specific line, confirmed from the comparison grid.")
    winning_quote_line_id = fields.Many2one(
        'vendor.quote.line', string="Winning Quote Line", copy=False,
        help="Audit trail: which vendor quote line this line's price/vendor came from.")

    # ------------------------------------------------------------------
    # Mirrors PurchaseOrder._create_picking above, but for the second entry
    # point core uses: a line added, or its qty changed, on an
    # already-confirmed order (see purchase_order_views.xml, which keeps
    # product_id editable post-confirm). Only lines whose order is genuinely
    # multi-vendor take the new path; everything else behaves exactly as
    # core does today.
    # ------------------------------------------------------------------
    def _create_or_update_picking(self):
        multi_vendor_lines = self.filtered(
            lambda l: l.vendor_id and len(
                set((l.order_id.order_line.filtered(lambda x: not x.display_type)).mapped('vendor_id').ids) - {False}
            ) > 1
        )
        single_vendor_lines = self - multi_vendor_lines
        super(PurchaseOrderLine, single_vendor_lines)._create_or_update_picking()

        # Same reasoning as PurchaseOrder._create_picking: a line added (or
        # re-quantified) post-confirm on what's still a single-vendor order
        # falls through to core's own logic above, which won't create a
        # vendor.bill. _get_or_create_vendor_bill is idempotent (a no-op if
        # one already exists for that vendor), so this is safe to call even
        # when nothing actually changed vendor-wise.
        for line in single_vendor_lines.filtered(lambda l: l.order_id.state == 'purchase' and l.vendor_id
                                                   and l.product_id.type == 'consu'):
            line.order_id._get_or_create_vendor_bill(line.vendor_id)

        for line in multi_vendor_lines:
            if not (line.product_id and line.product_id.type == 'consu'):
                continue

            vendor = line.vendor_id
            moves_to_assign = line.order_id.picking_ids.filtered(lambda p: p.partner_id == vendor).move_ids.filtered(
                lambda m: not m.purchase_line_id and line.product_id == m.product_id)
            moves_to_assign.purchase_line_id = line.id

            line_pickings = line.move_ids.picking_id.filtered(
                lambda p: p.partner_id == vendor and p.state not in ('done', 'cancel')
                and p.location_dest_id.usage in ('internal', 'transit', 'customer'))
            if line_pickings:
                picking = line_pickings[0]
            else:
                pickings = line.order_id.picking_ids.filtered(
                    lambda x: x.partner_id == vendor and x.state not in ('done', 'cancel')
                    and x.location_dest_id.usage in ('internal', 'transit', 'customer'))
                picking = pickings[:1]
                if not picking:
                    if not line.product_qty > line.qty_received:
                        continue
                    if not vendor.property_stock_supplier:
                        raise UserError(_("You must set a Vendor Location for %s", vendor.name))
                    vals = line.order_id._prepare_picking()
                    vals.update({'partner_id': vendor.id, 'location_id': vendor.property_stock_supplier.id})
                    picking = self.env['stock.picking'].create(vals)

            moves = line._create_stock_moves(picking)
            moves._action_confirm()._action_assign()
            line.order_id._get_or_create_vendor_bill(vendor)
