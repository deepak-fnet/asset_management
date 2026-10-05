from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PurchaseAdvanceRequestWizard(models.TransientModel):
    _name = 'purchase.advance.request.wizard'
    _description = 'Request Purchase Advance'

    order_id = fields.Many2one('purchase.order', required=True)
    currency_id = fields.Many2one(related='order_id.currency_id')
    advance_percent = fields.Float(related='order_id.advance_percent')
    amount_total = fields.Monetary(related='order_id.amount_total', string="Order Total")
    advance_requested = fields.Monetary(related='order_id.advance_requested', string="Already Requested")
    amount = fields.Monetary(string="Advance Amount", required=True)
    journal_id = fields.Many2one(
        'account.journal', string="Pay From", required=True,
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id)]")
    company_id = fields.Many2one(related='order_id.company_id')
    date = fields.Date(string="Payment Date", required=True, default=fields.Date.context_today)
    memo = fields.Char(string="Memo")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        order = self.env['purchase.order'].browse(res.get('order_id'))
        if order:
            res.setdefault('amount', max(order.advance_allowed - order.advance_requested, 0.0))
            res.setdefault('memo', _("Advance for %s", order.name))
            journal = self.env['account.journal'].sudo().search(
                [('type', '=', 'bank'), ('company_id', '=', order.company_id.id)], limit=1)
            if journal:
                res.setdefault('journal_id', journal.id)
        return res

    def action_confirm_request(self):
        self.ensure_one()
        self.order_id._request_advance(self.amount, self.journal_id, self.date, self.memo)
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
