# -*- coding: utf-8 -*-
"""Portal controllers letting a vendor bid on their own RFQs.

Security model
--------------
Every route resolves the RFQ through _get_vendor_order(), which checks the
order belongs to the logged-in user's commercial partner BEFORE returning it.
Portal users are not trusted to only request their own ids - guessing another
order id is the obvious attack, so ownership is verified on every single
request rather than relying on record rules alone.

Writes are done with sudo() only AFTER that ownership check has passed, since
a portal user has no write access to purchase.order by design.
"""

import logging

from odoo import http, _
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal, pager

_logger = logging.getLogger(__name__)


class VendorPortal(CustomerPortal):

    # ── Counters on the portal home ───────────────────────────────────────
    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "rfq_count" in counters:
            partner = request.env.user.partner_id.commercial_partner_id
            values["rfq_count"] = request.env["purchase.order"].sudo().search_count(
                self._vendor_rfq_domain(partner))
        return values

    def _vendor_rfq_domain(self, partner):
        """RFQs this vendor is allowed to see and bid on.

        Only draft/sent orders: once an RFQ is confirmed, cancelled or the
        bid is already in, there is nothing left for the vendor to do and
        letting them keep editing prices would rewrite an agreed order.
        """
        return [
            ("partner_id", "child_of", partner.id),
            ("state", "in", ("draft", "sent")),
        ]

    def _get_vendor_order(self, order_id, allow_submitted=False):
        """Return the RFQ if it truly belongs to this portal user.

        Raises AccessError otherwise. Returning a sudo() recordset is safe
        precisely because the ownership test has already run.
        """
        partner = request.env.user.partner_id.commercial_partner_id
        order = request.env["purchase.order"].sudo().browse(int(order_id))
        if not order.exists():
            raise AccessError(_("This request for quotation no longer exists."))

        allowed_partners = request.env["res.partner"].sudo().search(
            [("id", "child_of", partner.id)])
        if order.partner_id not in allowed_partners:
            _logger.warning(
                "[VendorPortal] user %s tried to access PO %s belonging to %s",
                request.env.user.login, order.id, order.partner_id.name)
            raise AccessError(_("You do not have access to this document."))

        if not allow_submitted and order.state not in ("draft", "sent"):
            raise AccessError(_(
                "This request for quotation is no longer open for bidding."))
        return order

    # ── List ──────────────────────────────────────────────────────────────
    @http.route(["/my/rfqs", "/my/rfqs/page/<int:page>"],
                type="http", auth="user", website=True)
    def portal_my_rfqs(self, page=1, **kw):
        partner = request.env.user.partner_id.commercial_partner_id
        Order = request.env["purchase.order"].sudo()
        domain = self._vendor_rfq_domain(partner)

        total = Order.search_count(domain)
        pager_values = pager(
            url="/my/rfqs", total=total, page=page, step=self._items_per_page)
        orders = Order.search(
            domain, limit=self._items_per_page, offset=pager_values["offset"],
            order="create_date desc")

        return request.render("vendor_portal.portal_my_rfqs", {
            "orders": orders,
            "pager": pager_values,
            "page_name": "rfq",
            "default_url": "/my/rfqs",
        })

    # ── Detail / bid form ─────────────────────────────────────────────────
    @http.route(["/my/rfq/<int:order_id>"], type="http", auth="user",
                website=True)
    def portal_rfq_detail(self, order_id, **kw):
        try:
            order = self._get_vendor_order(order_id, allow_submitted=True)
        except AccessError:
            return request.redirect("/my")

        return request.render("vendor_portal.portal_rfq_detail", {
            "order": order,
            "page_name": "rfq",
            # Bidding closes once submitted, so the template can render a
            # read-only summary instead of an editable form.
            "editable": order.state in ("draft", "sent")
                        and not order.is_bid_received,
            "error": kw.get("error"),
        })

    # ── Submit bid ────────────────────────────────────────────────────────
    @http.route(["/my/rfq/<int:order_id>/submit"], type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def portal_rfq_submit(self, order_id, **post):
        """Save the vendor's prices, then run the SAME action_bid_received()
        the internal button uses.

        Reusing that method rather than setting is_bid_received directly
        matters: it carries the validation that every selected line has a
        price and quantity, and anything purchase_extended adds to it later
        applies to portal bids automatically instead of silently diverging.
        """
        try:
            order = self._get_vendor_order(order_id)
        except AccessError:
            return request.redirect("/my")

        if order.is_bid_received:
            return request.redirect("/my/rfq/%s" % order.id)

        # Collect prices keyed by line id: price_<line_id>
        updates = {}
        for key, value in post.items():
            if not key.startswith("price_"):
                continue
            try:
                line_id = int(key.split("_", 1)[1])
                price = float(value or 0.0)
            except (ValueError, IndexError):
                continue
            if price < 0:
                return request.redirect(
                    "/my/rfq/%s?error=%s" % (
                        order.id, _("Prices cannot be negative.")))
            updates[line_id] = price

        # Only touch lines that actually belong to this order - a crafted
        # POST could otherwise carry a line id from someone else's RFQ.
        own_lines = {line.id: line for line in order.order_line}
        for line_id, price in updates.items():
            line = own_lines.get(line_id)
            if line:
                line.sudo().write({"price_unit": price})

        delivery_days = post.get("delivery_days")
        vendor_remarks = post.get("vendor_remarks")
        vals = {}
        if delivery_days:
            try:
                vals["vendor_delivery_days"] = int(delivery_days)
            except ValueError:
                pass
        if vendor_remarks:
            vals["vendor_remarks"] = vendor_remarks
        if vals:
            order.sudo().write(vals)

        try:
            order.sudo().action_bid_received()
        except ValidationError as e:
            # action_bid_received raises when a price or quantity is missing.
            # Surface that to the vendor rather than a 500 page.
            message = e.args[0] if e.args else _("Please complete all lines.")
            return request.redirect(
                "/my/rfq/%s?error=%s" % (order.id, message))

        order.sudo().message_post(
            body=_("Bid submitted through the vendor portal by %s.")
                 % request.env.user.name)
        return request.redirect("/my/rfq/%s" % order.id)
