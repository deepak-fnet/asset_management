from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError
from .vendor_compat_utils import group_member_emails

class AccountMove(models.Model):
    _inherit = 'account.move'

    # Reverse of vendor.bill.move_id - effectively single-record in practice
    # (one workflow bill per move), but kept as a proper One2many rather
    # than assuming that in code.
    vendor_bill_ids = fields.One2many('vendor.bill', 'move_id', string="Vendor Bill Workflow")
    vendor_bill_stores_approved = fields.Boolean(
        string="Vendor Bill Stores Approved", compute='_compute_vendor_bill_stores_approved', store=True,
        help="True once every linked vendor.bill workflow record has been "
             "approved by Purchase Stores (or further). A move with no "
             "linked vendor.bill at all (an ordinary bill created outside "
             "this workflow) is unaffected - only moves created via Upload "
             "Bill are gated.")

    @api.depends('vendor_bill_ids.state')
    def _compute_vendor_bill_stores_approved(self):
        for move in self:
            move.vendor_bill_stores_approved = bool(move.vendor_bill_ids) and all(
                b.state in ('stores_approved', 'finance_confirmed') for b in move.vendor_bill_ids
            )

    vendor_bill_awaiting_submit = fields.Boolean(
        string="Vendor Bill Awaiting Submit", compute='_compute_vendor_bill_state_flags',
        help="True while any linked vendor.bill has been uploaded but not "
             "yet submitted by the Purchase User - drives the Submit button "
             "on this form.")
    vendor_bill_awaiting_stores = fields.Boolean(
        string="Vendor Bill Awaiting Stores Approval", compute='_compute_vendor_bill_state_flags',
        help="True while any linked vendor.bill has been submitted but not "
             "yet approved by Purchase Stores - drives the Stores Approve "
             "button on this form.")

    @api.depends('vendor_bill_ids.state')
    def _compute_vendor_bill_state_flags(self):
        for move in self:
            move.vendor_bill_awaiting_submit = any(b.state == 'uploaded' for b in move.vendor_bill_ids)
            move.vendor_bill_awaiting_stores = any(b.state == 'submitted' for b in move.vendor_bill_ids)

    def action_vendor_bill_submit(self):
        """Thin forward to vendor.bill.action_submit_bill() - lets the
        Purchase User submit directly from the standard bill form too, not
        only from the PO's own Vendor Bills tab."""
        self.vendor_bill_ids.action_submit_bill()

    def action_vendor_bill_stores_approve(self):
        """Thin forward to vendor.bill.action_stores_approve() - lets
        Purchase Stores approve directly from the standard bill form too,
        not only from the PO's own Vendor Bills tab. Permission/state
        checks live on vendor.bill itself; this just relays to it."""
        self.vendor_bill_ids.action_stores_approve()

    # groups= on a button is static and would restrict posting for every
    # bill (including ordinary ones with no vendor.bill link at all) to
    # just these groups - can't be used here. This computed field lets the
    # view hide the Confirm button from non-Finance users on a workflow
    # bill specifically, without touching the button's own groups=. The
    # real enforcement is still the has_group() check in action_post below;
    # this is purely a "don't show a button that would just error" nicety.
    can_finance_post = fields.Boolean(compute='_compute_can_finance_post')

    def _compute_can_finance_post(self):
        finance_groups = (
            'vendor_management.group_purchase_role_finance',
            'vendor_management.group_vendor_manager',
            'vendor_management.group_vendor_md',
        )
        can_post = any(self.env.user.has_group(g) for g in finance_groups)
        for move in self:
            move.can_finance_post = can_post

    # ------------------------------------------------------------------
    # Hard gate, not just a hidden/restricted button: a move created via
    # Upload Bill must not be postable until Purchase Stores has approved
    # it, and only Purchase Finance may be the one to post it - clicking
    # this native "Confirm" button IS "Purchase Finance confirms the bill"
    # in this workflow; there is no separate custom confirm step. Once
    # posting actually succeeds, sync the linked vendor.bill(s) to
    # 'finance_confirmed' - that state means "posted", not "someone clicked
    # a different button".
    # ------------------------------------------------------------------
    def action_post(self):
        finance_groups = (
            'vendor_management.group_purchase_role_finance',
            'vendor_management.group_vendor_manager',
            'vendor_management.group_vendor_md',
        )
        for move in self:
            if move.vendor_bill_ids:
                if not move.vendor_bill_stores_approved:
                    raise UserError(_(
                        "This vendor bill is part of a Purchase Order's approval "
                        "workflow and cannot be posted until Purchase Stores has "
                        "approved it."
                    ))
                if not any(self.env.user.has_group(g) for g in finance_groups):
                    raise AccessError(_("Only Purchase Finance can confirm (post) this vendor bill."))
        res = super().action_post()
        self.vendor_bill_ids.filtered(lambda b: b.state == 'stores_approved').write({'state': 'finance_confirmed'})
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
