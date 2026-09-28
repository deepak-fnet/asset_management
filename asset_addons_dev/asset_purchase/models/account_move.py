# -*- coding: utf-8 -*-

from odoo import models


class AccountMove(models.Model):
    _inherit = "account.move"

    def action_post(self):
        res = super().action_post()
        for move in self.filtered(lambda m: m.move_type in ("in_invoice", "in_receipt")):
            move._link_assets_from_bill()
        return res

    def _link_assets_from_bill(self):
        """Link this now-posted vendor bill back onto the fixed asset(s) it
        invoices, matched via the PO line each bill line was generated from.

        Only runs after posting succeeds (called from action_post AFTER the
        super() call) - a bill that fails to post, whether from this
        module's own checks or another module's (e.g. vendor_management's
        approval gate), never reaches here, so an asset is never linked to
        an invoice that turned out not to actually go through.

        Caps how many not-yet-linked assets each bill line claims to that
        line's own invoiced quantity, so a second partial bill for the same
        product on the same PO does not sweep up assets a later bill was
        meant to cover.

        Runs as sudo: whoever posts the bill (e.g. Purchase Finance) has no
        reason to also hold an Asset Management group, but this linkage is
        an automated side effect of posting, not a user-driven asset
        read/write, so it should not depend on the poster's asset rights.
        """
        self.ensure_one()
        Asset = self.env["asset.asset"].sudo()
        for line in self.invoice_line_ids:
            po_line = line.purchase_line_id
            if not po_line or not line.product_id:
                continue
            assets = Asset.search([
                ("purchase_order_id", "=", po_line.order_id.id),
                ("product_id", "=", line.product_id.id),
                ("invoice_move_id", "=", False),
            ], limit=int(line.quantity) or None)
            if assets:
                assets.write({
                    "invoice_move_id": self.id,
                    "invoice_ref": self.name,
                })
