from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError
from .vendor_compat_utils import group_member_emails

class AccountMove(models.Model):
    _inherit = 'account.move'

    # ------------------------------------------------------------------
    # Purchase bill approval: Upload Bill (purchase order) -> Stores
    # Approve -> Finance Confirm. Applies to vendor bills created from a
    # purchase order; any other move posts exactly as standard Odoo.
    # "Finance Confirm" is Odoo's own Confirm (action_post) button - there
    # is no separate custom confirm step.
    # ------------------------------------------------------------------
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

    purchase_bill_approval = fields.Boolean(
        string="Needs Purchase Approval", compute='_compute_purchase_bill_approval', store=True,
        help="Vendor bill created from a purchase order: Stores must approve "
             "it before Finance can confirm (post) it.")
    bill_approval_state = fields.Selection([
        ('to_approve', 'Waiting Stores Approval'),
        ('stores_approved', 'Stores Approved'),
    ], string="Bill Approval", default='to_approve', copy=False, tracking=True)

    @api.depends('move_type', 'invoice_line_ids.purchase_line_id')
    def _compute_purchase_bill_approval(self):
        for move in self:
            move.purchase_bill_approval = (
                move.move_type in ('in_invoice', 'in_refund')
                and bool(move.invoice_line_ids.purchase_line_id))

    # groups= on a button is static and would restrict posting for every
    # move, so these drive visibility per user instead. The real enforcement
    # is the has_group() checks in the actions below.
    can_stores_approve = fields.Boolean(compute='_compute_bill_approval_rights')
    can_finance_post = fields.Boolean(compute='_compute_bill_approval_rights')

    def _compute_bill_approval_rights(self):
        user = self.env.user
        can_approve = any(user.has_group(g) for g in self.STORES_GROUPS)
        can_post = any(user.has_group(g) for g in self.FINANCE_GROUPS)
        for move in self:
            move.can_stores_approve = can_approve
            move.can_finance_post = can_post

    def action_stores_approve(self):
        if not any(self.env.user.has_group(g) for g in self.STORES_GROUPS):
            raise AccessError(_("Only Purchase Stores can approve this bill."))
        for move in self:
            if not move.purchase_bill_approval or move.state != 'draft':
                raise UserError(_("Only a draft purchase bill can be approved by Stores."))
            if move.bill_approval_state == 'stores_approved':
                continue
            move.bill_approval_state = 'stores_approved'
            move.message_post(body=_("Approved by Stores (%s).", self.env.user.name))

    def action_post(self):
        for move in self.filtered('purchase_bill_approval'):
            if move.bill_approval_state != 'stores_approved':
                raise UserError(_(
                    "%s must be approved by Purchase Stores before it can be confirmed.",
                    move.name or _("This bill")))
            if not any(self.env.user.has_group(g) for g in self.FINANCE_GROUPS):
                raise AccessError(_("Only Purchase Finance can confirm (post) this bill."))
        return super().action_post()

    def button_draft(self):
        # Reset to draft = changed after approval: needs Stores again.
        res = super().button_draft()
        self.filtered('purchase_bill_approval').write({'bill_approval_state': 'to_approve'})
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
