# -*- coding: utf-8 -*-
"""Lifecycle movements for assets: transfer, scrap, physical verification.

asset.internal.transfer and asset.removal from the standalone module are
replaced by asset.transfer and asset.scrap here. They work on asset.asset
rather than asset.addition, so they apply to IT and general assets alike -
there was no reason to have two parallel sets of movement records.
"""

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class AssetTransfer(models.Model):
    _name = "asset.transfer"
    _description = "Asset Transfer"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(
        required=True, copy=False, readonly=True,
        default=lambda self: _("New"))
    asset_id = fields.Many2one(
        "asset.asset", string="Asset", required=True,
        ondelete="cascade", index=True, tracking=True)
    asset_code = fields.Char(related="asset_id.asset_code", store=True)
    serial_number = fields.Char(related="asset_id.serial_number", store=True)

    transfer_date = fields.Date(
        required=True, default=fields.Date.context_today, tracking=True)
    transfer_type = fields.Selection(
        [("employee", "Change Assigned Employee"),
         ("location", "Change Location"),
         ("department", "Change Department"),
         ("multiple", "Multiple Changes")],
        required=True, default="employee", tracking=True)
    reason = fields.Text(required=True)

    # ── From: snapshotted when the transfer is created, not related ───────
    # A related field would follow the asset and show the NEW values once the
    # transfer completes, making the record claim it moved from wherever it
    # currently is - which destroys the audit trail entirely.
    from_employee_id = fields.Many2one(
        "hr.employee", string="From Employee", readonly=True)
    from_location_id = fields.Many2one(
        "asset.location", string="From Location", readonly=True)
    from_department_id = fields.Many2one(
        "hr.department", string="From Department", readonly=True)

    # ── To ────────────────────────────────────────────────────────────────
    to_employee_id = fields.Many2one("hr.employee", string="To Employee")
    to_location_id = fields.Many2one("asset.location", string="To Location")
    to_department_id = fields.Many2one("hr.department", string="To Department")

    state = fields.Selection(
        [("draft", "Draft"),
         ("submitted", "Submitted"),
         ("approved", "Approved"),
         ("done", "Done"),
         ("cancelled", "Cancelled")],
        default="draft", required=True, tracking=True, index=True)

    requested_by = fields.Many2one(
        "res.users", default=lambda self: self.env.user, readonly=True)
    approved_by = fields.Many2one("res.users", readonly=True)
    approved_on = fields.Datetime(readonly=True)
    completed_on = fields.Datetime(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "asset.transfer") or _("New")
            # Capture the "from" side at creation time.
            if vals.get("asset_id"):
                asset = self.env["asset.asset"].browse(vals["asset_id"])
                vals.setdefault("from_employee_id",
                                asset.assigned_employee_id.id or False)
                vals.setdefault("from_location_id",
                                asset.location_id.id or False)
                vals.setdefault("from_department_id",
                                asset.department_id.id or False)
        return super().create(vals_list)

    @api.onchange("asset_id")
    def _onchange_asset_id(self):
        if self.asset_id:
            self.from_employee_id = self.asset_id.assigned_employee_id
            self.from_location_id = self.asset_id.location_id
            self.from_department_id = self.asset_id.department_id

    @api.constrains("transfer_type", "to_employee_id", "to_location_id",
                    "to_department_id")
    def _check_destination(self):
        for rec in self:
            if rec.state == "draft":
                continue
            if rec.transfer_type == "employee" and not rec.to_employee_id:
                raise ValidationError(_("Select the employee to transfer to."))
            if rec.transfer_type == "location" and not rec.to_location_id:
                raise ValidationError(_("Select the location to transfer to."))
            if rec.transfer_type == "department" and not rec.to_department_id:
                raise ValidationError(_("Select the department to transfer to."))
            if rec.transfer_type == "multiple" and not (
                    rec.to_employee_id or rec.to_location_id
                    or rec.to_department_id):
                raise ValidationError(_("Set at least one destination."))

    # ── Workflow ──────────────────────────────────────────────────────────
    def action_submit(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only draft transfers can be submitted."))
            rec.state = "submitted"
        return True

    def action_approve(self):
        for rec in self:
            if rec.state != "submitted":
                raise UserError(_("Only submitted transfers can be approved."))
            rec.write({"state": "approved",
                       "approved_by": self.env.user.id,
                       "approved_on": fields.Datetime.now()})
        return True

    def action_done(self):
        """Apply the change to the asset."""
        for rec in self:
            if rec.state != "approved":
                raise UserError(_(
                    "Transfer %s must be approved before completing.") % rec.name)

            vals = {}
            if rec.to_employee_id:
                vals["assigned_employee_id"] = rec.to_employee_id.id
                # Assigning to somebody makes the asset assigned; leaving it
                # in draft after a completed transfer would misreport it.
                if rec.asset_id.state == "draft":
                    vals["state"] = "assigned"
            if rec.to_location_id:
                vals["location_id"] = rec.to_location_id.id
            if rec.to_department_id and not rec.to_employee_id:
                # department_id is related to the employee, so it can only be
                # set directly when the employee is not also changing -
                # otherwise the employee's own department wins anyway.
                vals["department_id"] = rec.to_department_id.id

            if vals:
                rec.asset_id.write(vals)

            rec.write({"state": "done",
                       "completed_on": fields.Datetime.now()})

            detail = rec._describe_change()

            # Log into the asset's own assignment history rather than only in
            # chatter. That model is what the Assignment History tab on the
            # asset form reads, so a transfer recorded only in chatter would
            # leave a visible gap in the asset's movement record - the very
            # thing someone opens that tab to see.
            self.env["asset.assignment.history"].sudo().create({
                "asset_id": rec.asset_id.id,
                # Only set for an employee transfer. For a location or
                # department move nobody's holding changed, and naming the
                # current holder here would read as if they had just been
                # given the asset.
                "employee_id": rec.to_employee_id.id if rec.to_employee_id else False,
                "action": "transfer",
                "description": "%s: %s" % (rec.name, detail),
            })

            rec.asset_id.message_post(body=_(
                "Transfer %(name)s completed. %(detail)s"
            ) % {"name": rec.name, "detail": detail})
        return True

    def _describe_change(self):
        """Plain-words summary of what moved, e.g.

            "Moved from Floor 1 to Floor 2"
            "Department changed from IT to Sales"
            "Reassigned from Priya to Kumar"

        This is what lands in asset.assignment.history.description, so it has
        to read on its own - someone scanning the Assignment History tab sees
        only this line, without the transfer record open beside it.
        """
        self.ensure_one()
        parts = []
        if self.to_location_id:
            parts.append(_("Moved from %(old)s to %(new)s") % {
                "old": self.from_location_id.display_name or _("no location"),
                "new": self.to_location_id.display_name})
        if self.to_department_id:
            parts.append(_("Department changed from %(old)s to %(new)s") % {
                "old": self.from_department_id.name or _("none"),
                "new": self.to_department_id.name})
        if self.to_employee_id:
            parts.append(_("Reassigned from %(old)s to %(new)s") % {
                "old": self.from_employee_id.name or _("unassigned"),
                "new": self.to_employee_id.name})
        return ". ".join(parts) or _("No change recorded.")

    def action_cancel(self):
        for rec in self:
            if rec.state == "done":
                raise UserError(_(
                    "A completed transfer cannot be cancelled. Raise a new "
                    "transfer to move the asset back."))
            rec.state = "cancelled"
        return True

    def action_reset_draft(self):
        return self.write({"state": "draft"})


class AssetScrap(models.Model):
    _name = "asset.scrap"
    _description = "Asset Scrap / Removal"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(
        required=True, copy=False, readonly=True,
        default=lambda self: _("New"))
    asset_id = fields.Many2one(
        "asset.asset", string="Asset", required=True,
        ondelete="cascade", index=True, tracking=True)
    asset_code = fields.Char(related="asset_id.asset_code", store=True)
    serial_number = fields.Char(related="asset_id.serial_number", store=True)
    category_id = fields.Many2one(related="asset_id.category_id", store=True)
    employee_id = fields.Many2one(
        related="asset_id.assigned_employee_id", store=True,
        string="Held By")

    scrap_date = fields.Date(
        required=True, default=fields.Date.context_today, tracking=True)
    scrap_reason = fields.Selection(
        [("end_of_life", "End of Life"),
         ("damaged", "Damaged Beyond Repair"),
         ("obsolete", "Obsolete"),
         ("lost", "Lost / Stolen"),
         ("sold", "Sold"),
         ("other", "Other")],
        required=True, tracking=True)
    description = fields.Text(required=True)

    disposal_method = fields.Selection(
        [("scrap", "Scrapped"),
         ("sold", "Sold"),
         ("returned", "Returned to Vendor"),
         ("donated", "Donated"),
         ("recycled", "Recycled")],
        tracking=True)
    disposal_value = fields.Monetary(
        currency_field="currency_id",
        help="Amount recovered, if the asset was sold or returned.")
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id)

    state = fields.Selection(
        [("draft", "Draft"),
         ("submitted", "Submitted"),
         ("approved", "Approved"),
         ("done", "Done"),
         ("cancelled", "Cancelled")],
        default="draft", required=True, tracking=True, index=True)

    requested_by = fields.Many2one(
        "res.users", default=lambda self: self.env.user, readonly=True)
    approved_by = fields.Many2one("res.users", readonly=True)
    approved_on = fields.Datetime(readonly=True)
    completed_on = fields.Datetime(readonly=True)

    attachment_file = fields.Binary("Supporting Document")
    attachment_filename = fields.Char()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "asset.scrap") or _("New")
        return super().create(vals_list)

    @api.constrains("asset_id", "state")
    def _check_not_already_scrapped(self):
        for rec in self:
            if rec.state in ("draft", "cancelled"):
                continue
            if rec.asset_id.state == "scrapped":
                other = self.search([
                    ("asset_id", "=", rec.asset_id.id),
                    ("state", "=", "done"),
                    ("id", "!=", rec.id),
                ], limit=1)
                if other:
                    raise ValidationError(_(
                        "%(asset)s was already scrapped by %(ref)s."
                    ) % {"asset": rec.asset_id.display_name, "ref": other.name})

    def action_submit(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only draft records can be submitted."))
            rec.state = "submitted"
        return True

    def action_approve(self):
        for rec in self:
            if rec.state != "submitted":
                raise UserError(_("Only submitted records can be approved."))
            rec.write({"state": "approved",
                       "approved_by": self.env.user.id,
                       "approved_on": fields.Datetime.now()})
        return True

    def action_done(self):
        for rec in self:
            if rec.state != "approved":
                raise UserError(_(
                    "Scrap %s must be approved before completing.") % rec.name)
            # Unassign as well as scrap: leaving a scrapped asset showing
            # against an employee makes their assigned-asset list wrong, and
            # is the usual cause of "why do I still have that laptop".
            rec.asset_id.write({
                "state": "scrapped",
                "assigned_employee_id": False,
            })
            rec.write({"state": "done",
                       "completed_on": fields.Datetime.now()})
            rec.asset_id.message_post(body=_(
                "Scrapped via %(ref)s. Reason: %(reason)s"
            ) % {"ref": rec.name,
                 "reason": dict(rec._fields["scrap_reason"].selection).get(
                     rec.scrap_reason, rec.scrap_reason)})
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state == "done":
                raise UserError(_(
                    "A completed scrap cannot be cancelled. Change the "
                    "asset's status directly if it was scrapped in error."))
            rec.state = "cancelled"
        return True

    def action_reset_draft(self):
        return self.write({"state": "draft"})


class PhysicalVerification(models.Model):
    _name = "physical.verification"
    _description = "Physical Verification"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(
        required=True, copy=False, readonly=True,
        default=lambda self: _("New"))
    verification_date = fields.Date(
        required=True, default=fields.Date.context_today, tracking=True)
    plant_id = fields.Many2one("plant.master", string="Plant")
    location_id = fields.Many2one("asset.location", string="Location")
    department_id = fields.Many2one("hr.department", string="Department")
    verified_by = fields.Many2one(
        "res.users", default=lambda self: self.env.user, tracking=True)
    notes = fields.Text()

    line_ids = fields.One2many(
        "physical.verification.line", "verification_id", string="Assets")

    total_count = fields.Integer(compute="_compute_counts", store=True)
    found_count = fields.Integer(compute="_compute_counts", store=True)
    missing_count = fields.Integer(compute="_compute_counts", store=True)

    state = fields.Selection(
        [("draft", "Draft"),
         ("in_progress", "In Progress"),
         ("done", "Completed"),
         ("cancelled", "Cancelled")],
        default="draft", required=True, tracking=True, index=True)

    @api.depends("line_ids.status")
    def _compute_counts(self):
        for rec in self:
            rec.total_count = len(rec.line_ids)
            rec.found_count = len(rec.line_ids.filtered(
                lambda l: l.status == "found"))
            rec.missing_count = len(rec.line_ids.filtered(
                lambda l: l.status == "missing"))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "physical.verification") or _("New")
        return super().create(vals_list)

    def action_load_assets(self):
        """Populate the lines from the assets matching the filters."""
        self.ensure_one()
        domain = [("state", "!=", "scrapped")]
        if self.plant_id:
            domain.append(("plant_id", "=", self.plant_id.id))
        if self.location_id:
            domain.append(("location_id", "=", self.location_id.id))
        if self.department_id:
            domain.append(("department_id", "=", self.department_id.id))

        assets = self.env["asset.asset"].search(domain)
        if not assets:
            raise UserError(_("No assets match those filters."))

        existing = set(self.line_ids.mapped("asset_id").ids)
        Line = self.env["physical.verification.line"]
        created = 0
        for asset in assets:
            if asset.id in existing:
                continue
            Line.create({
                "verification_id": self.id,
                "asset_id": asset.id,
                "status": "pending",
            })
            created += 1

        self.state = "in_progress"
        self.message_post(body=_("%s asset(s) loaded for verification.") % created)
        return True

    def action_done(self):
        for rec in self:
            pending = rec.line_ids.filtered(lambda l: l.status == "pending")
            if pending:
                raise UserError(_(
                    "%s line(s) are still pending. Mark every asset as found "
                    "or missing before completing.") % len(pending))
            rec.state = "done"
            rec.message_post(body=_(
                "Verification complete: %(found)s found, %(missing)s missing "
                "out of %(total)s."
            ) % {"found": rec.found_count, "missing": rec.missing_count,
                 "total": rec.total_count})
        return True

    def action_cancel(self):
        return self.write({"state": "cancelled"})


class PhysicalVerificationLine(models.Model):
    _name = "physical.verification.line"
    _description = "Physical Verification Line"
    _order = "verification_id, id"

    verification_id = fields.Many2one(
        "physical.verification", required=True, ondelete="cascade", index=True)
    asset_id = fields.Many2one(
        "asset.asset", string="Asset", required=True, ondelete="cascade")
    asset_code = fields.Char(related="asset_id.asset_code", store=True)
    tag_number = fields.Char(related="asset_id.tag_number", store=True)
    serial_number = fields.Char(related="asset_id.serial_number", store=True)
    employee_id = fields.Many2one(
        related="asset_id.assigned_employee_id", store=True)
    expected_location_id = fields.Many2one(
        related="asset_id.location_id", store=True, string="Expected Location")

    status = fields.Selection(
        [("pending", "Pending"),
         ("found", "Found"),
         ("missing", "Missing")],
        default="pending", required=True)
    actual_location_id = fields.Many2one(
        "asset.location", string="Actual Location",
        help="Set when the asset was found somewhere other than expected.")
    remarks = fields.Char()

    _asset_per_verification_uniq = models.Constraint(
        "unique (verification_id, asset_id)",
        "This asset is already on this verification.")

    def action_mark_found(self):
        return self.write({"status": "found"})

    def action_mark_missing(self):
        return self.write({"status": "missing"})


class AssetRfidComparison(models.Model):
    """Reconciles an RFID scan file against the asset register."""
    _name = "asset.rfid.comparison"
    _description = "RFID Comparison"
    _inherit = ["mail.thread"]
    _order = "id desc"

    name = fields.Char(
        required=True, copy=False, readonly=True,
        default=lambda self: _("New"))
    scan_date = fields.Date(
        required=True, default=fields.Date.context_today, tracking=True)
    plant_id = fields.Many2one("plant.master", string="Plant")
    location_id = fields.Many2one("asset.location", string="Location")
    notes = fields.Text()

    scan_file = fields.Binary("RFID Scan File")
    scan_filename = fields.Char()

    line_ids = fields.One2many(
        "asset.rfid.comparison.line", "comparison_id", string="Scanned Tags")

    matched_count = fields.Integer(compute="_compute_counts", store=True)
    unmatched_count = fields.Integer(compute="_compute_counts", store=True)
    not_scanned_count = fields.Integer(compute="_compute_counts", store=True)

    state = fields.Selection(
        [("draft", "Draft"),
         ("compared", "Compared"),
         ("done", "Closed")],
        default="draft", required=True, tracking=True)

    @api.depends("line_ids.match_state")
    def _compute_counts(self):
        for rec in self:
            rec.matched_count = len(rec.line_ids.filtered(
                lambda l: l.match_state == "matched"))
            rec.unmatched_count = len(rec.line_ids.filtered(
                lambda l: l.match_state == "unmatched"))
            rec.not_scanned_count = len(rec.line_ids.filtered(
                lambda l: l.match_state == "not_scanned"))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "asset.rfid.comparison") or _("New")
        return super().create(vals_list)

    def action_compare(self):
        """Match scanned tags against asset.tag_number, both directions.

        Scanned-but-unknown and known-but-not-scanned are different problems
        - one suggests an untagged or foreign asset on site, the other a
        missing asset - so both are recorded rather than only the overlap.
        """
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_(
                "Add the scanned tags first, then compare."))

        Asset = self.env["asset.asset"]
        scanned_tags = set()

        for line in self.line_ids.filtered(
                lambda l: l.match_state != "not_scanned"):
            tag = (line.tag_number or "").strip()
            scanned_tags.add(tag)
            asset = Asset.search([("tag_number", "=", tag)], limit=1) if tag else Asset
            if asset:
                line.write({"asset_id": asset.id, "match_state": "matched"})
            else:
                line.write({"asset_id": False, "match_state": "unmatched"})

        # Assets expected here that no tag was scanned for.
        domain = [("tag_number", "!=", False), ("state", "!=", "scrapped")]
        if self.plant_id:
            domain.append(("plant_id", "=", self.plant_id.id))
        if self.location_id:
            domain.append(("location_id", "=", self.location_id.id))

        Line = self.env["asset.rfid.comparison.line"]
        for asset in Asset.search(domain):
            if (asset.tag_number or "").strip() in scanned_tags:
                continue
            Line.create({
                "comparison_id": self.id,
                "tag_number": asset.tag_number,
                "asset_id": asset.id,
                "match_state": "not_scanned",
            })

        self.state = "compared"
        self.message_post(body=_(
            "Compared: %(m)s matched, %(u)s scanned but unknown, "
            "%(n)s expected but not scanned."
        ) % {"m": self.matched_count, "u": self.unmatched_count,
             "n": self.not_scanned_count})
        return True

    def action_close(self):
        return self.write({"state": "done"})

    def action_reset_draft(self):
        return self.write({"state": "draft"})


class AssetRfidComparisonLine(models.Model):
    _name = "asset.rfid.comparison.line"
    _description = "RFID Comparison Line"
    _order = "comparison_id, match_state, id"

    comparison_id = fields.Many2one(
        "asset.rfid.comparison", required=True, ondelete="cascade", index=True)
    tag_number = fields.Char("Tag ID", required=True, index=True)
    asset_id = fields.Many2one("asset.asset", string="Matched Asset")
    asset_name = fields.Char(related="asset_id.asset_name", store=True)
    employee_id = fields.Many2one(
        related="asset_id.assigned_employee_id", store=True)
    location_id = fields.Many2one(related="asset_id.location_id", store=True)

    match_state = fields.Selection(
        [("pending", "Not Compared"),
         ("matched", "Matched"),
         ("unmatched", "Scanned - Unknown Tag"),
         ("not_scanned", "Expected - Not Scanned")],
        default="pending", required=True, index=True)
    remarks = fields.Char()