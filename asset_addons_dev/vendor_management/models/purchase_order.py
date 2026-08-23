from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from .vendor_compat_utils import group_members, group_member_emails

class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
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
        """Override write - vendor assignment moved to vendor_quote.action_select()"""
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

    def action_confirm(self):
        """Feature 1 & 11: Block confirmation without vendors or bill reference"""
        for order in self:
            # Feature 1: Validate vendors selected
            if not order.vendor_ids:
                raise ValidationError(
                    _("Cannot confirm RFQ/PO without selecting vendors. Please add at least one vendor.")
                )
            
            # Feature 11: Validate bill reference exists
            if not order.bill_reference:
                raise ValidationError(
                    _("Bill Reference is required before confirming the order.")
                )
        
        return super().action_confirm()
    
    def button_confirm(self):
        # Enforce vendor finalization before confirmation
        for rec in self:
            # Logic: If only ONE vendor is selected, allow direct confirmation
            # Skip validation and automatically sync partner_id
            if len(rec.vendor_ids) == 1:
                if not rec.partner_id:
                    rec.partner_id = rec.vendor_ids[0].id
                continue

            if not rec.vendor_finalized:
                raise ValidationError(
                    _("You must finalize a vendor before confirming the Purchase Order.")
                )
        
        res = super().button_confirm()
        for rec in self:
            threshold = float(self.env['ir.config_parameter'].sudo().get_param('vendor_management.transaction_review_threshold', 1000.0))
            if rec.amount_total > threshold:
                rec._trigger_transaction_review()
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

    vendor_ids = fields.Many2many(
        'res.partner',
        'purchase_order_vendor_rel',
        'purchase_id',
        'partner_id',
        string="Selected Vendors",
        domain="[('is_vendor', '=', True)]"
    )
    
    quote_ids = fields.One2many('vendor.quote', 'rfq_id', string="Vendor Quotations")
    
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
    
    # Feature 11: Bill Reference (mandatory on confirm)
    bill_reference = fields.Char(
        string="Bill Reference",
        help="Reference number for billing purposes. Required before confirmation."
    )
    
    # RFQ Vendor Finalization Control
    vendor_finalized = fields.Boolean(
        string="Vendor Finalized",
        default=False,
        copy=False,
        help="Indicates whether a vendor has been finalized for this RFQ"
    )
    
    is_rfq_expired = fields.Boolean(
        compute='_compute_is_rfq_expired',
        string="RFQ Expired",
        help="True if RFQ expiry date has passed"
    )

    is_rated = fields.Boolean(compute='_compute_is_rated', store=False)

    rfq_revision = fields.Integer(string="RFQ Revision", default=0, tracking=True)
    rfq_last_update = fields.Datetime(string="RFQ Last Update")
    
    @api.depends('expiry_date')
    def _compute_is_rfq_expired(self):
        """Check if RFQ has expired based on expiry_date"""
        today = fields.Date.today()
        for rec in self:
            rec.is_rfq_expired = bool(rec.expiry_date and rec.expiry_date < today)

    def _compute_is_rated(self):
        for rec in self:
            rec.is_rated = self.env['vendor.performance.overview'].search_count([('purchase_id', '=', rec.id)]) > 0

    def action_finalize_vendor(self):
        """Finalize vendor selection and convert RFQ to standard PO mode"""
        for rec in self:
            # Validate state
            if rec.state not in ('draft', 'sent'):
                raise ValidationError(
                    _("Vendor can only be finalized during RFQ stage (Draft or Sent).")
                )
            
            # Validate at least one vendor selected
            if not rec.vendor_ids:
                raise ValidationError(
                    _("You must select at least one vendor before finalizing. "
                      "Please add vendors to the 'Selected Vendors' field.")
                )
            
            # Choose the first vendor
            chosen_vendor = rec.vendor_ids[0]
            
            # Finalize: Set partner_id, keep only chosen vendor, set flag
            rec.write({
                'partner_id': chosen_vendor.id,
                'vendor_ids': [(6, 0, [chosen_vendor.id])],  # Keep only chosen vendor
                'vendor_finalized': True
            })

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
                    template.send_mail(rec.id, force_send=True, email_values={'email_to': vendor.email})
            
            rec.comparison_state = 'sent'

    def action_compare_quotes(self):
        """Open the comparison screen."""
        self.ensure_one()
        # Compute ranks before opening
        self._compute_quote_ranks()
        return {
            'name': 'RFQ Comparison',
            'type': 'ir.actions.act_window',
            'res_model': 'vendor.quote',
            'view_mode': 'list,form',
            'domain': [('rfq_id', '=', self.id)],
            'context': {'search_default_rfq_id': self.id},
        }

    def _compute_quote_ranks(self):
        for rec in self:
            quotes = rec.quote_ids.filtered(lambda q: q.state in ('submitted', 'selected'))
            if not quotes:
                continue
            
            # Simple ranking by total_amount
            sorted_quotes = quotes.sorted(key=lambda q: q.total_amount)
            for i, quote in enumerate(sorted_quotes):
                quote.rank = i + 1

    def action_manager_review_quotes(self):
        self.comparison_state = 'review'

    def action_approve_quotes(self):
        """MD approves quotes - selects lowest vendor and sends notification"""
        for order in self:
            # Find quote with lowest total amount
            submitted_quotes = order.quote_ids.filtered(lambda q: q.state == 'submitted' and q.total_amount > 0)
            if not submitted_quotes:
                continue
            
            # Get lowest priced quote
            lowest_quote = submitted_quotes.sorted(key=lambda q: q.total_amount)[0]
            
            # Mark as selected
            lowest_quote.write({'state': 'selected'})
            
            # Reject other quotes
            other_quotes = order.quote_ids.filtered(lambda q: q.id != lowest_quote.id)
            other_quotes.write({'state': 'rejected'})
            
            # Update purchase order with selected vendor
            order.write({
                'partner_id': lowest_quote.vendor_id.id,
                'vendor_ids': [(6, 0, [lowest_quote.vendor_id.id])],
                'comparison_state': 'approved',
            })
            
            # Send email notification to selected vendor
            template = self.env.ref('vendor_management.mail_template_vendor_selected', raise_if_not_found=False)
            if template and lowest_quote.vendor_id.email:
                ctx = {
                    'rfq_name': order.name,
                    'total_amount': order.amount_total,
                    'vendor_name': lowest_quote.vendor_id.name,
                }
                template.with_context(ctx).send_mail(lowest_quote.id, force_send=True, email_values={'email_to': lowest_quote.vendor_id.email})
        
        return True

    def action_create_final_po(self):
        """Select the best quote and create/confirm the PO."""
        self.ensure_one()
        selected_quote = self.quote_ids.filtered(lambda q: q.state == 'selected')
        if not selected_quote:
            # Pick rank 1 if none explicitly selected
            selected_quote = self.quote_ids.filtered(lambda q: q.rank == 1)
        
        if selected_quote:
            selected_quote = selected_quote[0]
            self.partner_id = selected_quote.vendor_id
            # Remove all other vendors from selected list (Part 3)
            self.vendor_ids = [(6, 0, [selected_quote.vendor_id.id])]
            
            # Reject/Lock other quotes
            other_quotes = self.quote_ids.filtered(lambda q: q.id != selected_quote.id)
            other_quotes.write({'state': 'rejected'})
            
            # Update prices and quantities from quote
            for line in self.order_line:
                quote_line = selected_quote.line_ids.filtered(lambda ql: ql.product_id == line.product_id)
                if quote_line:
                    line.write({
                        'price_unit': quote_line[0].vendor_price,
                        'product_qty': quote_line[0].product_qty,
                    })
            
            self.comparison_state = 'po_created'
            self.button_confirm()

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
    
    def action_select_vendor(self):
        """MD selects a vendor from comparison and locks it"""
        self.ensure_one()
        # This should be called from vendor.quote record
        # The actual selection happens in vendor.quote.action_select()
        return self.action_compare_quotes()
    
    # write() override removed - all vendor assignment moved to vendor_quote.action_select()

class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'
