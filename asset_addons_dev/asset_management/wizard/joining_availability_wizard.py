# -*- coding: utf-8 -*-
"""Stock availability check for a joining process.

Replaces the silent pass/fail of the old action_validate(), which either
raised or moved on with nothing shown. The approver could not see WHAT was
available, only that something was - so "available" was taken on trust and
the shortfall only surfaced later at the Assign step.

This shows the actual units, per requested category, before validating.
"""

from odoo import models, fields, api, _
from odoo.exceptions import UserError


class JoiningAvailabilityWizard(models.TransientModel):
    _name = "joining.availability.wizard"
    _description = "Check Asset Availability"

    joining_id = fields.Many2one(
        "asset.joining.process", required=True, ondelete="cascade")
    line_ids = fields.One2many(
        "joining.availability.wizard.line", "wizard_id", string="Categories")

    all_available = fields.Boolean(compute="_compute_summary")
    total_requested = fields.Integer(compute="_compute_summary")
    total_available = fields.Integer(compute="_compute_summary")
    shortfall = fields.Integer(compute="_compute_summary")

    @api.depends("line_ids.requested_qty", "line_ids.available_qty")
    def _compute_summary(self):
        for rec in self:
            rec.total_requested = sum(rec.line_ids.mapped("requested_qty"))
            rec.total_available = sum(rec.line_ids.mapped("available_qty"))
            rec.shortfall = sum(
                max(0, l.requested_qty - l.available_qty) for l in rec.line_ids)
            rec.all_available = not rec.shortfall

    @api.model
    def _available_domain(self, category):
        """The ONE definition of "assignable stock".

        Deliberately identical to the domain on the Assign Asset field in
        the joining process form. If these two ever drift, this wizard
        promises units that the assign step then refuses to offer - which is
        precisely the confusion it exists to remove.
        """
        domain = [
            ("category_id", "=", category.id),
            ("state", "=", "draft"),
            # Only FIXED assets are assignable - agent-created records exist
            # for telemetry visibility and are never assigned themselves; the
            # fixed asset they are mapped to is what gets assigned.
            #
            # is_general_asset (the stricter, purpose-built flag for this)
            # lives in the OPTIONAL general_asset module, not in
            # asset_management - this file is part of asset_management, which
            # must work whether or not general_asset is installed. Hardcoding
            # a field from an addon that may not be there crashed view
            # loading outright ("Unknown field asset.asset.is_general_asset"),
            # which is a much worse failure than a slightly looser domain.
            #
            # monitoring_protocol IS owned by asset_management, so it is the
            # only asset-type distinction usable here unconditionally. If
            # general_asset is installed, it tightens this same domain itself
            # (see general_asset's own inherited view) - this base domain
            # only needs to be correct on its own, not maximally strict.
            ("monitoring_protocol", "!=", "agent"),
        ]
        return domain

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        joining_id = self.env.context.get("active_id")
        if not joining_id:
            return values

        joining = self.env["asset.joining.process"].browse(joining_id)
        Asset = self.env["asset.asset"]

        lines = []
        for req in joining.requirement_ids:
            available = Asset.search(self._available_domain(req.category_id))
            lines.append((0, 0, {
                "requirement_id": req.id,
                "category_id": req.category_id.id,
                "requested_qty": req.quantity,
                "available_qty": len(available),
                "asset_ids": [(6, 0, available.ids)],
            }))

        values.update({"joining_id": joining.id, "line_ids": lines})
        return values

    def action_confirm(self):
        """Validate the joining process, recording what was seen."""
        self.ensure_one()
        joining = self.joining_id

        if not self.total_available:
            raise UserError(_(
                "No assignable stock exists for any requested category. "
                "Raise an Asset Request to purchase."))

        summary = []
        for line in self.line_ids:
            short = line.requested_qty - line.available_qty
            summary.append(_(
                "%(flag)s %(cat)s - requested %(req)d, available %(av)d%(gap)s"
            ) % {
                "flag": "⚠" if short > 0 else "✓",
                "cat": line.category_id.name,
                "req": line.requested_qty,
                "av": line.available_qty,
                "gap": _(" (short by %d)") % short if short > 0 else "",
            })

        joining.message_post(body="<br/>".join(
            [_("Availability checked:")] + summary))
        joining.state = "validate"
        return {"type": "ir.actions.act_window_close"}

    def action_raise_asset_request(self):
        """Shortfall path: go buy the missing units.

        Mirrors asset.joining.process.action_raise_asset_request() - same
        rule (only categories actually short get a line, quantity is the
        GAP, not the requested amount, reuses joining_id.asset_request_id if
        one already exists) - just driven from this wizard's own numbers
        instead of re-deriving availability from the requirement lines.
        """
        self.ensure_one()
        joining = self.joining_id

        short_lines = self.line_ids.filtered(lambda l: l.shortfall > 0)
        if not short_lines:
            raise UserError(_(
                "Every requested category has enough assignable stock - "
                "there is no shortage to raise a request for."))

        request = joining.asset_request_id
        if not request:
            request = self.env["asset.request"].create({
                "requested_by": self.env.uid,
            })
            joining.asset_request_id = request.id

        existing_categories = request.line_ids.mapped("asset_category_id")
        for line in short_lines:
            if line.category_id in existing_categories:
                # Raising a request twice for the same joining process must
                # not double the ask - a category already on the request
                # keeps its existing line rather than getting a second one.
                continue
            request.line_ids.create({
                "request_id": request.id,
                "asset_category_id": line.category_id.id,
                "quantity": line.shortfall,
                "joining_requirement_id": line.requirement_id.id,
                "description": _(
                    "Shortfall for %(employee)s's joining process %(ref)s"
                ) % {"employee": joining.employee_id.name, "ref": joining.name},
            })

        return {
            "type": "ir.actions.act_window",
            "name": _("Asset Request"),
            "res_model": "asset.request",
            "res_id": request.id,
            "view_mode": "form",
            "target": "current",
        }


class JoiningAvailabilityWizardLine(models.TransientModel):
    _name = "joining.availability.wizard.line"
    _description = "Check Asset Availability Line"

    wizard_id = fields.Many2one(
        "joining.availability.wizard", required=True, ondelete="cascade")
    # NOT readonly at model level, deliberately.
    #
    # A readonly model field is stripped from the values the web client sends
    # back on save. The footer buttons save the wizard first, so the o2m lines
    # were being rewritten WITHOUT category_id - and since it is required,
    # every click failed with "Missing required value for the field
    # 'Category'". The fields are made readonly in the VIEW instead, which
    # stops the user editing them without stopping the round trip.
    requirement_id = fields.Many2one("asset.joining.requirement")
    category_id = fields.Many2one("asset.category", required=True)
    requested_qty = fields.Integer()
    available_qty = fields.Integer()
    asset_ids = fields.Many2many("asset.asset", string="Available Units")

    shortfall = fields.Integer(compute="_compute_shortfall")
    status = fields.Selection(
        [("ok", "Available"), ("partial", "Partial"), ("none", "None")],
        compute="_compute_shortfall")

    @api.depends("requested_qty", "available_qty")
    def _compute_shortfall(self):
        for rec in self:
            rec.shortfall = max(0, rec.requested_qty - rec.available_qty)
            if not rec.available_qty:
                rec.status = "none"
            elif rec.shortfall:
                rec.status = "partial"
            else:
                rec.status = "ok"
