from odoo import _, fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    budget_line_ids = fields.One2many('asset.budget.line', 'move_id', string='Budget')

    def _post(self, soft=True):
        posted = super()._post(soft)
        posted.filtered(lambda m: m.move_type in ('in_invoice', 'in_refund'))._budget_spend()
        return posted

    def button_draft(self):
        self._budget_unspend()
        return super().button_draft()

    def button_cancel(self):
        self._budget_unspend()
        return super().button_cancel()

    def _budget_spend(self):
        """Posted vendor bill for a request's order: Spent, and the order's
        reservation is consumed by the same amount."""
        Line = self.env['asset.budget.line'].sudo()
        Budget = self.env['asset.budget']
        for move in self:
            if move.sudo().budget_line_ids:
                continue
            per_key = {}
            for aml in move.invoice_line_ids.filtered(lambda l: l.purchase_line_id and l.product_id):
                order = aml.purchase_line_id.order_id
                req = order.asset_request_id
                if not req:
                    continue
                budget = Budget._find(aml.product_id.categ_id, req.department_id,
                                      req.request_date or fields.Date.today(), move.company_id)
                if not budget:
                    continue
                entry = per_key.setdefault((budget, order), [0.0, 0.0])
                entry[0] += aml.balance          # untaxed, company currency; negative for a refund
                entry[1] += aml.quantity * (-1 if move.move_type == 'in_refund' else 1)
            for (budget, order), (amount, qty) in per_key.items():
                Line.create({'budget_id': budget.id, 'kind': 'spend', 'order_id': order.id, 'move_id': move.id,
                             'request_id': order.asset_request_id.id, 'amount': amount, 'quantity': qty,
                             'date': move.invoice_date or fields.Date.today(), 'note': move.ref or move.name})
                reserve = order.sudo().budget_line_ids.filtered(
                    lambda l: l.budget_id == budget and l.kind == 'reserve' and l.state == 'active')[:1]
                if reserve:
                    reserve.write({
                        'consumed_amount': min(max(reserve.consumed_amount + amount, 0.0), reserve.amount),
                        'consumed_qty': min(max(reserve.consumed_qty + qty, 0.0), reserve.quantity)})
                    if order.invoice_status == 'invoiced':
                        reserve.write({'state': 'closed', 'note': _("Fully billed")})

    def _budget_unspend(self):
        for move in self.filtered(lambda m: m.state == 'posted'):
            for spend in move.sudo().budget_line_ids.filtered(lambda l: l.kind == 'spend'):
                reserve = spend.order_id.sudo().budget_line_ids.filtered(
                    lambda l: l.budget_id == spend.budget_id and l.kind == 'reserve' and l.state in ('active', 'closed'))[:1]
                if reserve:
                    reserve.write({'state': 'active',
                                   'consumed_amount': max(reserve.consumed_amount - spend.amount, 0.0),
                                   'consumed_qty': max(reserve.consumed_qty - spend.quantity, 0.0)})
                spend.unlink()
