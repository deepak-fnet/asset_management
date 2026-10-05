from markupsafe import Markup

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError
from odoo.tools.misc import formatLang
from .vendor_compat_utils import group_member_emails, group_members

class AccountMove(models.Model):
    _inherit = 'account.move'

    # ------------------------------------------------------------------
    # Purchase bill approval:
    #   Upload Bill (purchase order) -> Purchase User fills it in and
    #   Submits -> Department Head approves -> Stores approves -> Finance
    #   Confirms (Odoo's own Confirm / action_post).
    # Any approver can Reject with a reason, sending it back to the
    # Purchase User. Applies to vendor bills created from a purchase order;
    # every other move posts exactly as standard Odoo. Each step emails the
    # people who have to act next.
    # ------------------------------------------------------------------
    PURCHASE_USER_GROUPS = (
        'vendor_management.group_purchase_role_user',
        'vendor_management.group_vendor_manager',
        'vendor_management.group_vendor_md',
    )
    STORES_GROUPS = (
        'vendor_management.group_purchase_role_stores',
        'vendor_management.group_vendor_manager',
        'vendor_management.group_vendor_md',
    )
    FINANCE_GROUPS = (
        'vendor_management.group_purchase_role_finance',
        'vendor_management.group_vendor_manager',
        'vendor_management.group_vendor_md',
    )
    # Manager/MD may stand in for a department head who is unavailable.
    DEPT_OVERRIDE_GROUPS = (
        'vendor_management.group_vendor_manager',
        'vendor_management.group_vendor_md',
    )

    purchase_bill_approval = fields.Boolean(
        string="Needs Purchase Approval", compute='_compute_purchase_bill_approval', store=True,
        help="Vendor bill created from a purchase order: it must be submitted "
             "and approved by the Department Head and Stores before Finance "
             "can confirm (post) it.")
    bill_approval_state = fields.Selection([
        ('draft', 'Draft'),
        ('to_dept', 'Waiting Department Head'),
        ('to_stores', 'Waiting Stores'),
        ('stores_approved', 'Waiting Finance'),
    ], string="Bill Approval", default='draft', copy=False, tracking=True)
    bill_submitted_by = fields.Many2one('res.users', string="Submitted By", copy=False, readonly=True)
    bill_dept_approver_id = fields.Many2one(
        'res.users', string="Department Head", copy=False, readonly=True,
        help="Manager of the submitting user's department (Employees), "
             "resolved when the bill is submitted.")

    @api.depends('move_type', 'invoice_line_ids.purchase_line_id')
    def _compute_purchase_bill_approval(self):
        for move in self:
            move.purchase_bill_approval = (
                move.move_type in ('in_invoice', 'in_refund')
                and bool(move.invoice_line_ids.purchase_line_id))

    # groups= on a button is static and would apply to every move, so these
    # drive visibility per user instead. The real enforcement is the checks
    # in the actions below.
    can_bill_submit = fields.Boolean(compute='_compute_bill_approval_rights')
    can_dept_approve = fields.Boolean(compute='_compute_bill_approval_rights')
    can_stores_approve = fields.Boolean(compute='_compute_bill_approval_rights')
    can_finance_post = fields.Boolean(compute='_compute_bill_approval_rights')
    can_bill_reject = fields.Boolean(compute='_compute_bill_approval_rights')

    def _user_in(self, groups):
        return any(self.env.user.has_group(g) for g in groups)

    @api.depends('bill_approval_state', 'bill_dept_approver_id')
    @api.depends_context('uid')
    def _compute_bill_approval_rights(self):
        is_buyer = self._user_in(self.PURCHASE_USER_GROUPS)
        is_stores = self._user_in(self.STORES_GROUPS)
        is_finance = self._user_in(self.FINANCE_GROUPS)
        is_override = self._user_in(self.DEPT_OVERRIDE_GROUPS)
        for move in self:
            move.can_bill_submit = is_buyer
            move.can_dept_approve = is_override or move.bill_dept_approver_id == self.env.user
            move.can_stores_approve = is_stores
            move.can_finance_post = is_finance
            move.can_bill_reject = {
                'to_dept': move.can_dept_approve,
                'to_stores': is_stores,
                'stores_approved': is_finance,
            }.get(move.bill_approval_state, False)

    # -------------------------------------------------------------- helpers
    def _bill_notify(self, users, subject, body):
        """Email + chatter message to these users (not to the actor)."""
        self.ensure_one()
        partners = (users - self.env.user).filtered('partner_id').partner_id
        if partners:
            self.message_post(body=body, subject=subject, partner_ids=partners.ids,
                              message_type='comment', subtype_xmlid='mail.mt_comment')

    def _bill_group_users(self, xmlid):
        group = self.env.ref(xmlid, raise_if_not_found=False)
        if not group:
            return self.env['res.users']
        users = group.all_user_ids if 'all_user_ids' in group._fields else group_members(group)
        return users.filtered(lambda u: u.active and not u.share)

    def _bill_label(self):
        self.ensure_one()
        amount = formatLang(self.env, self.amount_total, currency_obj=self.currency_id)
        return Markup('<b>%s</b> - %s, %s') % (
            self.name if self.name and self.name != '/' else _('Draft bill'),
            self.partner_id.display_name or '', amount)

    def _bill_find_dept_head(self, user):
        """Manager of the user's department; when the user IS that manager,
        the parent department's manager (else themselves)."""
        employee = user.employee_id or user.employee_ids[:1]
        department = employee.department_id
        while department:
            manager_user = department.manager_id.user_id
            if manager_user and manager_user != user:
                return manager_user
            if not department.parent_id:
                return manager_user
            department = department.parent_id
        return self.env['res.users']

    def _check_bill_stage(self, stage):
        for move in self:
            if not move.purchase_bill_approval or move.state != 'draft' or move.bill_approval_state != stage:
                raise UserError(_("%s is not at this approval step.", move.name or _("This bill")))

    # -------------------------------------------------------------- actions
    def action_bill_submit(self):
        if not self._user_in(self.PURCHASE_USER_GROUPS):
            raise AccessError(_("Only a Purchase User can submit this bill."))
        self._check_bill_stage('draft')
        for move in self:
            if not move.invoice_date:
                raise UserError(_("Enter the Bill Date before submitting."))
            if not move.ref:
                raise UserError(_("Enter the Bill Reference (vendor's bill number) before submitting."))
            head = move._bill_find_dept_head(self.env.user)
            if not head:
                raise UserError(_(
                    "No Department Head found for %s. In Employees, link this user to an "
                    "employee whose department has a Manager (with a user).", self.env.user.name))
            # The head approves via the bill itself (and the email links to
            # it), so they must at least be able to view bills.
            if not self.env['ir.model.access'].with_user(head).check('account.move', 'read', raise_exception=False):
                raise UserError(_(
                    "%s is the Department Head but cannot view bills. Give this user "
                    "Invoicing 'Billing' or Accounting 'Read-only' access (Settings > Users).",
                    head.name))
            move.write({
                'bill_approval_state': 'to_dept',
                'bill_submitted_by': self.env.user.id,
                'bill_dept_approver_id': head.id,
            })
            move.message_post(body=_("Submitted for approval to %s (Department Head).", head.name))
            move._bill_notify(head, _("Bill approval required: %s", move.ref),
                              Markup('<p>%s was submitted by %s and is waiting for your approval '
                                     'as Department Head.</p>') % (move._bill_label(), self.env.user.name))

    def action_dept_approve(self):
        self._check_bill_stage('to_dept')
        for move in self:
            if not (move.bill_dept_approver_id == self.env.user or self._user_in(self.DEPT_OVERRIDE_GROUPS)):
                raise AccessError(_("Only %s (Department Head) can approve this bill.",
                                    move.bill_dept_approver_id.name))
            # A department head typically has read-only (or no) accounting
            # rights: they are authorised just above, so record the step
            # with elevated rights. sudo() keeps the uid - the chatter still
            # shows who approved.
            move = move.sudo()
            move.bill_approval_state = 'to_stores'
            move.message_post(body=_("Approved by Department Head (%s).", self.env.user.name))
            move._bill_notify(move._bill_group_users('vendor_management.group_purchase_role_stores'),
                              _("Bill waiting for Stores approval: %s", move.ref),
                              Markup('<p>%s was approved by the Department Head and is waiting '
                                     'for Stores approval.</p>') % move._bill_label())

    def action_stores_approve(self):
        if not self._user_in(self.STORES_GROUPS):
            raise AccessError(_("Only Purchase Stores can approve this bill."))
        self._check_bill_stage('to_stores')
        for move in self:
            move.bill_approval_state = 'stores_approved'
            move.message_post(body=_("Approved by Stores (%s).", self.env.user.name))
            move._bill_notify(move._bill_group_users('vendor_management.group_purchase_role_finance'),
                              _("Bill waiting for Finance confirmation: %s", move.ref),
                              Markup('<p>%s was approved by Stores and is waiting for Finance '
                                     'to confirm it.</p>') % move._bill_label())

    def action_bill_reject(self):
        self.ensure_one()
        if not self.can_bill_reject:
            raise AccessError(_("You can't reject this bill at its current step."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Reject Bill'),
            'res_model': 'purchase.bill.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_move_id': self.id},
        }

    def _bill_reject(self, reason):
        self.ensure_one()
        if not self.can_bill_reject:
            raise AccessError(_("You can't reject this bill at its current step."))
        step = dict(self._fields['bill_approval_state']._description_selection(self.env)).get(
            self.bill_approval_state)
        # Authorised just above; the rejecting department head may have only
        # read access to bills (see action_dept_approve).
        self = self.sudo()
        self.bill_approval_state = 'draft'
        self.message_post(body=Markup('<p>Rejected by %s (at: %s).</p><p><b>Reason:</b> %s</p>') % (
            self.env.user.name, step, reason))
        self._bill_notify(self.bill_submitted_by,
                          _("Bill rejected: %s", self.ref or self.name),
                          Markup('<p>%s was <b>rejected</b> by %s.</p><p><b>Reason:</b> %s</p>'
                                 '<p>Please correct it and submit it again.</p>') % (
                              self._bill_label(), self.env.user.name, reason))

    def action_post(self):
        for move in self.filtered('purchase_bill_approval'):
            if move.bill_approval_state != 'stores_approved':
                raise UserError(_(
                    "%s must be submitted and approved by the Department Head and Stores "
                    "before it can be confirmed.", move.name or _("This bill")))
            if not self._user_in(self.FINANCE_GROUPS):
                raise AccessError(_("Only Purchase Finance can confirm (post) this bill."))
        res = super().action_post()
        for move in self.filtered('purchase_bill_approval'):
            move._bill_notify(move.bill_submitted_by | move.bill_dept_approver_id,
                              _("Bill confirmed: %s", move.ref or move.name),
                              Markup('<p>%s was confirmed by Finance (%s).</p>') % (
                                  move._bill_label(), self.env.user.name))
        return res

    def button_draft(self):
        # Reset to draft = changed after approval: the whole chain again.
        res = super().button_draft()
        self.filtered('purchase_bill_approval').write({'bill_approval_state': 'draft'})
        return res

    def write(self, vals):
        res = super().write(vals)
        if 'payment_state' in vals and vals['payment_state'] in ('paid', 'in_payment'):
            for rec in self:
                if rec.move_type == 'in_invoice' and rec.partner_id.is_vendor:
                    rec._send_payment_notification()
        return res

    def _send_payment_notification(self):
        self.ensure_one()
        template = self.env.ref('vendor_management.mail_template_bill_paid', raise_if_not_found=False)
        if template:
            md_group = self.env.ref('vendor_management.group_vendor_md')
            email_cc = group_member_emails(md_group)
            template.send_mail(self.id, force_send=True, email_values={'email_cc': email_cc})

    is_rated = fields.Boolean(compute='_compute_is_rated', store=False)

    def _compute_is_rated(self):
        for rec in self:
            rec.is_rated = self.env['vendor.performance.overview'].search_count([
                ('vendor_id', '=', rec.partner_id.id),
                '|',
                ('move_id', '=', rec.id),
                ('purchase_id', '=', rec.purchase_id.id) if rec.purchase_id else ('id', '=', 0)
            ]) > 0

    def action_open_rating_wizard(self):
        self.ensure_one()
        return {
            'name': _('Rate Vendor'),
            'type': 'ir.actions.act_window',
            'res_model': 'vendor.rating.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_purchase_id': self.purchase_id.id if self.purchase_id else False,
                'default_move_id': self.id,
            }
        }
