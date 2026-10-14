import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError

KINDS = [
    ("approve", "Approved"),
    ("comment", "Comment"),
    ("query", "Query"),
    ("reply", "Reply to Query"),
    ("revision", "Revision Requested"),
    ("resubmit", "Resubmitted"),
    ("reject", "Rejected"),
]


class PurchaseApprovalEvent(models.Model):
    """One step of an order's approval conversation: an approver's decision
    or note, the creator's answer. Together they are the approval history
    (who said what, at which level, when).

    Queries and revision requests stay 'open' until the creator replies /
    resubmits; a revision also remembers what the order looked like and what
    was changed in answer (same idea as a rejection)."""
    _name = "purchase.approval.event"
    _description = "Purchase Approval Event"
    _order = "id desc"

    order_id = fields.Many2one("purchase.order", required=True, ondelete="cascade", index=True)
    kind = fields.Selection(KINDS, required=True, index=True)
    user_id = fields.Many2one("res.users", string="By", default=lambda self: self.env.user, readonly=True)
    date = fields.Datetime(default=fields.Datetime.now, readonly=True)
    level = fields.Integer(string="Level No.", help="Approval level (sequence) the approver sits at.")
    level_name = fields.Char(string="Level", readonly=True, help="Approval role of that level, e.g. Level 1 - Purchase Head.")
    text = fields.Text(string="Message")
    state = fields.Selection([("open", "Open"), ("closed", "Closed")], default="closed", index=True)
    # query: the creator's answer
    reply = fields.Text(readonly=True)
    replied_by = fields.Many2one("res.users", readonly=True)
    replied_on = fields.Datetime(readonly=True)
    # revision: what the order looked like, and what changed since
    snapshot = fields.Text(readonly=True)
    change_summary = fields.Text(readonly=True)
    change_count = fields.Integer(readonly=True)
    number = fields.Integer(string="#", readonly=True, help="Running number within its kind on this order.")

    # A copy of the whole order at this moment (revision requested / resubmitted),
    # so anyone can open the event and see the order exactly as it was.
    has_copy = fields.Boolean(readonly=True)
    copy_multi_vendor = fields.Boolean(readonly=True, help="RFQ with several vendors: each line carries its own vendor.")
    copy_partner_id = fields.Many2one("res.partner", string="Vendor", readonly=True)
    copy_currency_id = fields.Many2one("res.currency", readonly=True)
    copy_date_order = fields.Datetime(string="Order Deadline", readonly=True)
    copy_date_planned = fields.Datetime(string="Expected Arrival", readonly=True)
    copy_payment_term_id = fields.Many2one("account.payment.term", string="Payment Terms", readonly=True)
    copy_notes = fields.Html(string="Terms & Conditions", readonly=True)
    copy_amount_untaxed = fields.Monetary(string="Untaxed Amount", currency_field="copy_currency_id", readonly=True)
    copy_amount_tax = fields.Monetary(string="Taxes", currency_field="copy_currency_id", readonly=True)
    copy_amount_total = fields.Monetary(string="Total", currency_field="copy_currency_id", readonly=True)
    copy_line_ids = fields.One2many("purchase.approval.event.line", "event_id", string="Products", readonly=True)
    copy_term_ids = fields.One2many("purchase.approval.event.term", "event_id", string="Terms", readonly=True)

    def _store_order_copy(self):
        for rec in self:
            order = rec.order_id
            vals = {
                "has_copy": True,
                "copy_multi_vendor": bool("vendor_ids" in order._fields and len(order.vendor_ids) > 1
                                          and not order.rfq_id and not order.direct_order),
                "copy_partner_id": order.partner_id.id,
                "copy_currency_id": order.currency_id.id,
                "copy_date_order": order.date_order,
                "copy_date_planned": order.date_planned,
                "copy_payment_term_id": order.payment_term_id.id,
                "copy_notes": order.note,
                "copy_amount_untaxed": order.amount_untaxed,
                "copy_amount_tax": order.amount_tax,
                "copy_amount_total": order.amount_total,
                "copy_line_ids": [(5, 0, 0)] + [(0, 0, {
                    "sequence": line.sequence,
                    "display_type": line.display_type or False,
                    "product_id": line.product_id.id,
                    "name": line.name,
                    "vendor_id": line.vendor_id.id if "vendor_id" in line._fields else False,
                    "product_qty": line.product_qty,
                    "uom_name": line.product_uom_id.name or "",
                    "price_unit": line.price_unit,
                    "discount": line.discount,
                    "taxes": ", ".join(line.tax_ids.mapped("name")),
                    "price_subtotal": line.price_subtotal,
                }) for line in order.order_line],
                "copy_term_ids": [(5, 0, 0)],
            }
            if "terms_condition_line_ids" in order._fields:
                vals["copy_term_ids"] += [(0, 0, {
                    "name": t.term_id.display_name or "",
                    "value": t.value_id.display_name or "",
                }) for t in order.terms_condition_line_ids]
            rec.sudo().write(vals)

    @api.depends("kind", "number", "order_id.name")
    def _compute_display_name(self):
        labels = dict(KINDS)
        for rec in self:
            rec.display_name = "%s #%s - %s" % (labels.get(rec.kind, rec.kind), rec.number or 1,
                                               rec.order_id.name or "")

    def _close_revision(self):
        """The creator resubmitted after this revision request."""
        self.sudo().write({"state": "closed", "replied_by": self.env.user.id,
                           "replied_on": fields.Datetime.now()})


class PurchaseApprovalEventLine(models.Model):
    _name = "purchase.approval.event.line"
    _description = "Approval Event - Order Line Copy"
    _order = "sequence, id"

    event_id = fields.Many2one("purchase.approval.event", required=True, ondelete="cascade", index=True)
    currency_id = fields.Many2one(related="event_id.copy_currency_id")
    sequence = fields.Integer()
    display_type = fields.Char()
    product_id = fields.Many2one("product.product", string="Product")
    name = fields.Text(string="Description")
    vendor_id = fields.Many2one("res.partner", string="Vendor")
    product_qty = fields.Float(string="Quantity")
    uom_name = fields.Char(string="Unit")
    price_unit = fields.Float(string="Unit Price")
    discount = fields.Float(string="Disc.%")
    taxes = fields.Char(string="Taxes")
    price_subtotal = fields.Monetary(string="Subtotal")


class PurchaseApprovalEventTerm(models.Model):
    _name = "purchase.approval.event.term"
    _description = "Approval Event - Terms Copy"

    event_id = fields.Many2one("purchase.approval.event", required=True, ondelete="cascade", index=True)
    name = fields.Char(string="Term")
    value = fields.Char(string="Value")


class PurchaseApprovalActionWizard(models.TransientModel):
    """Comment / Query / Request Revision from the approver."""
    _name = "purchase.approval.action.wizard"
    _description = "Approval Comment / Query / Revision"

    order_id = fields.Many2one("purchase.order", required=True)
    kind = fields.Selection(
        [("query", "Query"), ("revision", "Request Revision")],
        required=True, readonly=True)
    text = fields.Text(string="Message", required=True)
    hint = fields.Char(compute="_compute_hint")

    @api.depends("kind")
    def _compute_hint(self):
        hints = {
            "query": _("Ask the user who submitted the order a question. Approval waits on you until they reply."),
            "revision": _("Say what must change. The order goes back to the purchase user, "
                          "who edits it and submits it for approval again from the first level."),
        }
        for wiz in self:
            wiz.hint = hints.get(wiz.kind, "")

    def action_confirm(self):
        self.ensure_one()
        text = (self.text or "").strip()
        if not text:
            raise UserError(_("Please write your message."))
        order = self.order_id
        if self.kind == "query":
            order._approval_query(text)
        else:
            order._approval_request_revision(text)
        return {"type": "ir.actions.act_window_close"}


class PurchaseApprovalReplyWizard(models.TransientModel):
    _name = "purchase.approval.reply.wizard"
    _description = "Reply to an Approval Query"

    order_id = fields.Many2one("purchase.order", required=True)
    query_id = fields.Many2one("purchase.approval.event", string="Query", readonly=True)
    query_text = fields.Text(related="query_id.text", string="Question")
    query_by = fields.Many2one(related="query_id.user_id", string="Asked By")
    text = fields.Text(string="Your Reply", required=True)

    def action_confirm(self):
        self.ensure_one()
        text = (self.text or "").strip()
        if not text:
            raise UserError(_("Please write your reply."))
        self.order_id._approval_reply(self.query_id, text)
        return {"type": "ir.actions.act_window_close"}


class PurchaseApprovalRejectWizard(models.TransientModel):
    _name = "purchase.approval.reject.wizard"
    _description = "Reject Purchase Order"

    order_id = fields.Many2one("purchase.order", required=True)
    reason = fields.Text(string="Reason for Rejection", required=True)

    def action_confirm_reject(self):
        self.ensure_one()
        if not (self.reason or "").strip():
            raise UserError(_("Please enter a reason for the rejection."))
        self.order_id._reject_with_reason(self.reason.strip())
        return {"type": "ir.actions.act_window_close"}
