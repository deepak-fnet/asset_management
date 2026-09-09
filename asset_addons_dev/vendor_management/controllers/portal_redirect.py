from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.addons.website.controllers.main import Website
from odoo import http
from odoo.http import request


def _vendor_of(user):
    """The vendor partner for this user, if they are one - else None."""
    if not user.has_group('base.group_portal'):
        return None
    return request.env['res.partner'].sudo().search([
        ('user_ids', '=', user.id),
        ('is_vendor', '=', True)
    ], limit=1)


class VendorPortalRedirect(CustomerPortal):

    @http.route(['/my', '/my/home'], type='http', auth='user', website=True)
    def home(self, **kw):
        vendor = _vendor_of(request.env.user)
        if vendor:
            return request.redirect('/my/vendor')

        return super().home(**kw)


class VendorWebsiteHomeRedirect(Website):

    def index(self, **kw):
        """The top nav's "Home" link points at the site root ("/"), not
        "/my" - a vendor portal user clicking it landed on the generic,
        empty website homepage instead of their own dashboard. Only
        redirected for a logged-in vendor; overriding index() (rather than
        redeclaring the '/' route) keeps every other visitor on the normal
        website homepage exactly as before.
        """
        vendor = _vendor_of(request.env.user)
        if vendor:
            return request.redirect('/my/vendor')

        return super().index(**kw)


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
