from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from datetime import timedelta


class HrExpense(models.Model):
    _inherit = "hr.expense"

    category_ids = fields.Many2many('product.product')
    department_id = fields.Many2one('hr.department', related='employee_id.department_id')
    expense_line_ids = fields.One2many(
        'hr.expense.line',
        'expense_id',
        string='Expense Lines'
    )
    employee_no = fields.Char(
        string="Employee No",
        related="employee_id.identification_id",
        store=True
    )
    # Odoo 19 has no expense report (hr.expense.sheet): the expense itself is
    # submitted and approved. Accountant / Paid are this module's own steps,
    # driven by accountant_stage / process_end (see _compute_state).
    state = fields.Selection(
        selection_add=[
            ('approved',), ('accountant', 'Accountant'),
            ('paid',), ('done', 'Paid (Settled)'),
            ('refused',),
        ],
        ondelete={'accountant': 'set default', 'done': 'set default'},
    )
    process_end = fields.Boolean("Process End", copy=False)
    accountant_stage = fields.Boolean("With Accountant", copy=False)
    product_id = fields.Many2one(
        comodel_name='product.product',
        string="Category",
        tracking=False,
        check_company=True,
        domain=[('can_be_expensed', '=', True)],
        ondelete='restrict',
        default=lambda self: self._get_dummy_product(),
    )
    voucher = fields.Char("Voucher")
    remarks = fields.Char()

    @api.constrains('from_date', 'to_date')
    def _check_date_range(self):
        for rec in self:
            if rec.from_date and rec.to_date and rec.to_date < rec.from_date:
                raise ValidationError(_("To Date cannot be earlier than From Date."))

    def _get_dummy_product(self):
        # The placeholder category of a multi-line claim (the lines carry the
        # real categories). Read/created as superuser: employees cannot
        # create products.
        dummy = self.env.ref('expenses_extended.product_dummy_expense', raise_if_not_found=False)
        if dummy and dummy.can_be_expensed:
            return dummy.id
        dummy = self.env['product.product'].sudo().search([
            ('name', '=', 'Dummy Expense'),
            ('can_be_expensed', '=', True),
        ], limit=1)
        if not dummy:
            template = self.env['product.template'].sudo().create({
                'name': 'Dummy Expense',
                'type': 'service',
                'can_be_expensed': True,
            })
            dummy = template.product_variant_id
        return dummy.id

    balance_amount = fields.Monetary(
        string="Balance Amount",
        currency_field='currency_id',
        compute='_compute_balance_amount',
        store=True,
        readonly=True
    )
    custom_total = fields.Monetary(
        string="Total In Currency",
        currency_field='currency_id',
        compute='_compute_custom_total_amount',
        store=True,
        readonly=True
    )
    flight_amount = fields.Monetary(
        string="Flight Charges Paid By Company",
        currency_field='currency_id',
        compute='_compute_flight_amount',
        store=True,
        readonly=True
    )
    purpose = fields.Char("Purpose of Visit")

    advance_pay = fields.Monetary(
        string="Less Advance Amount",
        currency_field='currency_id'
    )

    employee_pay = fields.Monetary(
        string="Employee To Pay",
        currency_field='currency_id',compute="compute_employee_pay",
    )
    company_pay = fields.Monetary(
        string="Company To Pay",
        currency_field='currency_id',compute="compute_company_pay",
    )

    line_color = fields.Selection(
        [('green', 'Green'), ('yellow', 'Yellow'), ('red', 'Red')],
        compute='_compute_line_color'
    )
    from_date = fields.Date("From Date", required=True)
    to_date = fields.Date("To Date", required=True)

    payment_mode = fields.Selection(
        selection=[
            ('own_account', 'Employee (to reimburse)'),
            ('company_account', 'Company'),
        ],
        compute='_compute_payment_mode_from_balance',
        store=True,
        readonly=True,
    )

    net_amount = fields.Monetary(compute='_compute_net_amount', string="Tax Amount")

    quantity = fields.Float(digits='Product Unit of Measure', default=1)

    bal_amount = fields.Monetary()

    # ── Advance Payment Integration ──
    advance_payment_line_ids = fields.One2many(
        'advance.payment.line',
        'expense_id',
        string="Adjusted Advance Lines",
    )
    available_advance_line_ids = fields.Many2many(
        'advance.payment.line',
        string="Available Advance Lines",
        compute='_compute_available_advance_lines',
    )

    is_account_team = fields.Boolean(
        string="Is Account Team",
        compute='_compute_is_account_team',
    )

    advance_id = fields.Many2one('advance.payment')
    grade_id = fields.Many2one('travel.grade.category')
    cannot_approve_reason = fields.Char(string='Cannot Approve Reason', compute='_compute_cannot_approve_reason')

    # Report-level figures (were on hr.expense.sheet in Odoo 17)
    amount_due = fields.Monetary(string="Advance Balance", compute="_compute_amount_due", currency_field='currency_id')
    amount_status = fields.Selection([('green', 'Green'), ('red', 'Red')], compute='_compute_amount_due')
    advance_adjusted_count = fields.Integer(string='Adjusted Count', compute='_compute_advance_summary')
    advance_adjusted_total = fields.Monetary(string='Adjusted Total', compute='_compute_advance_summary',
                                             currency_field='currency_id')
    advance_available_count = fields.Integer(string='Available Count', compute='_compute_advance_summary')
    advance_available_total = fields.Monetary(string='Available Total', compute='_compute_advance_summary',
                                              currency_field='currency_id')

    @api.depends_context('uid')
    @api.depends('employee_id', 'manager_id')
    def _compute_cannot_approve_reason(self):
        for expense in self:
            expense.cannot_approve_reason = False if expense.can_approve else _(
                "%s: you are not the manager of this employee", expense.name or '')

    @api.depends('total_amount_currency', 'advance_pay')
    def _compute_amount_due(self):
        for rec in self:
            rec.amount_due = (rec.advance_pay or 0.0) - (rec.total_amount_currency or 0.0)
            rec.amount_status = 'red' if rec.total_amount_currency > rec.advance_pay else 'green'

    @api.depends('advance_payment_line_ids', 'available_advance_line_ids')
    def _compute_advance_summary(self):
        for rec in self:
            adjusted, available = rec.advance_payment_line_ids, rec.available_advance_line_ids
            rec.advance_adjusted_count = len(adjusted)
            rec.advance_adjusted_total = sum(adjusted.mapped('amount'))
            rec.advance_available_count = len(available)
            rec.advance_available_total = sum(available.mapped('amount'))

    @api.depends('process_end', 'accountant_stage')
    def _compute_state(self):
        super()._compute_state()
        for expense in self:
            if expense.process_end:
                expense.state = 'done'
            elif expense.accountant_stage and expense.state == 'approved':
                expense.state = 'accountant'

    def action_submit_to_manager(self):
        return self.action_submit()

    def action_approve_expense(self):
        return self.action_approve()

    def action_paid(self):
        self.write({'process_end': True})

    def action_accountant(self):
        self.write({'accountant_stage': True})

    @api.onchange('from_date','to_date','grade_id')
    def onchange_date(self):
        for rec in self:
            for line in rec.expense_line_ids:
                line._onchange_product_id()

    def action_open_advance(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Advance Payment',
            'res_model': 'advance.payment',
            'res_id': self.advance_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_create_advance(self):
        if not self.advance_id:
            advance = self.env['advance.payment'].create({
                'expense_id': self.id,
                'line_ids': [(0, 0, {
                    'employee_id': self.env.user.employee_id.id,
                    'amount': 0.0,
                })]
            })
            self.advance_id = advance.id

        else:
            self.advance_id.line_ids = [(0, 0, {
                'employee_id': self.env.user.employee_id.id,
                'amount': 0.0,
            })]

        return {
            'type': 'ir.actions.act_window',
            'name': 'Advance Payment',
            'res_model': 'advance.payment',
            'res_id': self.advance_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _check_bill_age(self):
        for expense in self:
            if expense.to_date and fields.Date.today() > expense.to_date + timedelta(days=30):
                raise ValidationError(_("You cannot process payment. Bill is older than 30 days."))

    def _expense_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        return f"{base_url}/odoo/action-hr_expense.hr_expense_actions_all/{self.id}"

    def _send_html_mail(self, email_to, subject, body):
        self.env['mail.mail'].sudo().create({
            'subject': subject,
            'body_html': body,
            'email_to': email_to,
            'email_from': self.env.user.email or self.env.company.email or 'noreply@yourcompany.com',
        }).send()

    def _mail_manager_submitted(self):
        for rec in self:
            emp = rec.sudo().employee_id
            manager = emp.department_id.manager_id or rec.sudo().department_id.manager_id
            if not (manager and manager.work_email):
                continue
            rec._send_html_mail(manager.work_email, 'Expense Submitted for Approval', f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px;">
                    <p>Dear {manager.name},</p>
                    <p>An expense has been submitted and requires your approval.</p>
                    <table style="border-collapse: collapse;">
                        <tr><td><strong>Employee:</strong></td><td>{rec.employee_id.name}</td></tr>
                        <tr><td><strong>Expense:</strong></td><td>{rec.name}</td></tr>
                        <tr><td><strong>Total Amount:</strong></td><td>{rec.total_amount_currency}</td></tr>
                    </table>
                    <br/>
                    <p><a href="{rec._expense_url()}" style="background-color:#875A7B; color:#ffffff;
                          padding:10px 16px; text-decoration:none; border-radius:5px; display:inline-block;">
                        View Expense</a></p>
                    <br/>
                    <p>Please review and take necessary action.</p>
                    <p>Regards,<br/>Axles India</p>
                </div>""")

    def _mail_finance_approved(self):
        finance_group = self.env.ref('account.group_account_invoice', raise_if_not_found=False)
        finance_users = finance_group.user_ids.filtered(lambda u: u.email) if finance_group else []
        for rec in self:
            for user in finance_users:
                rec._send_html_mail(user.email, 'Expense Approved - Action Required (Finance)', f"""
                    <div style="font-family: Arial, sans-serif; font-size: 14px;">
                        <p>Dear {user.name},</p>
                        <p>An expense has been <b>approved by HOD</b> and is ready for your action.</p>
                        <table style="border-collapse: collapse;">
                            <tr><td><strong>Employee:</strong></td><td>{rec.employee_id.name}</td></tr>
                            <tr><td><strong>Expense:</strong></td><td>{rec.name}</td></tr>
                            <tr><td><strong>Total Amount:</strong></td><td>{rec.total_amount_currency}</td></tr>
                        </table>
                        <br/>
                        <p><a href="{rec._expense_url()}" style="background-color:#28a745; color:#ffffff;
                              padding:10px 16px; text-decoration:none; border-radius:5px;">Review &amp; Process</a></p>
                        <br/>
                        <p>Please proceed with further processing.</p>
                        <p>Regards,<br/>Axles India</p>
                    </div>""")

    def action_submit(self):
        """Odoo 17 created an expense report here. Odoo 19 submits the
        expense itself: bills older than 30 days are refused, a head of
        department's own expense is approved at once (finance is told),
        anyone else's goes to their department manager."""
        self._check_bill_age()
        if self.filtered(lambda expense: not expense.is_editable):
            raise UserError(_('You are not authorized to edit this expense.'))
        res = super().action_submit()
        # HR data read as superuser: employees only see public profiles.
        heads = self.filtered(lambda e: e.state == 'submitted' and e.sudo().employee_id == e.sudo().employee_id.department_id.manager_id)
        if heads:
            heads._do_approve(check=False)
        approved = self.filtered(lambda e: e.state == 'approved')
        approved._mail_finance_approved()
        (self - approved)._mail_manager_submitted()
        return res

    def action_submit_expenses(self):
        return self.action_submit()

    def action_approve(self):
        res = super().action_approve()
        self.filtered(lambda e: e.state == 'approved')._mail_finance_approved()
        return res

    @api.depends('total_amount_currency', 'advance_pay')
    def compute_employee_pay(self):
        for rec in self:
            total = rec.total_amount_currency or 0.0
            advance = rec.advance_pay or 0.0
            if advance > total:
                rec.employee_pay = advance - total
            else:
                rec.employee_pay = 0.0

    @api.depends('total_amount_currency', 'advance_pay')
    def compute_company_pay(self):
        for rec in self:
            total = rec.total_amount_currency or 0.0
            advance = rec.advance_pay or 0.0
            if total > advance:
                rec.company_pay = total - advance
            else:
                rec.company_pay = 0.0

    @api.depends_context('uid')
    def _compute_is_account_team(self):
        """Check if current user belongs to account team (account.group_account_invoice)."""
        is_account = self.env.user.has_group('account.group_account_invoice')
        for rec in self:
            rec.is_account_team = is_account

    @api.depends('employee_id')
    def _compute_available_advance_lines(self):
        """Show confirmed, non-adjusted advance payment lines for the selected employee."""
        for rec in self:
            if rec.employee_id:
                lines = self.env['advance.payment.line'].search([
                    ('employee_id', '=', rec.employee_id.id),
                    ('line_state', '=', 'confirmed'),
                    ('expense_id', '=', False),
                ])
                rec.available_advance_line_ids = lines
            else:
                rec.available_advance_line_ids = False

    def _compute_net_amount(self):
        for expense in self:
            line_total = sum(expense.expense_line_ids.mapped('tax_amount'))
            expense.net_amount = line_total

    @api.depends('total_amount_currency', 'advance_pay')
    def _compute_payment_mode_from_balance(self):
        for expense in self:
            total = expense.total_amount_currency or 0.0
            advance = expense.advance_pay or 0.0
            if advance > total:
                # Employee got more advance than spent → employee reimburses company
                expense.payment_mode = 'own_account'
            elif total > advance:
                # Employee spent more than advance → company pays employee
                expense.payment_mode = 'company_account'
            else:
                # Equal → no one owes, default to own_account
                expense.payment_mode = 'own_account'

    @api.depends('total_amount_currency', 'advance_pay', 'process_end')
    def _compute_line_color(self):
        for rec in self:
            if rec.process_end:
                rec.line_color = 'green'
                continue
            if not rec.total_amount_currency:
                rec.line_color = 'red'
                continue
            balance_ratio = rec.advance_pay / rec.total_amount_currency
            if balance_ratio >= 1.0:
                rec.line_color = 'green'
            elif balance_ratio > 0:
                rec.line_color = 'yellow'
            else:
                rec.line_color = 'red'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('product_id'):
                vals['product_id'] = self._get_dummy_product()
        return super().create(vals_list)

    def action_open_expense_summary(self):
        self.ensure_one()
        Summary = self.env['hr.expense.summary']
        SummaryLine = self.env['hr.expense.summary.line']
        summary = Summary.search([('expense_id', '=', self.id)], limit=1)
        if summary:
            summary.summary_line_ids.unlink()
        else:
            summary = Summary.create({
                'expense_id': self.id,
            })
        total = sum(self.expense_line_ids.mapped('amount'))
        advance = self.advance_pay or 0.0
        balance = total - advance
        summary.write({
            'total_amount': total,
            'advance_amount': advance,
            'balance_amount': balance,
        })
        for line in self.expense_line_ids:
            SummaryLine.create({
                'summary_id': summary.id,
                'description': line.description,
                'category_id': line.category_id[:1].id if line.category_id else False,
                'amount': line.amount,
            })
        return {
            'name': 'Expense Summary',
            'type': 'ir.actions.act_window',
            'res_model': 'hr.expense.summary',
            'view_mode': 'form',
            'res_id': summary.id,
            'target': 'new',
        }

    @api.onchange('category_ids')
    def _onchange_category_ids(self):
        if not self.category_ids:
            self.expense_line_ids = [(5, 0, 0)]
            return
        lines = []
        for category in self.category_ids:
            lines.append((0, 0, {
                'category_id': [(6, 0, [category.id])],
                'date': fields.Date.today(),
                'description': category.display_name,
                'amount': 0.0,
                'flight_paid_by': 'self',
            }))
        self.expense_line_ids = [(5, 0, 0)] + lines

    @api.depends(
        'expense_line_ids.amount',
        'expense_line_ids',
        'currency_id'
    )
    def _compute_custom_total_amount(self):
        for expense in self:
            line_total = sum(expense.expense_line_ids.mapped('total_with_tax'))
            expense.custom_total = line_total
        # super()._compute_total_amount_currency()

    @api.depends(
        'expense_line_ids.amount',
        'expense_line_ids',
        'currency_id'
    )
    def _compute_flight_amount(self):
        for expense in self:
            line_total = sum(
                expense.expense_line_ids
                .filtered(lambda l: l.flight_paid_by == 'company')
                .mapped('total_with_tax')
            )
            expense.flight_amount = line_total

    @api.depends(
        'expense_line_ids.amount',
        'expense_line_ids',
        'currency_id'
    )
    def _compute_total_amount_currency(self):
        for expense in self:
            line_total = sum(
                expense.expense_line_ids
                .filtered(lambda l: l.flight_paid_by == 'self')
                .mapped('total_with_tax')
            )
            expense.total_amount_currency = line_total

        super()._compute_total_amount_currency()

    @api.depends('total_amount_currency', 'advance_pay', 'process_end')
    def _compute_balance_amount(self):
        for rec in self:
            if rec.process_end:
                rec.balance_amount = 0.0
            else:
                rec.balance_amount = (rec.total_amount_currency or 0.0) - (rec.advance_pay or 0.0)


class HrExpensesLine(models.Model):
    _name = "hr.expense.line"
    _description = "Expense Trip Line"

    expense_id = fields.Many2one(
        'hr.expense',
        string='Expense',
    )
    category_ids = fields.Many2many('product.product')
    category_id = fields.Many2one('product.product', domain=[('can_be_expensed', '=', True)])
    internal_id = fields.Char(related='category_id.default_code')
    date = fields.Date()
    description = fields.Char("Purpose of Visit")
    amount = fields.Float("Gross Amount")
    name = fields.Char("File Name")
    store_file = fields.Binary("File")
    purpose_of_visit = fields.Char("Purpose of Visit")
    mode = fields.Selection([('flight', 'Flight'), ('train', 'Train'), ('bus', 'Bus'), ('other', 'Other')])
    date_from = fields.Date("Departure Date and Time")
    date_to = fields.Date("Arrival Date and Time")
    from_location = fields.Char("From Location")
    to_location = fields.Char()
    tax_ids = fields.Many2many(
        'account.tax', compute='_compute_tax_ids',
        store=True,
        string="Taxes",
    )
    tax_amount = fields.Monetary(
        string="Tax Amount",
        currency_field='currency_id',
        compute='_compute_tax_amount',
        store=True,
    )
    total_with_tax = fields.Monetary(
        string="Net Amount",
        currency_field='currency_id',
        compute='_compute_tax_amount',
        store=True,
    )
    currency_id = fields.Many2one(
        related='expense_id.currency_id',
        store=True
    )

    # Selection field: Self or Company for flight category
    flight_paid_by = fields.Selection(
        [('self', 'Self'), ('company', 'Company')],
        string="Paid By",
        default='self',
        help="For flight category: 'Self' = user enters gross amount, 'Company' = only account team enters gross amount."
    )

    # NEW: Computed field to check if category is flight
    # store=False so it always recomputes fresh (avoids stale data on existing records)
    is_flight_category = fields.Boolean(
        string="Is Flight",
        compute='_compute_is_flight_category',
        store=False,
    )
    type = fields.Selection([('self', 'Self'), ('company', 'Company'), ('either', 'Self or Company')], default='self',
        compute='_compute_is_flight_category',
        store=True)
    # Computed: is this a company-paid flight? (for readonly on gross amount)
    is_company_flight = fields.Boolean(
        string="Is Company Flight",
        compute='_compute_is_company_flight',
        store=False,
    )

    # Single computed Boolean for tree view readonly modifier
    is_readonly_line = fields.Boolean(
        string="Is Readonly Line",
        compute='_compute_is_readonly_line',
        store=False,
    )
    is_account_user = fields.Boolean(
        string="Is Account User",
        compute='_compute_is_readonly_line',
        store=False,
    )
    claimed_amount = fields.Float(
        "Claimed Amount",
        readonly=True,
        copy=True,
        help="Original amount entered by the employee in draft state.",
    )

    grade_id = fields.Many2one('travel.grade')
    eligible_amount = fields.Float()
    is_exceeded = fields.Boolean(compute='_compute_exceed')

    @api.onchange('category_id', 'claimed_amount', 'amount', 'total_with_tax')
    def _onchange_product_id(self):
        for rec in self:
            rec.eligible_amount = 0.0
            rec.grade_id = False

            if not rec.expense_id or not rec.expense_id.grade_id or not rec.category_id:
                continue

            actual_grade = self.env['travel.grade'].search([
                ('upload_id', '=', rec.category_id.id),
                ('grade_categ_id', '=', rec.expense_id.grade_id.id),
            ], limit=1)
            rec.grade_id = actual_grade.id

            if not actual_grade:
                continue

            line = rec.category_id.travel_line_ids.filtered(
                lambda l: l.id == actual_grade.id
            )[:1]
            if not line:
                continue

            # Total trip budget for this category
            days = 1
            if rec.expense_id.from_date and rec.expense_id.to_date:
                days = (rec.expense_id.to_date - rec.expense_id.from_date).days + 1
            total_budget = days * line.max_amount

            # Walk *all* sibling lines in the same expense + same category,
            # summing what was claimed BEFORE this row. Use line_ids from the
            # in-memory parent so unsaved lines are included.
            siblings = rec.expense_id.expense_line_ids.filtered(
                lambda l: l.category_id == rec.category_id
            )

            used_before = 0.0
            for l in siblings:
                if l == rec:
                    break
                used_before += l.total_with_tax or 0.0

            rec.eligible_amount = max(total_budget - used_before, 0.0)

    @api.depends(
        'total_with_tax',
        'eligible_amount',
        'category_id',
        'expense_id.expense_line_ids.total_with_tax',
        'expense_id.expense_line_ids.category_id',
    )
    def _compute_exceed(self):
        for rec in self:
            total = rec.total_with_tax or 0.0
            eligible = rec.eligible_amount or 0.0
            rec.is_exceeded = bool(total) and total > eligible

    @api.onchange('claimed_amount')
    def _onchange_amount_set_claimed(self):
        if not self.expense_id or self.expense_id.state == 'draft':
            self.amount = self.claimed_amount

    @api.depends('flight_paid_by')
    def _compute_is_company_flight(self):
        for line in self:
            line.is_company_flight = line.flight_paid_by == 'company'

    @api.depends('expense_id.state')
    def _compute_is_readonly_line(self):
        """Line is readonly when expense state is 'done' and user is NOT in account team."""
        is_account = self.env.user.has_group('account.group_account_invoice')
        for line in self:
            expense_state = line.expense_id.state if line.expense_id else 'draft'
            line.is_account_user = is_account
            if expense_state == 'done' and not is_account:
                line.is_readonly_line = True
            else:
                line.is_readonly_line = False

    @api.depends('category_id')
    def _compute_is_flight_category(self):
        for line in self:
            # if line.category_id and line.category_id.name:
            #     line.is_flight_category = 'flight' in line.category_id.name.lower()
            # else:
            line.is_flight_category = False

    @api.onchange('category_id')
    def _onchange_category_id_flight(self):
        """Auto-reset flight_paid_by to 'self' when category changes to non-flight."""
        self._compute_is_flight_category()
        if self.category_id.expense_type == 'self':
            self.flight_paid_by = 'self'
        if self.category_id.expense_type == 'company':
            self.flight_paid_by = 'company'

    @api.onchange('flight_paid_by')
    def _onchange_flight_paid_by(self):
        """When 'company' is selected, reset gross amount to 0 (account team will fill it).
           When category is not flight, force back to 'self'."""
        if self.category_id.expense_type == 'self':
            self.flight_paid_by = 'self'
        if self.category_id.expense_type == 'company':
            self.flight_paid_by = 'company'
            self.amount = 0.0
        if not self.flight_paid_by:
            self.flight_paid_by = 'company'
        # if not self.is_flight_category:
        #     self.flight_paid_by = 'self'
        # if self.flight_paid_by == 'company':
        #     self.amount = 0.0

    @api.depends('amount', 'tax_ids', 'expense_id.currency_id')
    def _compute_tax_amount(self):
        for line in self:
            if not line.tax_ids or not line.amount:
                line.tax_amount = 0.0
                line.total_with_tax = line.amount
                continue
            taxes_res = line.tax_ids.compute_all(
                line.amount,
                currency=line.expense_id.currency_id,
                quantity=1.0,
                product=False,
                partner=line.expense_id.employee_id,
            )
            tax_total = sum(t['amount'] for t in taxes_res.get('taxes', []))
            line.tax_amount = tax_total
            line.total_with_tax = taxes_res['total_included']

    @api.depends('category_id')
    def _compute_tax_ids(self):
        for rec in self:
            rec.tax_ids = rec.category_id.mapped('supplier_taxes_id')

    @api.constrains('date_from', 'date_to', 'expense_id')
    def _check_dates_within_expense_range(self):
        for rec in self:
            expense = rec.expense_id
            if not expense or not expense.from_date or not expense.to_date:
                continue
            if not rec.date_from or not rec.date_to:
                continue
            date_from = fields.Date.to_date(rec.date_from)
            date_to = fields.Date.to_date(rec.date_to)
            exp_from = fields.Date.to_date(expense.from_date)
            exp_to = fields.Date.to_date(expense.to_date)
            if date_from < exp_from or date_from > exp_to:
                raise ValidationError(
                    _('Departure Date must be between %s and %s.')
                    % (exp_from, exp_to)
                )
            if date_to < exp_from or date_to > exp_to:
                raise ValidationError(
                    _('Arrival Date must be between %s and %s.')
                    % (exp_from, exp_to)
                )
            if date_from > date_to:
                raise ValidationError(
                    _('Departure Date cannot be later than Arrival Date.')
                )

    @api.model
    def _cron_send_settlement_summary_to_billing(self):
        """
        Daily summary email to Billing/Accounts group for all pending advance settlements
        """

        AdvanceLine = self.env['advance.payment.line']
        pending_lines = AdvanceLine.search([
            ('line_state', '=', 'confirmed'),
            ('expense_id', '=', False),
        ])

        if not pending_lines:
            return True

        Mail = self.env['mail.mail'].sudo()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        today = fields.Date.today()
        sender = self.env.company.email or 'noreply@yourcompany.com'

        # 🔹 Get Billing Group Users (change group XML ID if needed)
        group = self.env.ref('account.group_account_invoice', raise_if_not_found=False)
        if not group:
            return True

        emails = group.user_ids.mapped('partner_id.email')
        emails = list(filter(None, emails))

        if not emails:
            return True

        rows = ""
        total = 0.0
        symbol = pending_lines[:1].currency_id.symbol or ''

        for line in pending_lines:
            url = f"{base_url}/web#id={line.advance_id.id}&model=advance.payment&view_type=form"
            days_pending = (today - line.date).days if line.date else 0
            total += line.amount

            rows += f"""
                   <tr>
                       <td style="border:1px solid #ddd; padding:8px;">{line.advance_id.name or ''}</td>
                       <td style="border:1px solid #ddd; padding:8px;">{line.employee_id.name or ''}</td>
                       <td style="border:1px solid #ddd; padding:8px;">{line.date or ''}</td>
                       <td style="border:1px solid #ddd; padding:8px; text-align:right;">
                           {line.currency_id.symbol or ''} {line.amount:,.2f}
                       </td>
                       <td style="border:1px solid #ddd; padding:8px; text-align:center;">{days_pending}</td>
                       <td style="border:1px solid #ddd; padding:8px;">{line.remark or '-'}</td>
                       <td style="border:1px solid #ddd; padding:8px; text-align:center;">
                           <a href="{url}"
                              style="background-color:#875A7B; color:#ffffff;
                                     padding:6px 12px; text-decoration:none; border-radius:4px;">
                               View
                           </a>
                       </td>
                   </tr>
               """

        body = f"""
               <div style="font-family: Arial, sans-serif; font-size: 14px;">
                   <p>Dear Billing Team,</p>

                   <p>The following advance payments are <b>pending settlement</b>:</p>

                   <table style="border-collapse: collapse; width:100%;">
                       <thead>
                           <tr style="background-color:#875A7B; color:#ffffff;">
                               <th style="border:1px solid #ddd; padding:8px;">Reference</th>
                               <th style="border:1px solid #ddd; padding:8px;">Employee</th>
                               <th style="border:1px solid #ddd; padding:8px;">Advance Date</th>
                               <th style="border:1px solid #ddd; padding:8px;">Amount</th>
                               <th style="border:1px solid #ddd; padding:8px;">Days Pending</th>
                               <th style="border:1px solid #ddd; padding:8px;">Reason</th>
                               <th style="border:1px solid #ddd; padding:8px;">Action</th>
                           </tr>
                       </thead>
                       <tbody>{rows}</tbody>
                   </table>

                   <p style="margin-top:12px;">
                       <b>Total Pending: {symbol} {total:,.2f}</b>
                   </p>

                   <br/>
                   <p>Regards,<br/>Axles India</p>
               </div>
           """

        Mail.create({
            'subject': _('Daily Summary: Pending Advance Settlements'),
            'body_html': body,
            'email_to': ",".join(emails),
            'email_from': sender,
        }).send()

        return True


class HrExpenseSummary(models.Model):
    _name = 'hr.expense.summary'
    _description = 'Expense Summary'
    _rec_name = 'expense_id'

    expense_id = fields.Many2one(
        'hr.expense',
        string='Expense',
        required=True,
        ondelete='cascade'
    )
    currency_id = fields.Many2one(
        related='expense_id.currency_id',
        store=True
    )
    total_amount = fields.Monetary(
        string="Total Amount",
        currency_field='currency_id'
    )
    advance_amount = fields.Monetary(
        string="Advance Paid",
        currency_field='currency_id'
    )
    balance_amount = fields.Monetary(
        string="Balance Amount",
        currency_field='currency_id'
    )
    summary_line_ids = fields.One2many(
        'hr.expense.summary.line',
        'summary_id',
        string='Summary Lines'
    )


class HrExpenseSummaryLine(models.Model):
    _name = 'hr.expense.summary.line'
    _description = 'Expense Summary Line'

    summary_id = fields.Many2one(
        'hr.expense.summary',
        ondelete='cascade'
    )
    description = fields.Char()
    category_id = fields.Many2one('product.product')
    amount = fields.Monetary(
        currency_field='currency_id'
    )
    currency_id = fields.Many2one(
        related='summary_id.currency_id',
        store=True
    )


class HrExpenseReminders(models.Model):
    """Reminders that ran on hr.expense.sheet in Odoo 17."""
    _inherit = 'hr.expense'

    @api.model
    def _cron_send_payment_reminder_to_accounts(self):
        """
        Daily reminder to Finance/Accounts users for expenses that have been
        approved by HOD but payment is still pending (action_paid not done).
        """
        pending_sheets = self.search([('state', 'in', ('approved', 'accountant'))])
        if not pending_sheets:
            return True

        finance_group = self.env.ref('account.group_account_invoice', raise_if_not_found=False)
        if not finance_group:
            return True
        finance_users = finance_group.user_ids.filtered(lambda u: u.email)
        if not finance_users:
            return True

        Mail = self.env['mail.mail'].sudo()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        today = fields.Date.today()

        # Build the table rows once — same content goes to every finance user
        rows = ""
        for sheet in pending_sheets:
            url = sheet._expense_url()
            approve_date = sheet.write_date.date() if sheet.write_date else today
            days_pending = (today - approve_date).days
            rows += f"""
                    <tr>
                        <td style="border:1px solid #ddd; padding:8px;">{sheet.name or ''}</td>
                        <td style="border:1px solid #ddd; padding:8px;">{sheet.employee_id.name or ''}</td>
                        <td style="border:1px solid #ddd; padding:8px; text-align:right;">
                            {sheet.currency_id.symbol or ''} {sheet.total_amount_currency:,.2f}
                        </td>
                        <td style="border:1px solid #ddd; padding:8px; text-align:center;">{days_pending}</td>
                        <td style="border:1px solid #ddd; padding:8px; text-align:center;">
                            <a href="{url}"
                               style="background-color:#28a745; color:#ffffff;
                                      padding:6px 12px; text-decoration:none; border-radius:4px;">
                                View
                            </a>
                        </td>
                    </tr>
                """

        sender = self.env.company.email or 'noreply@yourcompany.com'

        for user in finance_users:
            body = f"""
                    <div style="font-family: Arial, sans-serif; font-size: 14px;">
                        <p>Dear {user.name},</p>
                        <p>The following expense(s) have been <b>approved</b> and are
                           <b>pending payment</b>. Kindly process them at the earliest.</p>
                        <table style="border-collapse: collapse; width:100%;">
                            <thead>
                                <tr style="background-color:#875A7B; color:#ffffff;">
                                    <th style="border:1px solid #ddd; padding:8px;">Report</th>
                                    <th style="border:1px solid #ddd; padding:8px;">Employee</th>
                                    <th style="border:1px solid #ddd; padding:8px;">Amount</th>
                                    <th style="border:1px solid #ddd; padding:8px;">Days Pending</th>
                                    <th style="border:1px solid #ddd; padding:8px;">Action</th>
                                </tr>
                            </thead>
                            <tbody>{rows}</tbody>
                        </table>
                        <br/>
                        <p>Regards,<br/>Axles India</p>
                    </div>
                """
            Mail.sudo().create({
                'subject': _('Daily Reminder: %s Expense Payment(s) Pending') % len(pending_sheets),
                'body_html': body,
                'email_to': user.email,
                'email_from': sender,
            }).sudo().send()

        return True

    @api.model
    def _cron_send_settlement_reminder_to_employees(self):
        """
        Daily reminder for confirmed advances where the employee has NOT
        yet created an expense record.
        Conditions:
          - advance.payment.line.line_state == 'confirmed'
          - advance.payment.line.expense_id is False  (line not adjusted)
          - advance.payment.expense_id is False       (parent advance not linked
                                                        to any hr.expense either)
        """
        AdvanceLine = self.env['advance.payment.line']
        pending_lines = AdvanceLine.search([
            ('line_state', '=', 'confirmed'),
            ('expense_id', '=', False),
            ('advance_id.expense_id', '=', False),
        ])
        if not pending_lines:
            return True

        Mail = self.env['mail.mail'].sudo()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        today = fields.Date.today()
        sender = self.env.company.email or 'noreply@yourcompany.com'

        for employee in pending_lines.mapped('employee_id'):
            if not employee.work_email:
                continue
            emp_lines = pending_lines.filtered(lambda l: l.employee_id == employee)

            rows = ""
            total = 0.0
            symbol = emp_lines[:1].currency_id.symbol or ''
            for line in emp_lines:
                url = f"{base_url}/web#id={line.advance_id.id}&model=advance.payment&view_type=form"
                days_pending = (today - line.date).days if line.date else 0
                total += line.amount
                rows += f"""
                    <tr>
                        <td style="border:1px solid #ddd; padding:8px;">{line.advance_id.name or ''}</td>
                        <td style="border:1px solid #ddd; padding:8px;">{line.date or ''}</td>
                        <td style="border:1px solid #ddd; padding:8px; text-align:right;">
                            {line.currency_id.symbol or ''} {line.amount:,.2f}
                        </td>
                        <td style="border:1px solid #ddd; padding:8px; text-align:center;">{days_pending}</td>
                        <td style="border:1px solid #ddd; padding:8px;">{line.remark or '-'}</td>
                        <td style="border:1px solid #ddd; padding:8px; text-align:center;">
                            <a href="{url}"
                               style="background-color:#875A7B; color:#ffffff;
                                      padding:6px 12px; text-decoration:none; border-radius:4px;">
                                View
                            </a>
                        </td>
                    </tr>
                """

            body = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px;">
                    <p>Dear {employee.name},</p>
                    <p>The following advance payment(s) issued to you have been
                       <b>confirmed</b> but <b>no expense report has been created</b>
                       to settle them. Please create your expense report and adjust
                       the advance at the earliest.</p>
                    <table style="border-collapse: collapse; width:100%;">
                        <thead>
                            <tr style="background-color:#875A7B; color:#ffffff;">
                                <th style="border:1px solid #ddd; padding:8px;">Reference</th>
                                <th style="border:1px solid #ddd; padding:8px;">Advance Date</th>
                                <th style="border:1px solid #ddd; padding:8px;">Amount</th>
                                <th style="border:1px solid #ddd; padding:8px;">Days Outstanding</th>
                                <th style="border:1px solid #ddd; padding:8px;">Reason</th>
                                <th style="border:1px solid #ddd; padding:8px;">Action</th>
                            </tr>
                        </thead>
                        <tbody>{rows}</tbody>
                    </table>
                    <p style="margin-top:12px;">
                        <b>Total Outstanding: {symbol} {total:,.2f}</b>
                    </p>
                    <br/>
                    <p>Regards,<br/>Axles India</p>
                </div>
            """

            Mail.sudo().create({
                'subject': _('Outstanding Advance: Please Create Your Expense Report'),
                'body_html': body,
                'email_to': employee.work_email,
                'email_from': sender,
            }).sudo().send()

        return True


class TravelGrade(models.Model):
    _name = 'travel.grade'
    _description = 'Travel Grade Limit'

    upload_id = fields.Many2one('product.product',)
    grade_categ_id = fields.Many2one('travel.grade.category')
    max_amount = fields.Float()


class TravelGradeCategory(models.Model):
    _name = 'travel.grade.category'
    _description = 'Travel Grade'

    name = fields.Char()
    max_amount = fields.Float()


class ProductProduct(models.Model):
    _inherit = 'product.product'

    travel_line_ids = fields.One2many('travel.grade','upload_id')
    expense_type = fields.Selection([('self','Self'),('company','Company'),('either','Self or Company')],default='self',required=True)


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    grade_id = fields.Many2one(
        'travel.grade',
        string="Travel Grade"
    )