from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class AssetBudget(models.Model):
    """Budget for one product category + department over a period.

    Total   = allocated + transfers in + additional budget
    Balance = Total - (in process + spent + transfers out + surrendered)
    In process (reserved) comes from requests' RFQs / orders under approval or
    confirmed and not yet billed; spent comes from posted vendor bills.
    """
    _name = 'asset.budget'
    _description = 'Budget Allocation'
    _inherit = ['mail.thread']
    _order = 'date_from desc, category_id, department_id'

    name = fields.Char(compute='_compute_name', store=True)
    category_id = fields.Many2one('product.category', string='Product Category', required=True, tracking=True)
    department_id = fields.Many2one('hr.department', string='Department', required=True, tracking=True)
    date_from = fields.Date('From', required=True, tracking=True)
    date_to = fields.Date('To', required=True, tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    ledger_code = fields.Char('Ledger Code')
    note = fields.Text()
    active = fields.Boolean(default=True)

    budget_amount = fields.Monetary('Allocated Amount', tracking=True)
    budget_qty = fields.Float('Allocated Quantity', tracking=True,
                              help="Optional. 0 = no quantity limit.")

    line_ids = fields.One2many('asset.budget.line', 'budget_id', string='Budget History')
    transfer_ids = fields.One2many('asset.budget.transfer', 'budget_id', string='Transfers')

    transfer_in = fields.Monetary(compute='_compute_amounts', store=True)
    transfer_out = fields.Monetary(compute='_compute_amounts', store=True)
    additional_amount = fields.Monetary('Additional', compute='_compute_amounts', store=True)
    surrendered_amount = fields.Monetary('Surrendered', compute='_compute_amounts', store=True)
    total_amount = fields.Monetary('Total Budget Allocated', compute='_compute_amounts', store=True)
    reserved_amount = fields.Monetary('In Process', compute='_compute_amounts', store=True,
                                      help="On RFQs / purchase orders, not billed yet.")
    spent_amount = fields.Monetary('Invoiced', compute='_compute_amounts', store=True,
                                   help="Posted vendor bills.")
    po_amount = fields.Monetary('PO Amount', compute='_compute_amounts', store=True,
                                help="Committed on RFQs / purchase orders: not yet billed + already billed.")
    balance_amount = fields.Monetary('Balance', compute='_compute_amounts', store=True)
    reserved_qty = fields.Float('In Process Qty', compute='_compute_amounts', store=True)
    spent_qty = fields.Float('Spent Qty', compute='_compute_amounts', store=True)
    balance_qty = fields.Float('Balance Qty', compute='_compute_amounts', store=True)
    used_percent = fields.Float('Used %', compute='_compute_amounts', store=True)

    @api.depends('category_id', 'department_id', 'date_from', 'date_to')
    def _compute_name(self):
        for rec in self:
            period = ('%s - %s' % (rec.date_from.strftime('%b %Y'), rec.date_to.strftime('%b %Y'))
                      if rec.date_from and rec.date_to else '')
            rec.name = ' / '.join(p for p in [rec.department_id.name, rec.category_id.complete_name, period] if p)

    @api.depends('budget_amount', 'budget_qty', 'line_ids.amount', 'line_ids.consumed_amount',
                 'line_ids.quantity', 'line_ids.consumed_qty', 'line_ids.state', 'line_ids.kind',
                 'transfer_ids.amount', 'transfer_ids.direction')
    def _compute_amounts(self):
        for rec in self:
            tr = rec.transfer_ids
            rec.transfer_in = sum(tr.filtered(lambda t: t.direction == 'in').mapped('amount'))
            rec.transfer_out = sum(tr.filtered(lambda t: t.direction == 'out').mapped('amount'))
            rec.additional_amount = sum(tr.filtered(lambda t: t.direction == 'add').mapped('amount'))
            rec.surrendered_amount = sum(tr.filtered(lambda t: t.direction == 'surrender').mapped('amount'))
            reserves = rec.line_ids.filtered(lambda l: l.kind == 'reserve' and l.state == 'active')
            spends = rec.line_ids.filtered(lambda l: l.kind == 'spend')
            rec.reserved_amount = sum(max(l.amount - l.consumed_amount, 0.0) for l in reserves)
            rec.reserved_qty = sum(max(l.quantity - l.consumed_qty, 0.0) for l in reserves)
            rec.spent_amount = sum(spends.mapped('amount'))
            rec.spent_qty = sum(spends.mapped('quantity'))
            rec.po_amount = rec.reserved_amount + rec.spent_amount
            rec.total_amount = rec.budget_amount + rec.transfer_in + rec.additional_amount
            rec.balance_amount = rec.total_amount - (rec.reserved_amount + rec.spent_amount
                                                     + rec.transfer_out + rec.surrendered_amount)
            rec.balance_qty = rec.budget_qty - (rec.reserved_qty + rec.spent_qty) if rec.budget_qty else 0.0
            used = rec.reserved_amount + rec.spent_amount
            rec.used_percent = (used / rec.total_amount * 100.0) if rec.total_amount else 0.0

    @api.constrains('date_from', 'date_to', 'category_id', 'department_id', 'company_id', 'active')
    def _check_overlap(self):
        for rec in self.filtered('active'):
            if rec.date_to < rec.date_from:
                raise ValidationError(_("The budget ends before it starts."))
            clash = self.search([
                ('id', '!=', rec.id), ('category_id', '=', rec.category_id.id),
                ('department_id', '=', rec.department_id.id), ('company_id', '=', rec.company_id.id),
                ('date_from', '<=', rec.date_to), ('date_to', '>=', rec.date_from)], limit=1)
            if clash:
                raise ValidationError(_("A budget for %s already covers part of this period.", clash.name))

    @api.model
    def _find(self, category, department, on_date, company=None):
        """Budget for this category (or its nearest parent that has one) and
        department on that date."""
        company = company or self.env.company
        cat = category
        while cat:
            budget = self.sudo().search([
                ('category_id', '=', cat.id), ('department_id', '=', department.id),
                ('company_id', '=', company.id), ('date_from', '<=', on_date), ('date_to', '>=', on_date)], limit=1)
            if budget:
                return budget
            cat = cat.parent_id
        return self.browse()

    def action_open_transfer(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Budget Transfer'),
            'res_model': 'asset.budget.transfer.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'default_from_budget_id': self.id},
        }

    def action_view_lines(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Budget History'), 'res_model': 'asset.budget.line',
            'view_mode': 'list,form', 'domain': [('budget_id', '=', self.id)],
        }


class AssetBudgetLine(models.Model):
    """One budget movement. A reserve line holds an order's amount while it is
    under approval / not billed (consumed as bills post); a spend line is a
    posted vendor bill."""
    _name = 'asset.budget.line'
    _description = 'Budget History'
    _order = 'date desc, id desc'

    budget_id = fields.Many2one('asset.budget', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='budget_id.currency_id')
    kind = fields.Selection([('reserve', 'In Process'), ('spend', 'Spent')], required=True)
    state = fields.Selection([('active', 'Active'), ('moved', 'Moved to Vendor Orders'),
                              ('closed', 'Closed'), ('released', 'Released')], default='active', required=True)
    date = fields.Date(default=fields.Date.context_today)
    request_id = fields.Many2one('asset.request', string='Request', index=True)
    order_id = fields.Many2one('purchase.order', string='RFQ / Order', index=True)
    move_id = fields.Many2one('account.move', string='Vendor Bill', index=True)
    amount = fields.Monetary('Amount')
    quantity = fields.Float('Quantity')
    consumed_amount = fields.Monetary('Billed')
    consumed_qty = fields.Float('Billed Qty')
    note = fields.Char()


class AssetBudgetTransfer(models.Model):
    _name = 'asset.budget.transfer'
    _description = 'Budget Transfer'
    _order = 'date desc, id desc'

    budget_id = fields.Many2one('asset.budget', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='budget_id.currency_id')
    direction = fields.Selection([('in', 'Transfer In'), ('out', 'Transfer Out'),
                                  ('add', 'Additional Budget'), ('surrender', 'Surrendered')], required=True)
    other_budget_id = fields.Many2one('asset.budget', string='From / To Budget')
    amount = fields.Monetary(required=True)
    date = fields.Date(default=fields.Date.context_today)
    reason = fields.Char()
    user_id = fields.Many2one('res.users', default=lambda s: s.env.user, string='Done By')

    @api.constrains('amount')
    def _check_amount(self):
        if any(t.amount <= 0 for t in self):
            raise UserError(_("The amount must be positive."))
