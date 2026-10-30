from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AdvanceAdjustWizard(models.TransientModel):
    _name = 'advance.adjust.wizard'
    _description = 'Adjust Advance Payment Wizard'

    advance_line_id = fields.Many2one(
        'advance.payment.line',
        string="Advance Line",
        required=True,
    )
    expense_id = fields.Many2one(
        'hr.expense',
        string="Expense",
        required=True,
    )
    advance_amount = fields.Monetary(
        related='advance_line_id.amount',
        string="Amount",
        readonly=True,
    )
    currency_id = fields.Many2one(
        related='advance_line_id.currency_id',
    )
    advance_date = fields.Date(
        related='advance_line_id.date',
        string="Date",
        readonly=True,
    )
    advance_remark = fields.Text(
        related='advance_line_id.remark',
        string="Reason",
        readonly=True,
    )

    def action_adjust(self):
        self.ensure_one()
        line = self.advance_line_id
        expense = self.expense_id

        if line.line_state != 'confirmed':
            raise UserError(_("Only confirmed advance lines can be adjusted."))
        if line.expense_id:
            raise UserError(_("This advance line is already adjusted to another expense."))

        line.write({'expense_id': expense.id})

        # Add amount FIRST
        expense.advance_pay = (expense.advance_pay or 0.0) + line.amount

        # Flush so that balance_amount recomputes in DB
        expense.flush_recordset(['advance_pay'])
        expense.invalidate_recordset(['balance_amount'])
        expense.flush_recordset(['balance_amount'])

        # Calculate manually instead of relying on computed field
        total = expense.total_amount_currency or 0.0
        advance = expense.advance_pay or 0.0
        balance = total - advance

        if balance == 0.0:
            # Fully covered by the advance: Paid (state follows process_end).
            expense.process_end = True

        # return {'type': 'ir.actions.client', 'tag': 'reload'}


class AdvanceRemoveWizard(models.TransientModel):
    _name = 'advance.remove.wizard'
    _description = 'Remove Advance Payment Wizard'

    advance_line_id = fields.Many2one(
        'advance.payment.line',
        string="Advance Line",
        required=True,
    )
    expense_id = fields.Many2one(
        'hr.expense',
        string="Expense",
        required=True,
    )
    advance_amount = fields.Monetary(
        related='advance_line_id.amount',
        string="Amount",
        readonly=True,
    )
    currency_id = fields.Many2one(
        related='advance_line_id.currency_id',
    )

    def action_remove(self):
        """Unlink the advance line from expense and subtract amount."""
        self.ensure_one()
        line = self.advance_line_id
        expense = self.expense_id

        # Subtract amount
        expense.advance_pay = max((expense.advance_pay or 0.0) - line.amount, 0.0)

        # Unlink
        line.write({
            'expense_id': False,
        })

        return {'type': 'ir.actions.client', 'tag': 'reload'}
