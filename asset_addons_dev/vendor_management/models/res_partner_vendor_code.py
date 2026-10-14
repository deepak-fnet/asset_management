# -*- coding: utf-8 -*-
"""Vendor code and portal-login conflicts.

* Vendor code (VC0001...) is given automatically when the vendor is approved &
  activated. It shows in the name ("Deepak - VC0001") and can be searched on,
  so buyers can pick a vendor by code. Vendor Manager / MD can also type or
  change it on the vendor form (it must stay unique).
* A vendor whose email is already the login of another Odoo user cannot get
  portal access (Odoo logins are unique). That used to be skipped silently;
  now the vendor record says so, and who holds that login.
"""
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

VENDOR_CODE_EDITORS = (
    'vendor_management.group_vendor_manager',
    'vendor_management.group_vendor_md',
)


class ResPartnerVendorCode(models.Model):
    _inherit = 'res.partner'

    _rec_names_search = ['complete_name', 'email', 'ref', 'vat', 'company_registry', 'vendor_code']

    vendor_code = fields.Char(string='Vendor Code', copy=False, index=True, tracking=True,
                              help="Given automatically when the vendor is approved and activated. "
                                   "Vendor Manager / MD can change it; it must be unique.")
    can_edit_vendor_code = fields.Boolean(compute='_compute_can_edit_vendor_code')
    portal_conflict_user_id = fields.Many2one(
        'res.users', string='Portal Login Conflict', compute='_compute_portal_conflict',
        help="Another user already logs in with this vendor's email, so the vendor cannot get "
             "portal access with it.")

    _vendor_code_unique = models.Constraint('UNIQUE(vendor_code)', 'Vendor code must be unique.')

    @api.depends_context('uid')
    def _compute_can_edit_vendor_code(self):
        can = self.env.su or any(self.env.user.has_group(g) for g in VENDOR_CODE_EDITORS)
        for partner in self:
            partner.can_edit_vendor_code = can

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'vendor_code' in vals:
                vals['vendor_code'] = (vals['vendor_code'] or '').strip() or False
        return super().create(vals_list)

    def write(self, vals):
        if 'vendor_code' in vals:
            # The automatic code on approval runs with sudo(); typing one is for managers.
            if not (self.env.su or any(self.env.user.has_group(g) for g in VENDOR_CODE_EDITORS)):
                raise AccessError(_("Only a Vendor Manager can change the vendor code."))
            # '' would clash with other blank codes under UNIQUE; store no code instead.
            vals['vendor_code'] = (vals['vendor_code'] or '').strip() or False
        return super().write(vals)

    @api.depends('vendor_code')
    def _compute_display_name(self):
        super()._compute_display_name()
        for partner in self:
            if partner.vendor_code and not partner.env.context.get('formatted_display_name'):
                partner.display_name = '%s - %s' % (partner.display_name, partner.vendor_code)

    @api.depends('email', 'user_ids', 'is_vendor')
    def _compute_portal_conflict(self):
        Users = self.env['res.users'].sudo().with_context(active_test=False)
        for partner in self:
            conflict = Users
            email = (partner.email or '').strip()
            if partner.is_vendor and email and not partner.commercial_partner_id.user_ids:
                conflict = Users.search([('login', '=ilike', email),
                                         ('partner_id', '!=', partner.commercial_partner_id.id)], limit=1)
            partner.portal_conflict_user_id = conflict

    def _assign_vendor_code(self):
        for partner in self.filtered(lambda p: p.is_vendor and not p.vendor_code):
            partner.sudo().vendor_code = self.env['ir.sequence'].sudo().next_by_code('vendor.code')

    def action_approve_activate(self):
        res = super().action_approve_activate()
        self._assign_vendor_code()
        return res

    def _action_trigger_portal_access(self):
        res = super()._action_trigger_portal_access()
        for commercial in self.mapped('commercial_partner_id'):
            user = commercial.portal_conflict_user_id
            if commercial.is_vendor and user and not commercial.user_ids:
                commercial.message_post(body=Markup(
                    '<p><b>Portal access not given:</b> the email <b>%s</b> is already the login of '
                    'user <b>%s</b> (%s). Use a different email for this vendor, or change that '
                    "user's login, then save again.</p>") % (commercial.email, user.name, user.partner_id.display_name))
        return res
