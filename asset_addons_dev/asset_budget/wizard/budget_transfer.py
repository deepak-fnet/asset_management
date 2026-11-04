from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AssetBudgetTransferWizard(models.TransientModel):
    _name = 'asset.budget.transfer.wizard'
    _description = 'Budget Transfer'

    operation = fields.Selection([('transfer', 'Transfer to another budget'), ('add', 'Additional budget'),
                                  ('surrender', 'Surrender budget')], default='transfer', required=True)
    from_budget_id = fields.Many2one('asset.budget', string='Budget', required=True)
    from_balance = fields.Monetary(related='from_budget_id.balance_amount', string='Balance')
    currency_id = fields.Many2one(related='from_budget_id.currency_id')
    to_budget_id = fields.Many2one('asset.budget', string='To Budget')
    to_balance = fields.Monetary(related='to_budget_id.balance_amount', string='To Balance')
    amount = fields.Monetary(required=True)
    reason = fields.Char(required=True)

    def action_apply(self):
        self.ensure_one()
        if self.amount <= 0:
            raise UserError(_("Enter a positive amount."))
        T = self.env['asset.budget.transfer']
        if self.operation == 'add':
            T.create({'budget_id': self.from_budget_id.id, 'direction': 'add', 'amount': self.amount,
                      'reason': self.reason})
            return {'type': 'ir.actions.act_window_close'}
        if self.amount > self.from_budget_id.balance_amount:
            raise UserError(_("Only %s is left in this budget.", self.from_budget_id.balance_amount))
        if self.operation == 'surrender':
            T.create({'budget_id': self.from_budget_id.id, 'direction': 'surrender', 'amount': self.amount,
                      'reason': self.reason})
            return {'type': 'ir.actions.act_window_close'}
        if not self.to_budget_id or self.to_budget_id == self.from_budget_id:
            raise UserError(_("Choose another budget to transfer to."))
        T.create({'budget_id': self.from_budget_id.id, 'direction': 'out', 'amount': self.amount,
                  'other_budget_id': self.to_budget_id.id, 'reason': self.reason})
        T.create({'budget_id': self.to_budget_id.id, 'direction': 'in', 'amount': self.amount,
                  'other_budget_id': self.from_budget_id.id, 'reason': self.reason})
        return {'type': 'ir.actions.act_window_close'}
