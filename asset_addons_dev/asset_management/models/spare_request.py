# -*- coding: utf-8 -*-
r"""Spare part requests.

Flow
----
    draft -> submitted -> approved -> po_created -> received -> done
                       \-> rejected                \-> cancelled

A user requests spare products, a manager approves, a purchase order is raised
for the approved lines, and the request closes automatically once the PO's
goods are received.

Why the receipt check is polled rather than pushed
--------------------------------------------------
Odoo does not emit a business event when a PO is fully received; qty_received
is a computed field on the PO line. Overriding stock.picking.button_validate
would work but couples this module to inventory internals that shift between
versions. A cron plus a manual "Check Receipt" button is duller and survives
upgrades.
"""

import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SpareRequest(models.Model):
    _name = "spare.request"
    _description = "Spare Part Request"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(
        string="Reference", required=True, copy=False, readonly=True,
        default=lambda self: _("New"))

    requester_id = fields.Many2one(
        "res.users", string="Requested By", required=True, tracking=True,
        default=lambda self: self.env.user)
    employee_id = fields.Many2one("hr.employee", string="Employee", tracking=True)
    department_id = fields.Many2one("hr.department", string="Department")

    request_date = fields.Date(default=fields.Date.context_today, tracking=True)
    required_by = fields.Date(string="Required By", tracking=True)

    asset_id = fields.Many2one("asset.asset", string="For Asset", tracking=True)
    repair_id = fields.Many2one(
        "repair.management", string="Repair Order", tracking=True,
        help="Set when the request was raised from a repair order.")

    priority = fields.Selection(
        [("0", "Normal"), ("1", "Urgent")], default="0", tracking=True)
    reason = fields.Text()

    line_ids = fields.One2many(
        "spare.request.line", "request_id", string="Requested Products")

    state = fields.Selection(
        [("draft", "Draft"),
         ("submitted", "Submitted"),
         ("approved", "Approved"),
         ("rejected", "Rejected"),
         ("po_created", "PO Created"),
         ("received", "Received"),
         ("done", "Done"),
         ("cancelled", "Cancelled")],
        default="draft", required=True, tracking=True, index=True)

    approver_id = fields.Many2one("res.users", string="Approved By", readonly=True)
    approval_date = fields.Datetime(readonly=True)
    rejection_reason = fields.Char(readonly=True)

    purchase_order_id = fields.Many2one(
        "purchase.order", string="Purchase Order", readonly=True, copy=False)
    po_state = fields.Selection(
        related="purchase_order_id.state", string="PO Status", readonly=True)

    vendor_id = fields.Many2one(
        "res.partner", string="Preferred Vendor",
        domain="[('supplier_rank', '>', 0)]", tracking=True)

    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id)
    total_estimated = fields.Monetary(
        compute="_compute_totals", store=True, currency_field="currency_id")
    line_count = fields.Integer(compute="_compute_totals")
    fully_available = fields.Boolean(
        compute="_compute_availability", string="All In Stock")
    availability_note = fields.Char(compute="_compute_availability")

    # ══════════════════════════════════════════════════════════════════════
    @api.depends("line_ids.subtotal")
    def _compute_totals(self):
        for rec in self:
            rec.total_estimated = sum(rec.line_ids.mapped("subtotal"))
            rec.line_count = len(rec.line_ids)

    @api.depends("line_ids.available_qty", "line_ids.quantity")
    def _compute_availability(self):
        for rec in self:
            short = rec.line_ids.filtered(
                lambda l: l.available_qty < l.quantity)
            rec.fully_available = bool(rec.line_ids) and not short
            if not rec.line_ids:
                rec.availability_note = _("No products requested yet.")
            elif short:
                rec.availability_note = _("%s of %s line(s) short on stock.") % (
                    len(short), len(rec.line_ids))
            else:
                rec.availability_note = _("All requested products are in stock.")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "spare.request") or _("New")
        return super().create(vals_list)

    @api.onchange("employee_id")
    def _onchange_employee_id(self):
        if self.employee_id:
            self.department_id = self.employee_id.department_id

    @api.onchange("asset_id")
    def _onchange_asset_id(self):
        if self.asset_id and self.asset_id.assigned_employee_id:
            self.employee_id = self.asset_id.assigned_employee_id

    # ══════════════════════════════════════════════════════════════════════
    # Workflow
    # ══════════════════════════════════════════════════════════════════════
    def action_check_availability(self):
        """Refresh on-hand figures and report the shortfall."""
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_("Add at least one product first."))

        self.line_ids._compute_available_qty()
        short = self.line_ids.filtered(lambda l: l.available_qty < l.quantity)

        if not short:
            message = _("All %s product(s) are available in stock.") % len(self.line_ids)
            kind = "success"
        else:
            details = "\n".join(
                _("• %s: need %.2f, on hand %.2f") % (
                    line.product_id.display_name, line.quantity, line.available_qty)
                for line in short)
            message = _("Short on stock:\n%s") % details
            kind = "warning"

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Stock Availability"),
                "message": message,
                "type": kind,
                "sticky": bool(short),
            },
        }

    def action_submit(self):
        for rec in self:
            if not rec.line_ids:
                raise UserError(_("Add at least one product before submitting."))
            rec.state = "submitted"
            rec.message_post(body=_("Request submitted for approval."))
        return True

    def action_approve(self):
        for rec in self:
            if rec.state != "submitted":
                raise UserError(_("Only submitted requests can be approved."))
            rec.write({
                "state": "approved",
                "approver_id": self.env.user.id,
                "approval_date": fields.Datetime.now(),
            })
            rec.message_post(body=_("Request approved by %s.") % self.env.user.name)
        return True

    def action_reject(self):
        for rec in self:
            rec.write({"state": "rejected"})
            rec.message_post(body=_("Request rejected by %s.") % self.env.user.name)
        return True

    def action_reset_to_draft(self):
        return self.write({"state": "draft"})

    def action_cancel(self):
        for rec in self:
            if rec.purchase_order_id and rec.purchase_order_id.state not in (
                    "cancel", "draft"):
                raise UserError(_(
                    "Cancel or reset purchase order %s first."
                ) % rec.purchase_order_id.name)
            rec.state = "cancelled"
        return True

    def action_create_po(self):
        """Raise one purchase order for the approved lines."""
        self.ensure_one()
        if self.state != "approved":
            raise UserError(_("Approve the request before creating a purchase order."))
        if self.purchase_order_id:
            raise UserError(_("A purchase order already exists for this request."))
        if not self.vendor_id:
            raise UserError(_("Select a preferred vendor first."))

        lines = self.line_ids.filtered(lambda l: l.quantity > 0)
        if not lines:
            raise UserError(_("Nothing to order."))

        order_lines = []
        for line in lines:
            product = line.product_id
            order_lines.append((0, 0, {
                "product_id": product.id,
                "name": line.description or product.display_name,
                "product_qty": line.quantity,
                "product_uom_id": line.uom_id.id,
                "price_unit": line.unit_price or product.standard_price,
                "date_planned": fields.Datetime.now(),
            }))

        order = self.env["purchase.order"].create({
            "partner_id": self.vendor_id.id,
            "origin": self.name,
            "currency_id": self.currency_id.id,
            "order_line": order_lines,
        })

        self.write({"purchase_order_id": order.id, "state": "po_created"})
        self.message_post(
            body=_("Purchase order %s created.") % order.name)
        return self.action_view_po()

    def action_view_po(self):
        self.ensure_one()
        if not self.purchase_order_id:
            raise UserError(_("No purchase order linked to this request."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Purchase Order"),
            "res_model": "purchase.order",
            "res_id": self.purchase_order_id.id,
            "view_mode": "form",
        }

    def action_check_receipt(self):
        """Close the request when its PO has been fully received."""
        for rec in self:
            rec._sync_receipt_state()
        return True

    def _sync_receipt_state(self):
        """Move to received/done once every ordered line is fully received."""
        self.ensure_one()
        order = self.purchase_order_id
        if not order or self.state not in ("po_created", "received"):
            return False

        order_lines = order.order_line.filtered(lambda l: l.product_id)
        if not order_lines:
            return False

        # qty_received is maintained by Odoo from the stock moves; comparing it
        # against product_qty is the version-stable way to ask "did it arrive?".
        outstanding = order_lines.filtered(
            lambda l: (l.qty_received or 0.0) < (l.product_qty or 0.0))

        if outstanding:
            return False

        self.write({
            "state": "done",
            "received_date": fields.Datetime.now(),
        })
        self.message_post(
            body=_("All products received against %s. Request closed.") % order.name)
        return True

    received_date = fields.Datetime(readonly=True)

    @api.model
    def cron_check_receipts(self):
        """Close requests whose purchase orders have landed."""
        pending = self.sudo().search([
            ("state", "=", "po_created"),
            ("purchase_order_id", "!=", False),
        ])
        closed = 0
        for request in pending:
            if request._sync_receipt_state():
                closed += 1
        if closed:
            _logger.info("[Spare] Closed %s request(s) on receipt", closed)


class SpareRequestLine(models.Model):
    _name = "spare.request.line"
    _description = "Spare Request Line"

    request_id = fields.Many2one(
        "spare.request", required=True, ondelete="cascade", index=True)
    product_id = fields.Many2one(
        "product.product", string="Product", required=True,
        domain="[('is_spare', '=', True)]",
        help="Only products flagged as spare parts are offered.")
    description = fields.Char()
    quantity = fields.Float(string="Qty Requested", default=1.0, required=True)
    uom_id = fields.Many2one(
        "uom.uom", string="UoM", related="product_id.uom_id", readonly=True)

    available_qty = fields.Float(
        string="On Hand", compute="_compute_available_qty",
        help="Quantity currently available in stock.")
    is_short = fields.Boolean(compute="_compute_available_qty")

    unit_price = fields.Float(string="Unit Price")
    subtotal = fields.Monetary(
        compute="_compute_subtotal", store=True, currency_field="currency_id")
    currency_id = fields.Many2one(
        related="request_id.currency_id", store=True, readonly=True)

    state = fields.Selection(related="request_id.state", store=True)

    @api.depends("quantity", "unit_price")
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.quantity * line.unit_price

    @api.depends("product_id", "quantity")
    def _compute_available_qty(self):
        for line in self:
            product = line.product_id
            if not product:
                line.available_qty = 0.0
                line.is_short = False
                continue
            # qty_available is a non-stored computed field on product.product;
            # read it per record rather than putting it in a domain.
            line.available_qty = product.qty_available
            line.is_short = product.qty_available < line.quantity

    @api.onchange("product_id")
    def _onchange_product_id(self):
        if self.product_id:
            self.description = self.product_id.display_name
            self.unit_price = self.product_id.standard_price