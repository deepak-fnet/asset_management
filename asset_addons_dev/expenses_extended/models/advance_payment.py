from odoo import models, fields, api, _
from odoo.exceptions import UserError
from markupsafe import Markup


class AdvancePayment(models.Model):
    _name = 'advance.payment'
    _description = 'Advance Payment'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _rec_name = 'name'

    name = fields.Char(
        string="Reference",
        required=True,
        default='New',
        readonly=True,
        copy=False,
    )
    date = fields.Date(
        string="Date",
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    user_id = fields.Many2one(
        'res.users',
        string="Created By",
        default=lambda self: self.env.user,
        readonly=True,
        tracking=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        string="Currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string="Company",
        default=lambda self: self.env.company,
        required=True,
    )
    state = fields.Selection([
        ('draft', 'Draft'),
        ('request', 'Request'),
        ('approved', 'approved'),
        ('confirmed', 'Paid'),
    ], string="Status", default='draft', tracking=True)

    line_ids = fields.One2many(
        'advance.payment.line',
        'advance_id',
        string="Advance Lines",
    )
    total_amount = fields.Monetary(
        string="Total Advance",
        currency_field='currency_id',
        compute='_compute_total_amount',
        store=True,
    )
    is_created_by_user = fields.Boolean()
    expense_id = fields.Many2one('hr.expense')
    pending_advance_line_ids = fields.Many2many(
        'advance.payment.line',
        string='Pending Advance Lines',
        compute='_compute_pending_advance_lines',
        store=False,
    )
    pending_advance_count = fields.Integer(
        string='Pending Advance Count',
        compute='_compute_pending_advance_lines',
        store=False,
    )
    pending_advance_total = fields.Monetary(
        string='Pending Advance Total',
        compute='_compute_pending_advance_lines',
        store=False,
        currency_field='currency_id',
    )
    is_manager_user = fields.Boolean(
        compute="_compute_is_manager_user"
    )
    employee_id = fields.Many2one(
        'hr.employee',
        string="Employee",
        default=lambda self: self.env['hr.employee'].search([
            ('user_id', '=', self.env.uid)
        ], limit=1),
    )

    employee_no = fields.Char(
        string="Employee No",
        related="employee_id.identification_id",
        store=True
    )
    department_id = fields.Many2one(
        'hr.department',
        string="Department",
        related='employee_id.department_id',
        store=True,
    )
    description = fields.Text()

    amount = fields.Monetary(
        string="Amount",
        currency_field='currency_id',
        store=True,
        default=1
    )
    is_expense_user = fields.Boolean()
    has_billing_access = fields.Boolean(
        string="Has Billing Access",
        compute='_compute_has_billing_access',
    )
    remarks = fields.Char()

    def _compute_has_billing_access(self):
        user = self.env.user
        has_access = user.has_group('account.group_account_invoice')
        for rec in self:
            rec.has_billing_access = has_access

    @api.constrains('amount')
    def _onchange_amount(self):
        for rec in self:
            if rec.is_created_by_user:
                if rec.line_ids:
                    rec.line_ids[0].amount = rec.amount

    def action_view_form(self):
        self.ensure_one()
        return {
            'name': 'Form',
            'type': 'ir.actions.act_window',
            'res_model': 'advance.payment',
            'view_mode': 'form',
            'views': [
                (self.env.ref('expenses_extended.advance_payment_user_view_form').id, 'form'),
            ],
            'res_id': self.id,
            'target': 'current',
        }

    @api.depends('employee_id.parent_id.user_id')
    def _compute_is_manager_user(self):
        current_user = self.env.user
        for rec in self:
            manager_user = rec.employee_id.parent_id.user_id
            rec.is_manager_user = (manager_user == current_user)

    @api.depends('line_ids', 'line_ids.employee_id', 'line_ids.line_state', 'line_ids.expense_id')
    def _compute_pending_advance_lines(self):
        for rec in self:
            if rec.line_ids:
                lines = self.env['advance.payment.line'].search([
                    ('employee_id', 'in', rec.line_ids.mapped('employee_id').ids),
                    ('line_state', '=', 'confirmed'),
                    ('expense_id', '=', False),
                ])
                rec.pending_advance_line_ids = lines
                rec.pending_advance_count = len(lines)
                rec.pending_advance_total = sum(lines.mapped('amount'))
            else:
                rec.pending_advance_line_ids = False
                rec.pending_advance_count = 0
                rec.pending_advance_total = 0.0

    @api.depends('line_ids.amount')
    def _compute_total_amount(self):
        for rec in self:
            rec.total_amount = sum(rec.line_ids.mapped('amount'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'advance.payment'
                ) or 'ADV/'
        record = super().create(vals_list)
        if record.is_created_by_user:
            record.write({
                'line_ids': [(0, 0, {
                    'employee_id': record.employee_id.id,
                    'date': record.date,
                    'amount': record.amount,
                    'line_state': 'draft',
                })]
            })

            return record
        return record

    def action_request(self):
        Mail = self.env['mail.mail'].sudo()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        sender = self.env.company.email or 'noreply@yourcompany.com'

        # Get Accounts/Billing group
        email = self.employee_id.parent_id.work_email
        for rec in self:
            rec.state = 'request'

            if not email:
                continue

            url = f"{base_url}/web#id={rec.id}&model=advance.payment&view_type=form"

            # Prepare line details
            rows = ""
            for line in rec.line_ids:
                rows += f"""
                            <tr>
                                <td style="border:1px solid #ddd; padding:8px;">{line.employee_id.name}</td>
                                <td style="border:1px solid #ddd; padding:8px;">{line.date}</td>
                                <td style="border:1px solid #ddd; padding:8px;">
                                    {line.currency_id.symbol} {line.amount:,.2f}
                                </td>
                                <td style="border:1px solid #ddd; padding:8px;">{self.description or '-'}</td>
                            </tr>
                        """

            body = f"""
                        <div style="font-family: Arial, sans-serif; font-size: 14px;">
                            <p>Dear {self.employee_id.parent_id.name},</p>

                            <p>An advance is <b>Requested by {self.employee_id.name}</b> and is ready for processing.</p>

                            <table style="border-collapse: collapse; width:100%;">
                                <thead>
                                    <tr style="background-color:#875A7B; color:#ffffff;">
                                        <th style="border:1px solid #ddd; padding:8px;">Employee</th>
                                        <th style="border:1px solid #ddd; padding:8px;">Date</th>
                                        <th style="border:1px solid #ddd; padding:8px;">Amount</th>
                                        <th style="border:1px solid #ddd; padding:8px;">Reason</th>
                                    </tr>
                                </thead>
                                <tbody>{rows}</tbody>
                            </table>

                            <p style="margin-top:12px;">
                                <b>Total Amount: {rec.currency_id.symbol} {rec.total_amount:,.2f}</b>
                            </p>

                            <p>
                                <a href="{url}"
                                   style="background-color:#875A7B; color:#ffffff;
                                          padding:8px 14px; text-decoration:none; border-radius:4px;">
                                    View Advance
                                </a>
                            </p>

                            <br/>
                            <p>Regards,<br/>Axles India</p>
                        </div>
                    """

            Mail.sudo().create({
                'subject': _('Advance Request - Action Required'),
                'body_html': body,
                'email_to': email,
                'email_from': sender,
            }).sudo().send()


    def action_manager_approve(self):
        Mail = self.env['mail.mail'].sudo()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        sender = self.env.company.email or 'noreply@yourcompany.com'

        # Get Accounts/Billing group
        group = self.env.ref('account.group_account_invoice', raise_if_not_found=False)
        emails = group.user_ids.mapped('partner_id.email') if group else []
        emails = list(filter(None, emails))

        for rec in self:
            rec.state = 'approved'

            if not emails:
                continue

            url = f"{base_url}/web#id={rec.id}&model=advance.payment&view_type=form"

            # Prepare line details
            rows = ""
            for line in rec.line_ids:
                rows += f"""
                    <tr>
                        <td style="border:1px solid #ddd; padding:8px;">{line.employee_id.name}</td>
                        <td style="border:1px solid #ddd; padding:8px;">{line.date}</td>
                        <td style="border:1px solid #ddd; padding:8px;">
                            {line.currency_id.symbol} {line.amount:,.2f}
                        </td>
                        <td style="border:1px solid #ddd; padding:8px;">{self.description or '-'}</td>
                    </tr>
                """

            body = f"""
                <div style="font-family: Arial, sans-serif; font-size: 14px;">
                    <p>Dear Accounts Team,</p>

                    <p>An advance request has been <b>approved by manager</b> and is ready for processing.</p>

                    <table style="border-collapse: collapse; width:100%;">
                        <thead>
                            <tr style="background-color:#875A7B; color:#ffffff;">
                                <th style="border:1px solid #ddd; padding:8px;">Employee</th>
                                <th style="border:1px solid #ddd; padding:8px;">Date</th>
                                <th style="border:1px solid #ddd; padding:8px;">Amount</th>
                                <th style="border:1px solid #ddd; padding:8px;">Reason</th>
                            </tr>
                        </thead>
                        <tbody>{rows}</tbody>
                    </table>

                    <p style="margin-top:12px;">
                        <b>Total Amount: {rec.currency_id.symbol} {rec.total_amount:,.2f}</b>
                    </p>

                    <p>
                        <a href="{url}"
                           style="background-color:#875A7B; color:#ffffff;
                                  padding:8px 14px; text-decoration:none; border-radius:4px;">
                            View Advance
                        </a>
                    </p>

                    <br/>
                    <p>Regards,<br/>Axles India</p>
                </div>
            """

            Mail.create({
                'subject': _('Advance Approved - Action Required'),
                'body_html': body,
                'email_to': ",".join(emails),
                'email_from': sender,
            }).send()

        return True

    def action_confirm(self):
        """Confirm all lines in this advance payment."""
        for rec in self:
            for line in rec.line_ids:
                if line.amount <= 0:
                    raise UserError(
                        _("Amount must be greater than zero for employee %s.") % line.employee_id.name
                    )
                line.line_state = 'confirmed'
            rec.state = 'confirmed'
            # Post chatter message
            body = Markup(
                "<b>Advance Payment Confirmed</b><br/>"
                "Total: %s %s<br/>"
                "Lines: %s<br/>"
                "Confirmed By: %s"
            ) % (
                rec.currency_id.symbol,
                rec.total_amount,
                len(rec.line_ids),
                self.env.user.name,
            )
            rec.with_context(mail_create_nosubscribe=True).message_post(
                body=body,
                message_type='notification',
                subtype_xmlid='mail.mt_note',
            )
            # Send mail to employee
            employee_email = rec.employee_id.work_email
            if employee_email:
                mail_values = {
                    'subject': 'Advance Request Paid',
                    'body_html': """
                                    <p>Dear %s,</p>
                                    <p>Your advance request has been paid successfully.</p>
                                    <p>Amount: %s %s</p>
                                    <p>Regards,<br/>Axles India</p>
                                """ % (
                        rec.employee_id.name,
                        rec.currency_id.symbol,
                        rec.amount
                    ),
                    'email_to': employee_email,
                }
                self.env['mail.mail'].sudo().create(mail_values).sudo().send()

    def action_reset_draft(self):
        """Reset to draft."""
        for rec in self:
            for line in rec.line_ids:
                if line.is_adjusted:
                    raise UserError(
                        _("Cannot reset: some lines are already adjusted against expenses.")
                    )
                line.line_state = 'draft'
            rec.state = 'draft'

    @api.model
    def _cron_send_settlement_reminder_to_employees(self):
        """
        Daily reminder to employees who received a confirmed advance but have
        NOT yet adjusted/settled it against an expense.
        Condition:
            advance.payment.line.line_state == 'confirmed'
            AND expense_id is False  (not adjusted via the wizard yet)
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

        # Group by employee so each employee gets one consolidated email
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
                            <td style="border:1px solid #ddd; padding:8px;">{self.description or '-'}</td>
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
                        <p>Our records show the following advance payment(s) issued to you are
                           <b>pending settlement</b>. Please submit your expense claim and adjust
                           the advance at the earliest.</p>
                        <table style="border-collapse: collapse; width:100%;">
                            <thead>
                                <tr style="background-color:#875A7B; color:#ffffff;">
                                    <th style="border:1px solid #ddd; padding:8px;">Reference</th>
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
                'subject': _('Reminder: Advance Settlement Pending'),
                'body_html': body,
                'email_to': employee.work_email,
                'email_from': sender,
            }).send()

        return True

    @api.model
    def _cron_send_finance_approval_reminder(self):
        """
        Daily reminder to Finance team for advances
        that are approved by manager but not yet confirmed.
        """

        records = self.search([
            ('state', '=', 'approved')
        ])
        if not records:
            return True

        Mail = self.env['mail.mail'].sudo()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        today = fields.Date.today()
        sender = self.env.company.email or 'noreply@yourcompany.com'

        # Get Accounts group
        group = self.env.ref('account.group_account_invoice', raise_if_not_found=False)
        emails = group.user_ids.mapped('partner_id.email') if group else []
        emails = list(filter(None, emails))
        print(emails)
        if not emails:
            return True

        rows = ""

        for rec in records:
            days_pending = (today - rec.date).days if rec.date else 0
            url = f"{base_url}/web#id={rec.id}&model=advance.payment&view_type=form"

            for line in rec.line_ids:
                rows += f"""
                    <tr>
                        <td style="border:1px solid #ddd; padding:8px;">{rec.name}</td>
                        <td style="border:1px solid #ddd; padding:8px;">{line.employee_id.name}</td>
                        <td style="border:1px solid #ddd; padding:8px;">{rec.date}</td>
                        <td style="border:1px solid #ddd; padding:8px;">
                            {rec.currency_id.symbol} {line.amount:,.2f}
                        </td>
                        <td style="border:1px solid #ddd; padding:8px; text-align:center;">
                            {days_pending}
                        </td>
                        <td style="border:1px solid #ddd; padding:8px;">
                            {line.remark or '-'}
                        </td>
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
                <p>Dear Finance Team,</p>

                <p>The following advance requests are <b>approved but not yet confirmed</b>.
                   Kindly process them.</p>

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

                <br/>
                <p>Regards,<br/>Axles India</p>
            </div>
        """

        Mail.sudo().create({
            'subject': _('Reminder: Approved Advances Pending Confirmation'),
            'body_html': body,
            'email_to': ",".join(emails),
            'email_from': sender,
        }).sudo().send()

        return True

class AdvancePaymentLine(models.Model):
    _name = 'advance.payment.line'
    _description = 'Advance Payment Line'
    _order = 'date desc, id desc'

    advance_id = fields.Many2one(
        'advance.payment',
        string="Advance Payment",
        required=True,
        ondelete='cascade',
    )
    employee_id = fields.Many2one(
        'hr.employee',
        string="Employee",
        required=True,
    )
    department_id = fields.Many2one(
        'hr.department',
        string="Department",
        related='employee_id.department_id',
        store=True,
    )
    date = fields.Date(
        string="Date",
        required=True,
        default=fields.Date.context_today,
    )
    user_id = fields.Many2one(
        'res.users',
        string="Entered By",
        default=lambda self: self.env.user,
        readonly=True,
    )
    amount = fields.Monetary(
        string="Amount",
        required=True,
        currency_field='currency_id',
    )
    remark = fields.Text(
        string="Reason",
    )
    currency_id = fields.Many2one(
        related='advance_id.currency_id',
        store=True,
    )
    expense_id = fields.Many2one(
        'hr.expense',
        string="Adjusted Against",
        readonly=True,
    )
    is_adjusted = fields.Boolean(
        string="Adjusted",
        compute='_compute_is_adjusted',
        store=True,
    )
    line_state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
    ], string="Status", default='draft')

    is_readonly = fields.Boolean(
        string="Readonly",
    )

    def action_open_advance(self):
        self.ensure_one()
        return {
            'name': 'Advance Payment Lines',
            'type': 'ir.actions.act_window',
            'res_model': 'advance.payment',
            'view_mode': 'tree,form',
            'domain': [('id', '=', self.advance_id.id)],
        }

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

        Mail.sudo().create({
            'subject': _('Daily Summary: Pending Advance Settlements'),
            'body_html': body,
            'email_to': ",".join(emails),
            'email_from': sender,
        }).sudo().send()

        return True

    @api.depends('expense_id')
    def _compute_is_adjusted(self):
        for line in self:
            line.is_adjusted = bool(line.expense_id)

    def action_confirm_line(self):
        """Confirm individual advance line."""
        for line in self:
            if line.amount <= 0:
                raise UserError(_("Amount must be greater than zero to confirm."))
            line.line_state = 'confirmed'
            if line.advance_id:
                body = Markup(
                    "<b>Advance Line Confirmed</b><br/>"
                    "Employee: %s<br/>"
                    "Date: %s<br/>"
                    "Amount: %s %s<br/>"
                    "Reason: %s<br/>"
                    "Confirmed By: %s"
                ) % (
                    line.employee_id.name,
                    line.date,
                    line.currency_id.symbol,
                    line.amount,
                    line.remark or '-',
                    self.env.user.name,
                )
                line.advance_id.with_context(mail_create_nosubscribe=True).message_post(
                    body=body,
                    message_type='notification',
                    subtype_xmlid='mail.mt_note',
                )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines:
            if line.advance_id:
                body = Markup(
                    "<b>Advance Line Added</b><br/>"
                    "Employee: %s<br/>"
                    "Date: %s<br/>"
                    "Amount: %s %s<br/>"
                    "Reason: %s<br/>"
                    "Entered By: %s"
                ) % (
                    line.employee_id.name,
                    line.date,
                    line.currency_id.symbol,
                    line.amount,
                    line.remark or '-',
                    line.user_id.name,
                )
                line.advance_id.with_context(mail_create_nosubscribe=True).message_post(
                    body=body,
                    message_type='notification',
                    subtype_xmlid='mail.mt_note',
                )
        return lines

    def write(self, vals):
        for line in self:
            changes = []
            if 'amount' in vals and vals['amount'] != line.amount:
                changes.append(
                    "Amount: %s %s → %s %s" % (
                        line.currency_id.symbol, line.amount,
                        line.currency_id.symbol, vals['amount'],
                    )
                )
            if 'remark' in vals and vals.get('remark') != line.remark:
                changes.append(
                    "Reason: %s → %s" % (line.remark or '-', vals.get('remark') or '-')
                )
            if changes and line.advance_id:
                body = Markup(
                    "<b>Advance Line Updated</b> (Date: %s)<br/>%s"
                ) % (line.date, Markup('<br/>').join(changes))
                line.advance_id.with_context(mail_create_nosubscribe=True).message_post(
                    body=body,
                    message_type='notification',
                    subtype_xmlid='mail.mt_note',
                )
        return super().write(vals)

