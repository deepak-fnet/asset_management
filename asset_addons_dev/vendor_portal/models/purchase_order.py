# -*- coding: utf-8 -*-
from odoo import models, fields, api, _


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    # Extra bid detail the vendor supplies from the portal. Kept here rather
    # than in purchase_extended so that module stays independent of whether
    # the portal is installed.
    vendor_delivery_days = fields.Integer(
        string="Quoted Delivery (days)", copy=False,
        help="Lead time quoted by the vendor when submitting their bid.")
    vendor_remarks = fields.Text(
        string="Vendor Remarks", copy=False,
        help="Free-text notes submitted by the vendor with their bid.")
    bid_submitted_on = fields.Datetime(
        string="Bid Submitted On", readonly=True, copy=False)
    bid_submitted_by = fields.Many2one(
        "res.users", string="Bid Submitted By", readonly=True, copy=False)

    def action_bid_received(self):
        """Stamp who submitted the bid and when.

        Extends rather than replaces purchase_extended's implementation, so
        its price/quantity validation still runs first - if it raises, none
        of this is recorded, which is correct.
        """
        result = super().action_bid_received()
        for order in self:
            if not order.bid_submitted_on:
                order.write({
                    "bid_submitted_on": fields.Datetime.now(),
                    "bid_submitted_by": self.env.user.id,
                })
        return result

    def action_grant_portal_access(self):
        """Open the standard portal-access wizard for this order's vendor.

        Saves hunting for the contact in Contacts just to tick the portal
        box - the vendor needs a login before any of this is usable.
        """
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "portal.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "active_model": "res.partner",
                "active_ids": [self.partner_id.id],
            },
        }
