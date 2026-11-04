from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import formatLang

PARAM_MODE = 'asset_budget.exceed_mode'          # 'approval' (default) | 'block'
PARAM_APPROVER = 'asset_budget.approver_id'      # res.users id


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    budget_line_ids = fields.One2many('asset.budget.line', 'order_id', string='Budget')
    budget_exceeded = fields.Boolean(copy=False, readonly=True,
                                     help="Over budget when submitted for approval.")
    budget_check_note = fields.Html(copy=False, readonly=True)
    budget_summary = fields.Html(compute='_compute_budget_summary')

    # ------------------------------------------------------------------ helpers
    def _budget_applies(self):
        """Only purchases raised from a request touch a budget."""
        self.ensure_one()
        return bool(self.asset_request_id)

    def _budget_line_amount(self, line):
        price = line.winning_quote_line_id.vendor_price if 'winning_quote_line_id' in line._fields \
            and line.winning_quote_line_id else line.price_unit
        amount = (price or 0.0) * line.product_qty
        return self.currency_id._convert(amount, self.company_id.currency_id, self.company_id,
                                         self.date_order or fields.Date.today())

    def _budget_requirements(self):
        """{budget or False: [amount, qty, categories]} for this order's lines."""
        self.ensure_one()
        req = self.asset_request_id
        Budget = self.env['asset.budget']
        out = {}
        for line in self.order_line.filtered(lambda l: not l.display_type and l.product_id):
            cat = line.product_id.categ_id
            budget = Budget._find(cat, req.department_id, req.request_date or fields.Date.today(),
                                  self.company_id)
            key = budget or False
            entry = out.setdefault(key, [0.0, 0.0, self.env['product.category']])
            entry[0] += self._budget_line_amount(line)
            entry[1] += line.product_qty
            entry[2] |= cat
        return out

    def _budget_problems(self):
        """Text lines for every budget this order is missing or exceeds. The
        order's own active reservation is added back first (resubmission)."""
        self.ensure_one()
        problems = []
        dept = self.asset_request_id.department_id
        for budget, (amount, qty, cats) in self._budget_requirements().items():
            if not budget:
                problems.append(_("No budget for %(cats)s / %(dept)s.",
                                  cats=', '.join(cats.mapped('complete_name')), dept=dept.name or _('no department')))
                continue
            own = self.budget_line_ids.filtered(
                lambda l: l.budget_id == budget and l.kind == 'reserve' and l.state == 'active')
            balance = budget.balance_amount + sum(own.mapped('amount')) - sum(own.mapped('consumed_amount'))
            if amount > balance:
                problems.append(_("%(b)s: needs %(n)s, balance %(bal)s.", b=budget.name,
                                  n=formatLang(self.env, amount, currency_obj=budget.currency_id),
                                  bal=formatLang(self.env, balance, currency_obj=budget.currency_id)))
            if budget.budget_qty:
                bal_qty = budget.balance_qty + sum(own.mapped('quantity')) - sum(own.mapped('consumed_qty'))
                if qty > bal_qty:
                    problems.append(_("%(b)s: needs %(q)s units, %(bq)s left.", b=budget.name, q=qty, bq=bal_qty))
        return problems

    def _budget_reserve(self, note=None):
        """Release this order's active reservation and reserve again."""
        Line = self.env['asset.budget.line'].sudo()
        for order in self.filtered(lambda o: o._budget_applies()):
            order._budget_release(note=_("Re-reserved"))
            for budget, (amount, qty, _cats) in order._budget_requirements().items():
                if budget:
                    Line.create({'budget_id': budget.id, 'kind': 'reserve', 'order_id': order.id,
                                 'request_id': order.asset_request_id.id, 'amount': amount, 'quantity': qty,
                                 'note': note or _("Submitted for approval")})

    def _budget_release(self, note=None):
        lines = self.sudo().budget_line_ids.filtered(lambda l: l.kind == 'reserve' and l.state == 'active')
        if lines:
            lines.write({'state': 'released', 'note': note or _("Released")})

    def _budget_approver(self):
        uid = int(self.env['ir.config_parameter'].sudo().get_param(PARAM_APPROVER) or 0)
        return self.env['res.users'].sudo().browse(uid).exists()

    @api.depends('budget_line_ids.amount', 'budget_line_ids.state', 'budget_exceeded', 'order_line.price_subtotal')
    def _compute_budget_summary(self):
        for order in self:
            if not order.asset_request_id:
                order.budget_summary = False
                continue
            rows = []
            for budget, (amount, _qty, cats) in order._budget_requirements().items():
                if not budget:
                    rows.append(Markup('<tr><td>%s</td><td colspan="3" class="text-danger">%s</td></tr>') % (
                        ', '.join(cats.mapped('complete_name')), _("No budget")))
                    continue
                fmt = lambda v: formatLang(order.env, v, currency_obj=budget.currency_id)  # noqa: E731
                cls = 'text-danger' if amount > budget.balance_amount + sum(order.budget_line_ids.filtered(
                    lambda l: l.budget_id == budget and l.kind == 'reserve' and l.state == 'active').mapped(
                    lambda l: l.amount - l.consumed_amount)) else 'text-success'
                rows.append(Markup('<tr><td>%s</td><td class="text-end">%s</td><td class="text-end">%s</td>'
                                   '<td class="text-end %s">%s</td></tr>') % (
                    budget.name, fmt(budget.total_amount), fmt(amount), cls, fmt(budget.balance_amount)))
            order.budget_summary = Markup(
                '<table class="table table-sm mb-0"><thead><tr><th>%s</th><th class="text-end">%s</th>'
                '<th class="text-end">%s</th><th class="text-end">%s</th></tr></thead><tbody>%s</tbody></table>') % (
                _("Budget"), _("Total"), _("This Order"), _("Balance"), Markup('').join(rows)) if rows else False

    # ------------------------------------------------------------------ flow
    def action_submit_for_approval(self):
        mode = self.env['ir.config_parameter'].sudo().get_param(PARAM_MODE) or 'approval'
        exceeded = {}
        for order in self.filtered(lambda o: o._budget_applies()):
            problems = order._budget_problems()
            if problems and mode == 'block':
                raise UserError(_("Budget not sufficient for %(o)s:\n%(p)s", o=order.name, p='\n'.join(problems)))
            if problems:
                if not order._budget_approver():
                    raise UserError(_("%(o)s is over budget and needs the Budget Approver, but none is set "
                                      "(Purchase > Configuration > Settings > Budget).\n\n%(p)s",
                                      o=order.name, p='\n'.join(problems)))
                exceeded[order] = problems
        res = super().action_submit_for_approval()
        for order in self.filtered(lambda o: o._budget_applies()):
            problems = exceeded.get(order)
            order.write({'budget_exceeded': bool(problems),
                         'budget_check_note': Markup('<br/>').join(problems) if problems else False})
            order._budget_reserve()
            if problems:
                order._budget_add_approval_level(problems)
        return res

    def _budget_add_approval_level(self, problems):
        """Over budget: the Budget Approver signs off last."""
        self.ensure_one()
        approver = self._budget_approver()
        lines = self.approval_line_ids
        body = Markup('<p><b>%s</b></p><ul>%s</ul>') % (
            _("Over budget - %s added as the last approval level.", approver.name),
            Markup('').join(Markup('<li>%s</li>') % p for p in problems))
        own = lines.filtered(lambda l: l.approver_id == approver)
        last_seq = max(lines.mapped('sequence') or [0])
        if own:
            own.write({'sequence': max(last_seq, own[0].sequence)})
        else:
            self.env['purchase.approval.line'].sudo().create({
                'order_id': self.id, 'approver_id': approver.id, 'sequence': last_seq + 10})
        self.message_post(body=body)

    def action_reset_approval(self):
        res = super().action_reset_approval()
        self._budget_release(_("Approval reset"))
        self.write({'budget_exceeded': False, 'budget_check_note': False})
        return res

    def _reject_with_reason(self, reason):
        res = super()._reject_with_reason(reason)
        self.filtered(lambda o: o.approval_status == 'rejected')._budget_release(_("Rejected"))
        return res

    def button_cancel(self):
        res = super().button_cancel()
        self._budget_release(_("Cancelled"))
        return res

    def button_confirm(self):
        res = super().button_confirm()
        # RFQ split into vendor orders: the reservation follows each order.
        for rfq in self.filtered(lambda o: o._budget_applies()):
            children = self.search([('rfq_id', '=', rfq.id)]) if 'rfq_id' in self._fields else self.browse()
            active = rfq.sudo().budget_line_ids.filtered(lambda l: l.kind == 'reserve' and l.state == 'active')
            if not children or not active:
                continue
            active.write({'state': 'moved', 'note': _("Moved to %s", ', '.join(children.mapped('name')))})
            children._budget_reserve(note=_("Vendor order from %s", rfq.name))
        return res
