from odoo import models, api, fields


class POConfirmWizard(models.TransientModel):
    _name = 'po.confirm.wizard'
    _description = 'Partial PO Confirmation Wizard'

    purchase_id = fields.Many2one(
        'purchase.order',
        string="Purchase Order",
        required=True
    )

    message = fields.Text(
        string="Message",
        readonly=True,
        compute='_compute_message',
        store=False
    )

    @api.depends('purchase_id')
    def _compute_message(self):
        for wizard in self:
            if wizard.purchase_id:
                if wizard.purchase_id.ready_to_confirm:
                    wizard.message = "Are you sure you want to continue confirming this Purchase Order?"
                else:
                    wizard.message = "Are you sure you want to continue confirming this Partial Purchase Order?"
            else:
                wizard.message = ''

    def action_confirm_partial_po(self):
        if self.purchase_id:
            self.purchase_id.is_confirm_done = True
            self.purchase_id.button_confirm()
        else:
            return {'type': 'ir.actions.act_window_close'}