from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError


class VendorBill(models.Model):
    _name = 'vendor.bill'
    _description = 'Vendor Bill (per-vendor, tied to one PO)'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    order_id = fields.Many2one('purchase.order', string="Purchase Order", required=True, ondelete='cascade', index=True)
    vendor_id = fields.Many2one('res.partner', string="Vendor", required=True, domain="[('is_vendor', '=', True)]")

    qty_ordered = fields.Float(string="Qty Ordered", compute='_compute_quantities')
    qty_received = fields.Float(string="Qty Received", compute='_compute_quantities')

    state = fields.Selection([
        ('to_receive', 'To Receive'),
        ('uploaded', 'Bill Uploaded'),
        ('submitted', 'Submitted'),
        ('stores_approved', 'Stores Approved'),
        ('finance_confirmed', 'Finance Confirmed'),
    ], default='to_receive', string="Status", tracking=True, copy=False)

    # The real vendor bill - base flow: Purchase User's Upload Bill creates
    # this as a draft immediately (see action_upload_bill), and every step
    # from there on works with the actual account.move via Odoo's own
    # standard bill form (attachment/OCR panel, native Bill Reference/Date),
    # not through custom staging fields on this model.
    move_id = fields.Many2one('account.move', string="Vendor Bill Entry", readonly=True, copy=False)
    move_state = fields.Selection(related='move_id.state', string="Bill Status")

    _sql_constraints = [
        ('order_vendor_uniq', 'unique(order_id, vendor_id)',
         'A vendor bill already exists for this vendor on this order.'),
    ]

    @api.depends('order_id.order_line.vendor_id', 'order_id.order_line.product_qty',
                 'order_id.order_line.qty_received', 'vendor_id')
    def _compute_quantities(self):
        for rec in self:
            lines = rec.order_id.order_line.filtered(
                lambda l: l.vendor_id == rec.vendor_id and not l.display_type)
            rec.qty_ordered = sum(lines.mapped('product_qty'))
            rec.qty_received = sum(lines.mapped('qty_received'))

    def action_view_receive(self):
        """Open this vendor's own receipt(s) - reuses core's picking Validate flow."""
        self.ensure_one()
        pickings = self.order_id.picking_ids.filtered(lambda p: p.partner_id == self.vendor_id)
        return self.order_id._get_action_view_picking(pickings)

    def _prepare_move_vals(self):
        """Build the draft vendor bill's create() vals from this vendor's
        matched, received order lines - reusing the exact primitives core's
        own PO-level "Create Bill" uses (_prepare_invoice/
        _prepare_account_move_line), just scoped to one vendor instead of
        the whole order."""
        self.ensure_one()
        order = self.order_id.with_company(self.order_id.company_id)
        lines = order.order_line.filtered(lambda l: l.vendor_id == self.vendor_id and not l.display_type)
        if not lines:
            raise UserError(_("No order lines found for vendor %s.", self.vendor_id.name))

        partner_invoice_id = self.vendor_id.address_get(['invoice'])['invoice']
        partner_bank_id = self.vendor_id.commercial_partner_id.bank_ids.filtered_domain(
            ['|', ('company_id', '=', False), ('company_id', '=', order.company_id.id)])[:1]

        invoice_vals = order._prepare_invoice()
        invoice_vals.update({
            'partner_id': partner_invoice_id,
            'partner_bank_id': partner_bank_id.id,
            'invoice_origin': order.name,
            'invoice_line_ids': [(0, 0, line._prepare_account_move_line()) for line in lines],
        })
        return invoice_vals

    def _action_open_move(self):
        """Standard bill form navigation - same action id Accounting > Bills
        itself uses, so this looks and behaves exactly like the base flow."""
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('account.action_move_in_invoice_type')
        action['res_id'] = self.move_id.id
        action['view_mode'] = 'form'
        action['views'] = [(False, 'form')]
        return action

    def action_upload_bill(self):
        """Purchase User: create the real draft vendor bill (if not already
        created) and open it in Odoo's own standard bill form - attach the
        actual document and fill in Bill Reference/Date there, using native
        fields, not a custom staging form. Idempotent: re-opening an
        already-uploaded bill just navigates to the existing move."""
        self.ensure_one()
        if self.qty_received <= 0:
            raise UserError(_("Nothing has been received yet for %s.", self.vendor_id.name))
        if not self.move_id:
            move = self.env['account.move'].sudo().with_context(
                default_move_type='in_invoice').with_company(self.order_id.company_id).create(
                self._prepare_move_vals())
            self.write({'move_id': move.id, 'state': 'uploaded'})
        return self._action_open_move()

    def action_submit_bill(self):
        """Purchase User: explicitly submit the uploaded bill forward for
        Stores' approval - separate from Upload Bill itself (bill
        creation/attachment) so the Purchase User can attach the document
        and fill in the bill's details on the standard form first, then
        submit only once it's actually ready."""
        if not self.env.user.has_group('vendor_management.group_purchase_role_user'):
            raise AccessError(_("Only a Purchase User can submit this bill."))
        for rec in self:
            if rec.state != 'uploaded':
                raise UserError(_("Only an uploaded bill can be submitted."))
            if not rec.move_id:
                raise UserError(_("No bill has been uploaded yet."))
            rec.state = 'submitted'

    def action_stores_approve(self):
        if not self.env.user.has_group('vendor_management.group_purchase_role_stores'):
            raise AccessError(_("Only Purchase Stores can approve this bill."))
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(_("Only a submitted bill can be approved by Stores."))
            rec.state = 'stores_approved'

    def action_finance_confirm(self):
        """Purely navigational: opens the real bill for Purchase Finance to
        review and click Odoo's own "Confirm" button there - THAT is the
        single action that actually moves it from draft to posted (see
        account_move.py's action_post override, which is the real gate -
        restricted to Purchase Finance and only once Stores has approved -
        and which syncs this record's state to 'finance_confirmed' once
        posting actually succeeds). This method itself never changes state."""
        if not self.env.user.has_group('vendor_management.group_purchase_role_finance'):
            raise AccessError(_("Only Purchase Finance can confirm this bill."))
        self.ensure_one()
        if self.state != 'stores_approved':
            raise UserError(_("This bill must be approved by Purchase Stores first."))
        if not self.move_id:
            raise UserError(_("No bill has been uploaded yet."))
        return self._action_open_move()
