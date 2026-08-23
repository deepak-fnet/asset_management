from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class CancelBidWizard(models.TransientModel):
    _name = 'cancel.bid.wizard'
    _description = 'Cancel Bid Wizard'

    purchase_id = fields.Many2one('purchase.order', string="Source Purchase Order")
    reason = fields.Char( string="Reason")

    def action_cancel(self):
        self.ensure_one()
        if not self.reason:
            raise ValidationError("Please enter cancellation reason.")
        self.purchase_id.write({
            'state': 'cancel',
            'reason': self.reason,
            'is_cancel_bid':True,
        })
        return {'type': 'ir.actions.act_window_close'}
