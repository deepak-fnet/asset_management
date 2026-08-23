from odoo import models, fields, api, _
from .vendor_compat_utils import group_member_emails

class AccountMove(models.Model):
    _inherit = 'account.move'

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
