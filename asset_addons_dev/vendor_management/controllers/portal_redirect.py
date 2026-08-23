from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo import http
from odoo.http import request


class VendorPortalRedirect(CustomerPortal):

    @http.route(['/my'], type='http', auth='user', website=True)
    def home(self, **kw):
        user = request.env.user

        if user.has_group('base.group_portal'):
            vendor = request.env['res.partner'].sudo().search([
                ('user_ids', '=', user.id),
                ('is_vendor', '=', True)
            ], limit=1)

            if vendor:
                return request.redirect('/my/vendor')

        return super().home(**kw)


class VendorPortal(http.Controller):

    @http.route(['/my/vendor'], type='http', auth='user', website=True)
    def portal_vendor_dashboard(self):
        user = request.env.user
        partner = user.partner_id

        if user.has_group('base.group_portal') and not partner.is_vendor:
            partner.sudo().write({'is_vendor': True})

        return request.render('vendor_management.portal_vendor_dashboard', {
            'vendor': partner
        })

    @http.route(['/my/vendor/documents'], type='http', auth='user', website=True)
    def portal_vendor_documents(self):
        partner = request.env.user.partner_id

        docs = request.env['vendor.document'].sudo().search([
            ('vendor_id', '=', partner.id)
        ])

        return request.render('vendor_management.portal_vendor_documents', {
            'docs': docs
        })
