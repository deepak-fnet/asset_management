from odoo import _, api, fields, models
from odoo.exceptions import UserError
from ..models.vendor_compat_utils import find_department_head


class PurchaseAdvanceRequestWizard(models.TransientModel):
    _name = 'purchase.advance.request.wizard'
    _description = 'Request Purchase Advance'

    order_id = fields.Many2one('purchase.order', required=True)
    currency_id = fields.Many2one(related='order_id.currency_id')
    amount_total = fields.Monetary(related='order_id.amount_total', string="Order Total")
    advance_requested = fields.Monetary(related='order_id.advance_requested', string="Already Requested")
    advance_balance = fields.Monetary(related='order_id.advance_balance', string="Available for Advance")
    advance_type = fields.Selection([('percent', 'Percentage'), ('amount', 'Amount')],
                                    string="Advance As", default='percent', required=True)
    percent = fields.Float(string="Percentage (%)", digits=(5, 2))
    amount = fields.Monetary(string="Advance Amount", compute='_compute_amount',
                             store=True, readonly=False, precompute=True, required=True)
    journal_id = fields.Many2one(
        'account.journal', string="Pay From", required=True,
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id)]")
    company_id = fields.Many2one(related='order_id.company_id')
    date = fields.Date(string="Payment Date", required=True, default=fields.Date.context_today)
    memo = fields.Char(string="Memo")
    invoice_ids = fields.Many2many('ir.attachment', string="Vendor Invoice", required=True,
                                   help="The vendor's invoice / proforma asking for this advance.")
    needs_manager = fields.Boolean(compute='_compute_needs_manager')
    manager_id = fields.Many2one('res.users', compute='_compute_needs_manager', string="Approving Manager")

    @api.depends_context('uid')
    def _compute_needs_manager(self):
        for rec in self:
            rec.needs_manager = rec.order_id._advance_needs_manager()
            rec.manager_id = find_department_head(self.env, self.env.user) if rec.needs_manager else False

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        order = self.env['purchase.order'].browse(res.get('order_id'))
        if order:
            res.setdefault('memo', _("Advance for %s", order.name))
            journal = self.env['account.journal'].sudo().search(
                [('type', '=', 'bank'), ('company_id', '=', order.company_id.id)], limit=1)
            if journal:
                res.setdefault('journal_id', journal.id)
        return res

    @api.depends('advance_type', 'percent', 'order_id.amount_total')
    def _compute_amount(self):
        for rec in self:
            if rec.advance_type == 'percent':
                rec.amount = rec.order_id.amount_total * rec.percent / 100.0
            else:
                rec.amount = rec.amount or 0.0

    @api.constrains('percent')
    def _check_percent(self):
        for rec in self:
            if rec.advance_type == 'percent' and not 0 < rec.percent <= 100:
                raise UserError(_("Percentage must be between 0 and 100."))

    def action_confirm_request(self):
        self.ensure_one()
        self.order_id._request_advance(self.amount, self.journal_id, self.date, self.memo, self.invoice_ids)
        return {'type': 'ir.actions.act_window_close'}


class PurchaseAdvanceRejectWizard(models.TransientModel):
    _name = 'purchase.advance.reject.wizard'
    _description = 'Reject Purchase Advance'

    payment_id = fields.Many2one('account.payment', required=True)
    reason = fields.Text(string="Reason for Rejection", required=True)

    def action_confirm_reject(self):
        self.ensure_one()
        if not (self.reason or '').strip():
            raise UserError(_("Please enter a reason for the rejection."))
        self.payment_id._advance_reject(self.reason.strip())
        return {'type': 'ir.actions.act_window_close'}
