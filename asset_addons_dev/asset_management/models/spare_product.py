# -*- coding: utf-8 -*-
"""Spare-part flag on products, and the repair-order buttons that use it."""

from odoo import models, fields, api, _
from odoo.exceptions import UserError


class ProductTemplate(models.Model):
    _inherit = "product.template"

    is_spare = fields.Boolean(
        string="Is Spare Part", index=True,
        help="Tick to make this product selectable in spare requests and "
             "repair order spare lines, and to list it under Spare Products.")
    spare_category = fields.Selection(
        [("battery", "Battery"),
         ("screen", "Screen / Display"),
         ("keyboard", "Keyboard"),
         ("ram", "RAM"),
         ("storage", "Storage / Disk"),
         ("adapter", "Adapter / Charger"),
         ("motherboard", "Motherboard"),
         ("peripheral", "Peripheral"),
         ("other", "Other")],
        string="Spare Category")
    min_stock_alert = fields.Float(
        string="Minimum Stock Alert", default=0.0,
        help="Below this on-hand quantity the product is flagged as low stock.")
    is_low_stock = fields.Boolean(
        compute="_compute_is_low_stock", string="Low Stock")

    @api.depends("qty_available", "min_stock_alert")
    def _compute_is_low_stock(self):
        for product in self:
            product.is_low_stock = bool(
                product.is_spare
                and product.min_stock_alert > 0
                and product.qty_available < product.min_stock_alert)


class ProductProduct(models.Model):
    _inherit = "product.product"

    # Mirrored onto the variant so `domain="[('is_spare','=',True)]"` works on
    # the product.product Many2one used by request and repair lines.
    is_spare = fields.Boolean(
        related="product_tmpl_id.is_spare", store=True, index=True, readonly=True)


class RepairManagement(models.Model):
    _inherit = "repair.management"

    spare_request_ids = fields.One2many(
        "spare.request", "repair_id", string="Spare Requests")
    spare_request_count = fields.Integer(compute="_compute_spare_request_count")
    spare_confirmed = fields.Boolean(
        string="Spares Confirmed", readonly=True, copy=False,
        help="Set once spares are taken out of stock. Locks the lines.")

    def action_confirm_use_spare(self):
        """Reduce spare line quantities from stock, then lock the lines."""
        self.ensure_one()

        if self.spare_confirmed:
            raise UserError(_("Spares are already confirmed for this repair."))
        lines = self.spare_part_ids.filtered(lambda l: l.product_id and l.quantity > 0)
        if not lines:
            raise UserError(_("Add spare part lines first."))

        picking_type = self.env["stock.picking.type"].search([
            ("code", "=", "internal"),
            ("company_id", "=", self.env.company.id),
        ], limit=1) or self.env.ref("stock.picking_type_internal", raise_if_not_found=False)
        if not picking_type or not picking_type.default_location_src_id:
            raise UserError(_("Configure an internal transfer type with a source location."))

        source = picking_type.default_location_src_id
        dest = picking_type.default_location_dest_id or source

        # Aggregate per product first: two lines of 3 against 5 on hand is a
        # shortage, but checked line by line both would pass.
        needed = {}
        for line in lines:
            needed[line.product_id] = needed.get(line.product_id, 0.0) + line.quantity

        short = []
        for product, qty in needed.items():
            # Odoo 18 removed type='product'; storable is is_storable now.
            if not product.is_storable:
                continue
            on_hand = product.with_context(location=source.id).qty_available
            if on_hand < qty:
                short.append(_("• %s: need %.2f, on hand %.2f") % (
                    product.display_name, qty, on_hand))
        if short:
            raise UserError(_("Stock would go negative:\n\n%s") % "\n".join(short))

        picking = self.env["stock.picking"].create({
            "picking_type_id": picking_type.id,
            "location_id": source.id,
            "location_dest_id": dest.id,
            "origin": _("Repair %s") % self.name,
            "company_id": self.env.company.id,
        })
        for line in lines:
            for line in lines:
                self.env["stock.move"].create({
                    # Odoo 19 removed stock.move.name. _rec_name is now the
                    # computed 'reference' field, so the free-text label lives in
                    # description_picking instead.
                    "description_picking": _("Spare for %s") % self.name,
                    "product_id": line.product_id.id,
                    "product_uom_qty": line.quantity,
                    "product_uom": line.product_id.uom_id.id,
                    "picking_id": picking.id,
                    "location_id": source.id,
                    "location_dest_id": dest.id,
                    "company_id": self.env.company.id,
                })

        picking.action_confirm()
        picking.action_assign()

        # Someone else may have consumed the same stock between the check and
        # now. If reservation fell short, undo everything rather than validate
        # a partial transfer that silently consumes less than asked.
        unreserved = [
            _("• %s: wanted %.2f, reserved %.2f") % (
                m.product_id.display_name, m.product_uom_qty, m.quantity or 0.0)
            for m in picking.move_ids if (m.quantity or 0.0) < m.product_uom_qty
        ]
        if unreserved:
            picking.action_cancel()
            picking.unlink()
            raise UserError(_("Could not reserve stock:\n\n%s\n\nNothing consumed.")
                            % "\n".join(unreserved))

        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        picking.button_validate()

        self.spare_confirmed = True
        self.message_post(body=_("Spares consumed from stock. Transfer: %s") % picking.name)
        return True

    @api.depends("spare_request_ids")
    def _compute_spare_request_count(self):
        for rec in self:
            rec.spare_request_count = len(rec.spare_request_ids)

    def action_check_spare_availability(self):
        """Report on-hand stock for every spare line on this repair."""
        self.ensure_one()
        if not self.spare_part_ids:
            raise UserError(_("Add spare parts to this repair order first."))

        short, ok = [], []
        for line in self.spare_part_ids:
            product = line.product_id
            if not product:
                continue
            on_hand = product.qty_available
            if on_hand < line.quantity:
                short.append(_("• %s: need %.2f, on hand %.2f") % (
                    product.display_name, line.quantity, on_hand))
            else:
                ok.append(product.display_name)

        if not short:
            message = _("All %s spare part(s) are in stock.") % len(ok)
            kind = "success"
        else:
            message = _("Short on stock:\n%s\n\nUse Request Spares to raise a "
                        "purchase request.") % "\n".join(short)
            kind = "warning"

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Spare Availability"),
                "message": message,
                "type": kind,
                "sticky": bool(short),
            },
        }

    def action_request_spares(self):
        """Open a pre-filled spare request for the short lines.

        Only lines that are actually short are carried over - requesting parts
        already sitting in stock is the most common cause of duplicate POs.
        """
        self.ensure_one()
        if not self.spare_part_ids:
            raise UserError(_("Add spare parts to this repair order first."))

        lines = []
        for line in self.spare_part_ids:
            product = line.product_id
            if not product:
                continue
            shortfall = line.quantity - product.qty_available
            if shortfall <= 0:
                continue
            lines.append((0, 0, {
                "product_id": product.id,
                "description": line.description or product.display_name,
                "quantity": shortfall,
                "unit_price": line.cost or product.standard_price,
            }))

        if not lines:
            raise UserError(_(
                "Every spare part on this repair is already in stock. "
                "No request is needed."))

        return {
            "type": "ir.actions.act_window",
            "name": _("Request Spares"),
            "res_model": "spare.request",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_repair_id": self.id,
                "default_asset_id": self.asset_id.id,
                "default_reason": _("Spares for repair %s") % self.name,
                "default_line_ids": lines,
            },
        }

    def action_view_spare_requests(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Spare Requests"),
            "res_model": "spare.request",
            "view_mode": "list,form",
            "domain": [("repair_id", "=", self.id)],
            "context": {"default_repair_id": self.id,
                        "default_asset_id": self.asset_id.id},
        }


class RepairSparePart(models.Model):
    _inherit = "repair.spare.part"

    available_qty = fields.Float(
        string="On Hand", compute="_compute_available_qty")
    is_short = fields.Boolean(compute="_compute_available_qty", string="Short")

    @api.depends("product_id", "quantity")
    def _compute_available_qty(self):
        for line in self:
            product = line.product_id
            # qty_available is computed and not stored, so it is read per
            # record here and never used inside a search domain.
            line.available_qty = product.qty_available if product else 0.0
            line.is_short = bool(product) and product.qty_available < line.quantity