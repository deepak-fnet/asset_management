from odoo import _, fields, models
from odoo.exceptions import UserError


class PurchaseBillRejectWizard(models.TransientModel):
    _name = 'purchase.bill.reject.wizard'
    _description = 'Reject Purchase Bill'

    move_id = fields.Many2one('account.move', required=True)
    reason = fields.Text(string="Reason for Rejection", required=True)

    def action_confirm_reject(self):
        self.ensure_one()
        if not (self.reason or '').strip():
            raise UserError(_("Please enter a reason for the rejection."))
        self.move_id._bill_reject(self.reason.strip())
        return {'type': 'ir.actions.act_window_close'}
