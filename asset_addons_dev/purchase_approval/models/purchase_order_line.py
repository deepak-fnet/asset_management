from odoo import models, _
from odoo.exceptions import UserError


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    # The commercially-meaningful fields: what's bought, how much, at what
    # price and vendor. Deliberately excludes system-written fields like
    # qty_received/qty_invoiced/invoice_lines, which stock/account modules
    # keep updating on this same model throughout the order's life
    # regardless of approval state - blocking those would break receiving
    # and billing, not just the intended target (editing after sign-off).
    _APPROVAL_LOCKED_FIELDS = {
        "product_id", "product_qty", "price_unit", "tax_ids", "discount",
        "vendor_id",
    }

    def write(self, vals):
        if set(vals) & self._APPROVAL_LOCKED_FIELDS and not self.env.context.get(
                "skip_approval_lock_check"):
            locked = self.filtered(
                lambda l: l.order_id.approval_status in ("submitted", "approved"))
            if locked:
                raise UserError(_(
                    "%(order)s is awaiting or has approval - product, quantity, "
                    "price, tax and vendor can't be changed until it's rejected "
                    "or approval is reset.",
                    order=locked.order_id[:1].name,
                ))
        return super().write(vals)
