from markupsafe import Markup

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError
from odoo.tools.float_utils import float_compare
from odoo.tools.misc import formatLang
from .vendor_compat_utils import find_department_head

# ------------------------------------------------------------------
# Advance payment on a purchase order:
#   Payment Terms carry an Advance % -> after Confirm Order the Purchase
#   User clicks Request Advance (a draft vendor payment, Purchase Finance
#   emailed) -> Finance Confirms (posts) or Rejects it with a reason.
#   When the bill is confirmed the paid advances are matched against it
#   automatically, so the bill shows only the balance due.
# ------------------------------------------------------------------
PURCHASE_USER_GROUPS = (
    'vendor_management.group_purchase_role_user',
    'vendor_management.group_vendor_manager',
    'vendor_management.group_vendor_md',
)
MANAGER_OVERRIDE_GROUPS = (
    'purchase.group_purchase_manager',
    'vendor_management.group_vendor_manager',
    'vendor_management.group_vendor_md',
)
FINANCE_GROUPS = (
    'vendor_management.group_purchase_role_finance',
    'vendor_management.group_vendor_manager',
    'vendor_management.group_vendor_md',
)


def _user_in(env, groups):
    return any(env.user.has_group(g) for g in groups)


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    purchase_advance_id = fields.Many2one(
        'purchase.order', string="Advance for PO", copy=False, readonly=True, index='btree_not_null')
    advance_state = fields.Selection([
        ('to_manager', 'Waiting Manager'),
        ('requested', 'Requested'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], string="Advance Status", copy=False, readonly=True, tracking=True)
    advance_requested_by = fields.Many2one('res.users', string="Requested By", copy=False, readonly=True)
    advance_manager_id = fields.Many2one(
        'res.users', string="Department Manager", copy=False, readonly=True,
        help="Manager of the requester's department, who approves the advance before Finance.")
    advance_invoice_ids = fields.Many2many(
        'ir.attachment', 'purchase_advance_invoice_rel', 'payment_id', 'attachment_id',
        string="Vendor Invoice", copy=False)
    can_advance_manager = fields.Boolean(compute='_compute_can_advance_finance')
    can_advance_finance = fields.Boolean(compute='_compute_can_advance_finance')

    @api.depends_context('uid')
    def _compute_can_advance_finance(self):
        is_finance = _user_in(self.env, FINANCE_GROUPS)
        override = _user_in(self.env, MANAGER_OVERRIDE_GROUPS)
        for pay in self:
            pay.can_advance_finance = is_finance
            pay.can_advance_manager = override or pay.advance_manager_id == self.env.user

    def action_post(self):
        advances = self.filtered('purchase_advance_id')
        if advances:
            if not _user_in(self.env, FINANCE_GROUPS):
                raise AccessError(_("Only Purchase Finance can confirm an advance payment."))
            if advances.filtered(lambda p: p.advance_state != 'requested'):
                raise UserError(_("Only a requested advance can be confirmed."))
        res = super().action_post()
        for pay in advances:
            pay.advance_state = 'approved'
            order = pay.purchase_advance_id
            order._advance_notify(
                pay.advance_requested_by,
                _("Advance paid: %s", order.name),
                Markup('<p>Advance %s for %s was confirmed by Finance (%s).</p>') % (
                    pay._advance_label(), order.name, self.env.user.name))
            # Bill confirmed before the advance? Match it right away.
            order._reconcile_advances()
        return res

    def _advance_label(self):
        self.ensure_one()
        return Markup('<b>%s</b>') % formatLang(self.env, self.amount, currency_obj=self.currency_id)

    def _check_can_decide_advance(self):
        # Waiting Manager: the department manager decides; Requested: Finance.
        self.ensure_one()
        if self.state != 'draft' or self.advance_state not in ('to_manager', 'requested'):
            raise UserError(_("This advance is not waiting for a decision."))
        if self.advance_state == 'to_manager' and not self.can_advance_manager:
            raise AccessError(_("Only %s (the requester's department manager) can decide this advance.",
                                self.advance_manager_id.name))
        if self.advance_state == 'requested' and not _user_in(self.env, FINANCE_GROUPS):
            raise AccessError(_("Only Purchase Finance can decide this advance."))

    def action_advance_manager_approve(self):
        for pay in self:
            if pay.advance_state != 'to_manager' or pay.state != 'draft':
                raise UserError(_("This advance is not waiting for the manager."))
            pay._check_can_decide_advance()
            pay = pay.sudo()
            pay.advance_state = 'requested'
            order = pay.purchase_advance_id
            pay.message_post(body=_("Approved by the department manager (%s).", self.env.user.name))
            order._advance_notify(
                order._advance_finance_users(), _("Advance payment request: %s", order.name),
                Markup('<p>%s approved an advance of %s to %s for %s.</p>'
                       '<p>Please confirm or reject the payment <a href="/odoo/account.payment/%s">%s</a>.</p>') % (
                    self.env.user.name, pay._advance_label(), order.partner_id.display_name, order.name,
                    pay.id, pay.name or _('Draft payment')))
        return True

    def action_advance_reject(self):
        self.ensure_one()
        self._check_can_decide_advance()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Reject Advance'),
            'res_model': 'purchase.advance.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_payment_id': self.id},
        }

    def _advance_reject(self, reason):
        self.ensure_one()
        self._check_can_decide_advance()
        self = self.sudo()
        self.action_cancel()
        self.advance_state = 'rejected'
        order = self.purchase_advance_id
        msg = Markup('<p>Advance %s for %s was <b>rejected</b> by %s.</p><p><b>Reason:</b> %s</p>') % (
            self._advance_label(), order.name, self.env.user.name, reason)
        self.message_post(body=msg)
        order._advance_notify(self.advance_requested_by, _("Advance rejected: %s", order.name), msg)


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    advance_payment_ids = fields.One2many('account.payment', 'purchase_advance_id', string="Advances")
    advance_count = fields.Integer(compute='_compute_advance_amounts')
    advance_balance = fields.Monetary(compute='_compute_advance_amounts', string="Advance Balance",
                                      help="Order total not yet requested as advance.")
    goods_received = fields.Boolean(compute='_compute_advance_amounts')
    advance_requested = fields.Monetary(compute='_compute_advance_amounts', string="Advance Requested")
    advance_paid = fields.Monetary(compute='_compute_advance_amounts', string="Advance Paid")
    can_request_advance = fields.Boolean(compute='_compute_advance_amounts')

    @api.depends('amount_total', 'state', 'order_line.qty_received',
                 'advance_payment_ids.amount', 'advance_payment_ids.state', 'advance_payment_ids.advance_state',
                 'advance_payment_ids.advance_invoice_ids')
    @api.depends_context('uid')
    def _compute_advance_amounts(self):
        is_buyer = _user_in(self.env, PURCHASE_USER_GROUPS)
        for order in self:
            # Amounts only: Stores and approvers open the order without payment rights.
            payments = order.sudo().advance_payment_ids
            live = payments.filtered(
                lambda p: p.advance_state in ('to_manager', 'requested', 'approved'))
            order.advance_count = len(payments)
            order.advance_invoice_count = len(payments.advance_invoice_ids)
            order.advance_requested = sum(live.mapped('amount'))
            order.advance_paid = sum(live.filtered(lambda p: p.advance_state == 'approved').mapped('amount'))
            order.advance_balance = max(order.amount_total - order.advance_requested, 0.0)
            order.goods_received = any(
                l.qty_received > 0 for l in order.order_line if not l.display_type)
            order.order_fully_paid = order._is_fully_paid()
            # Any time after Confirm Order (before or after the goods arrive)
            # until the order is fully paid; up to the order total in all.
            order.can_request_advance = (
                is_buyer and order.state in ('purchase', 'done') and not order.order_fully_paid
                and order.currency_id.compare_amounts(order.advance_balance, 0) > 0)

    order_fully_paid = fields.Boolean(compute='_compute_advance_amounts')

    def _is_fully_paid(self):
        """Posted bills cover the whole order and are paid (Paid or In Process -
        a bank payment not reconciled yet counts as paid)."""
        self.ensure_one()
        bills = self.sudo().invoice_ids.filtered(
            lambda m: m.move_type == 'in_invoice' and m.state == 'posted')
        if not bills:
            return False
        covered = self.currency_id.compare_amounts(sum(bills.mapped('amount_total')), self.amount_total) >= 0
        return covered and all(b.payment_state in ('paid', 'in_payment') for b in bills)

    def _check_no_pending_advance(self):
        """A bill cannot go ahead while an advance on its order is still waiting
        for the manager or Finance: confirm or reject that advance first."""
        for order in self:
            pending = order.sudo().advance_payment_ids.filtered(
                lambda p: p.advance_state in ('to_manager', 'requested') and p.state == 'draft')
            if pending:
                raise UserError(_(
                    "%(order)s has an advance still waiting for approval (%(amt)s). Ask the "
                    "manager / Finance to confirm it, or reject (cancel) it, before this bill "
                    "goes ahead - otherwise the vendor would be paid twice.",
                    order=order.name, amt=", ".join(
                        formatLang(self.env, p.amount, currency_obj=p.currency_id) for p in pending)))

    def _advance_notify(self, users, subject, body):
        """Email + chatter message on the order to these users (not the actor)."""
        self.ensure_one()
        partners = (users - self.env.user).filtered('partner_id').partner_id
        if partners:
            self.message_post(body=body, subject=subject, partner_ids=partners.ids,
                              message_type='comment', subtype_xmlid='mail.mt_comment')
        else:
            self.message_post(body=body)

    def _advance_finance_users(self):
        group = self.env.ref('vendor_management.group_purchase_role_finance', raise_if_not_found=False)
        if not group:
            return self.env['res.users']
        return group.all_user_ids.filtered(lambda u: u.active and not u.share)

    def action_request_advance(self):
        self.ensure_one()
        if not self.can_request_advance:
            raise UserError(_("No advance can be requested on this order."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Request Advance'),
            'res_model': 'purchase.advance.request.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_order_id': self.id},
        }

    advance_invoice_count = fields.Integer(compute='_compute_advance_amounts')

    def _advance_needs_manager(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'vendor_management.advance_manager_approval', 'True') not in ('False', 'false', '0')

    def _request_advance(self, amount, journal, date, memo, invoices=None):
        self.ensure_one()
        if not _user_in(self.env, PURCHASE_USER_GROUPS):
            raise AccessError(_("Only a Purchase User can request an advance."))
        if self.state not in ('purchase', 'done'):
            raise UserError(_("An advance can be requested once the order is confirmed."))
        if self._is_fully_paid():
            raise UserError(_("%s is already fully paid - no advance can be requested.", self.name))
        if float_compare(amount, 0, precision_rounding=self.currency_id.rounding) <= 0:
            raise UserError(_("Enter an advance amount or percentage."))
        remaining = self.advance_balance
        if self.currency_id.compare_amounts(amount, remaining) > 0:
            raise UserError(_(
                "Advances cannot exceed the order total: at most %(left)s can still be requested.",
                left=formatLang(self.env, remaining, currency_obj=self.currency_id)))
        if not invoices:
            raise UserError(_("Attach the vendor's invoice for this advance."))
        manager = self.env['res.users']
        if self._advance_needs_manager():
            manager = find_department_head(self.env, self.env.user)
            if not manager:
                raise UserError(_(
                    "No Department Manager found for %s. In Employees, link this user to an employee "
                    "whose department has a Manager (with a user), or switch off 'Advance needs manager "
                    "approval' in the Vendor Management settings.", self.env.user.name))
            if not self.env['ir.model.access'].with_user(manager).check('account.payment', 'read', raise_exception=False):
                raise UserError(_(
                    "%s is the Department Manager but cannot view payments. Give this user Invoicing "
                    "'Billing' or Accounting 'Read-only' access (Settings > Users).", manager.name))
        # Authorised above; the buyer needn't hold payment-creation rights.
        payment = self.env['account.payment'].sudo().create({
            'payment_type': 'outbound',
            'partner_type': 'supplier',
            'partner_id': self.partner_id.commercial_partner_id.id,
            'amount': amount,
            'currency_id': self.currency_id.id,
            'journal_id': journal.id,
            'date': date,
            'memo': memo or _("Advance for %s", self.name),
            'company_id': self.company_id.id,
            'purchase_advance_id': self.id,
            'advance_state': 'to_manager' if manager else 'requested',
            'advance_requested_by': self.env.user.id,
            'advance_manager_id': manager.id,
        })
        invoices.sudo().write({'res_model': 'account.payment', 'res_id': payment.id})
        payment.sudo().advance_invoice_ids = [(6, 0, invoices.ids)]
        if manager:
            self._advance_notify(
                manager, _("Advance approval needed: %s", self.name),
                Markup('<p>%s requested an advance of %s to %s for %s (order total %s).</p>'
                       '<p>Please approve or reject it: <a href="/odoo/account.payment/%s">%s</a>.</p>') % (
                    self.env.user.name, payment._advance_label(), self.partner_id.display_name, self.name,
                    formatLang(self.env, self.amount_total, currency_obj=self.currency_id),
                    payment.id, payment.name or _('Draft payment')))
            return payment
        self._advance_notify(
            self._advance_finance_users(),
            _("Advance payment request: %s", self.name),
            Markup('<p>%s requested an advance of %s to %s for %s (order total %s).</p>'
                   '<p>Please confirm or reject the payment <a href="/odoo/account.payment/%s">%s</a>.</p>') % (
                self.env.user.name, payment._advance_label(), self.partner_id.display_name, self.name,
                formatLang(self.env, self.amount_total, currency_obj=self.currency_id),
                payment.id, payment.name or _('Draft payment')))
        return payment

    def action_view_advances(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Advances'),
            'res_model': 'account.payment',
            'view_mode': 'list,form',
            'domain': [('purchase_advance_id', '=', self.id)],
            'context': {'create': False},
        }

    def _reconcile_advances(self):
        """Match paid advances against this order's posted bills."""
        for order in self:
            advances = order.advance_payment_ids.filtered(
                lambda p: p.advance_state == 'approved' and p.move_id)
            bills = order.invoice_ids.filtered(
                lambda m: m.move_type == 'in_invoice' and m.state == 'posted')
            if not advances or not bills:
                continue
            for bill in bills:
                bill_lines = bill.line_ids.filtered(
                    lambda l: l.account_id.account_type == 'liability_payable' and not l.reconciled)
                adv_lines = advances.move_id.line_ids.filtered(
                    lambda l: l.account_id in bill_lines.account_id and not l.reconciled
                    and l.partner_id.commercial_partner_id == bill.commercial_partner_id)
                if bill_lines and adv_lines:
                    (bill_lines | adv_lines).sudo().reconcile()
                    bill.message_post(body=_("Advance payment(s) %s adjusted against this bill.",
                                             ', '.join(adv_lines.move_id.mapped('name'))))


class AccountMove(models.Model):
    _inherit = 'account.move'

    def action_post(self):
        res = super().action_post()
        self.filtered(lambda m: m.move_type == 'in_invoice').invoice_line_ids \
            .purchase_line_id.order_id._reconcile_advances()
        return res
