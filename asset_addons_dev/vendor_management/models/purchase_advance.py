from markupsafe import Markup

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError
from odoo.tools.float_utils import float_compare
from odoo.tools.misc import formatLang

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
FINANCE_GROUPS = (
    'vendor_management.group_purchase_role_finance',
    'vendor_management.group_vendor_manager',
    'vendor_management.group_vendor_md',
)


def _user_in(env, groups):
    return any(env.user.has_group(g) for g in groups)


class AccountPaymentTerm(models.Model):
    _inherit = 'account.payment.term'

    advance_percent = fields.Float(
        string="Advance %", digits=(5, 2),
        help="Share of a purchase order that can be paid to the vendor in "
             "advance, after the order is confirmed. 0 = no advance.")

    @api.constrains('advance_percent')
    def _check_advance_percent(self):
        for term in self:
            if not 0 <= term.advance_percent <= 100:
                raise UserError(_("Advance % must be between 0 and 100."))


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    purchase_advance_id = fields.Many2one(
        'purchase.order', string="Advance for PO", copy=False, readonly=True, index='btree_not_null')
    advance_state = fields.Selection([
        ('requested', 'Requested'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], string="Advance Status", copy=False, readonly=True, tracking=True)
    advance_requested_by = fields.Many2one('res.users', string="Requested By", copy=False, readonly=True)
    can_advance_finance = fields.Boolean(compute='_compute_can_advance_finance')

    @api.depends_context('uid')
    def _compute_can_advance_finance(self):
        is_finance = _user_in(self.env, FINANCE_GROUPS)
        for pay in self:
            pay.can_advance_finance = is_finance

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

    def action_advance_reject(self):
        self.ensure_one()
        if not _user_in(self.env, FINANCE_GROUPS):
            raise AccessError(_("Only Purchase Finance can reject an advance payment."))
        if self.advance_state != 'requested' or self.state != 'draft':
            raise UserError(_("Only a requested advance can be rejected."))
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
        if not _user_in(self.env, FINANCE_GROUPS):
            raise AccessError(_("Only Purchase Finance can reject an advance payment."))
        if self.advance_state != 'requested' or self.state != 'draft':
            raise UserError(_("Only a requested advance can be rejected."))
        self.action_cancel()
        self.advance_state = 'rejected'
        order = self.purchase_advance_id
        msg = Markup('<p>Advance %s for %s was <b>rejected</b> by %s.</p><p><b>Reason:</b> %s</p>') % (
            self._advance_label(), order.name, self.env.user.name, reason)
        self.message_post(body=msg)
        order._advance_notify(self.advance_requested_by, _("Advance rejected: %s", order.name), msg)


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    advance_percent = fields.Float(related='payment_term_id.advance_percent', string="Advance %")
    advance_payment_ids = fields.One2many('account.payment', 'purchase_advance_id', string="Advances")
    advance_count = fields.Integer(compute='_compute_advance_amounts')
    advance_allowed = fields.Monetary(compute='_compute_advance_amounts', string="Advance Allowed")
    advance_requested = fields.Monetary(compute='_compute_advance_amounts', string="Advance Requested")
    advance_paid = fields.Monetary(compute='_compute_advance_amounts', string="Advance Paid")
    can_request_advance = fields.Boolean(compute='_compute_advance_amounts')

    @api.depends('amount_total', 'advance_percent', 'state',
                 'advance_payment_ids.amount', 'advance_payment_ids.state', 'advance_payment_ids.advance_state')
    @api.depends_context('uid')
    def _compute_advance_amounts(self):
        is_buyer = _user_in(self.env, PURCHASE_USER_GROUPS)
        for order in self:
            live = order.advance_payment_ids.filtered(lambda p: p.advance_state in ('requested', 'approved'))
            order.advance_count = len(order.advance_payment_ids)
            order.advance_allowed = order.amount_total * order.advance_percent / 100.0
            order.advance_requested = sum(live.mapped('amount'))
            order.advance_paid = sum(live.filtered(lambda p: p.advance_state == 'approved').mapped('amount'))
            order.can_request_advance = (
                is_buyer and order.state == 'purchase' and order.advance_percent > 0
                and order.currency_id.compare_amounts(order.advance_allowed, order.advance_requested) > 0)

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

    def _request_advance(self, amount, journal, date, memo):
        self.ensure_one()
        if not _user_in(self.env, PURCHASE_USER_GROUPS):
            raise AccessError(_("Only a Purchase User can request an advance."))
        if self.state != 'purchase' or self.advance_percent <= 0:
            raise UserError(_("This order's payment terms allow no advance."))
        if float_compare(amount, 0, precision_rounding=self.currency_id.rounding) <= 0:
            raise UserError(_("Enter an advance amount."))
        remaining = self.advance_allowed - self.advance_requested
        if self.currency_id.compare_amounts(amount, remaining) > 0:
            raise UserError(_(
                "Advance is limited to %(pct)s%% of the order: at most %(left)s can still be requested.",
                pct=self.advance_percent,
                left=formatLang(self.env, remaining, currency_obj=self.currency_id)))
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
            'advance_state': 'requested',
            'advance_requested_by': self.env.user.id,
        })
        self._advance_notify(
            self._advance_finance_users(),
            _("Advance payment request: %s", self.name),
            Markup('<p>%s requested an advance of %s to %s for %s (%s%% of %s).</p>'
                   '<p>Please confirm or reject the payment <a href="/odoo/account.payment/%s">%s</a>.</p>') % (
                self.env.user.name, payment._advance_label(), self.partner_id.display_name, self.name,
                self.advance_percent,
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
