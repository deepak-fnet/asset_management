# -*- coding: utf-8 -*-
"""License management - fully manual entry, linked to assets.

Seats are the point of this model. A license is not just a record of a
purchase; it is a claim about how many machines may legally run the software.
So assigned_seats is computed from real assignment rows rather than typed in,
and over-deployment is surfaced rather than left for an audit to discover.
"""

from datetime import timedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class AssetLicense(models.Model):
    _name = "asset.license"
    _description = "Software License"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "expiry_date, name"

    name = fields.Char(
        string="License Name", required=True, tracking=True,
        help="e.g. 'Microsoft 365 Business Standard', 'AutoCAD 2026'")
    reference = fields.Char(
        string="Internal Reference", copy=False, readonly=True,
        default=lambda self: _("New"))
    active = fields.Boolean(default=True)

    software_name = fields.Char(
        string="Software / Product", tracking=True,
        help="Product name as it appears when installed. Matching this to "
             "what the agents report is how you spot unlicensed installs.")
    version = fields.Char()
    publisher = fields.Char(help="Microsoft, Adobe, Autodesk, ...")
    vendor_id = fields.Many2one(
        "res.partner", string="Purchased From", tracking=True)

    license_type = fields.Selection(
        [("perpetual", "Perpetual"),
         ("subscription", "Subscription"),
         ("volume", "Volume / Enterprise"),
         ("oem", "OEM (tied to device)"),
         ("open_source", "Open Source"),
         ("trial", "Trial")],
        default="subscription", required=True, tracking=True, index=True)

    license_key = fields.Char(
        string="License Key / Serial", groups="base.group_system",
        help="Restricted to system administrators - a volume key is as "
             "sensitive as a password.")

    # ── Seats ─────────────────────────────────────────────────────────────
    total_seats = fields.Integer(
        string="Total Seats", default=1, required=True, tracking=True,
        help="How many installations this license legally permits. "
             "Use 0 for unlimited / site licences.")
    is_unlimited = fields.Boolean(
        compute="_compute_seats", store=True, string="Unlimited")
    assigned_seats = fields.Integer(
        compute="_compute_seats", store=True, string="Assigned")
    available_seats = fields.Integer(
        compute="_compute_seats", store=True, string="Available")
    seat_usage_pct = fields.Float(
        compute="_compute_seats", store=True, string="Usage %")
    is_over_deployed = fields.Boolean(
        compute="_compute_seats", store=True, string="Over-Deployed",
        index=True,
        help="More assets assigned than seats owned. This is the number an "
             "audit would penalise you for.")

    # ── Dates / money ─────────────────────────────────────────────────────
    purchase_date = fields.Date(tracking=True)
    start_date = fields.Date(tracking=True)
    expiry_date = fields.Date(
        tracking=True,
        help="Leave empty for perpetual licences that never expire.")
    days_to_expiry = fields.Integer(
        compute="_compute_expiry", store=True, string="Days Left")
    state = fields.Selection(
        [("draft", "Draft"),
         ("active", "Active"),
         ("expiring", "Expiring Soon"),
         ("expired", "Expired"),
         ("cancelled", "Cancelled")],
        compute="_compute_expiry", store=True, default="draft",
        tracking=True, index=True)

    cost = fields.Monetary(currency_field="currency_id", tracking=True)
    cost_per_seat = fields.Monetary(
        compute="_compute_seats", store=True, currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id)
    renewal_cost = fields.Monetary(currency_field="currency_id")
    auto_renew = fields.Boolean(tracking=True)

    po_reference = fields.Char(string="PO / Invoice Reference")
    notes = fields.Text()
    attachment_file = fields.Binary(string="License Document")
    attachment_filename = fields.Char()

    assignment_ids = fields.One2many(
        "asset.license.assignment", "license_id", string="Assignments")

    _reference_uniq = models.Constraint(
        "UNIQUE(reference)", "License reference must be unique.")

    # ══════════════════════════════════════════════════════════════════════
    @api.depends("total_seats", "cost",
                 "assignment_ids.state", "assignment_ids.license_id")
    def _compute_seats(self):
        for rec in self:
            # 0 means unlimited / site licence - a real and common case that
            # must not be reported as "over-deployed by N".
            rec.is_unlimited = rec.total_seats <= 0
            active_assignments = rec.assignment_ids.filtered(
                lambda a: a.state == "active")
            rec.assigned_seats = len(active_assignments)

            if rec.is_unlimited:
                rec.available_seats = 0
                rec.seat_usage_pct = 0.0
                rec.is_over_deployed = False
                rec.cost_per_seat = 0.0
                continue

            rec.available_seats = rec.total_seats - rec.assigned_seats
            rec.seat_usage_pct = round(
                100.0 * rec.assigned_seats / rec.total_seats, 1)
            rec.is_over_deployed = rec.assigned_seats > rec.total_seats
            rec.cost_per_seat = (rec.cost / rec.total_seats) if rec.cost else 0.0

    @api.model
    def _license_alert_days(self):
        return int(self.env["ir.config_parameter"].sudo().get_param(
            "asset_warranty_license.license_alert_days", default="30"))

    @api.depends("expiry_date", "start_date", "active")
    def _compute_expiry(self):
        today = fields.Date.today()
        window = self._license_alert_days()
        for rec in self:
            if not rec.active:
                rec.state = "cancelled"
                rec.days_to_expiry = 0
                continue
            if not rec.expiry_date:
                # Perpetual: no expiry to compute. Still "active" once it has
                # started, so it is not left sitting in Draft forever.
                rec.days_to_expiry = 0
                rec.state = "active" if (
                    not rec.start_date or rec.start_date <= today) else "draft"
                continue
            days = (rec.expiry_date - today).days
            rec.days_to_expiry = days
            if days < 0:
                rec.state = "expired"
            elif days <= window:
                rec.state = "expiring"
            elif rec.start_date and rec.start_date > today:
                rec.state = "draft"
            else:
                rec.state = "active"

    @api.constrains("total_seats")
    def _check_total_seats(self):
        for rec in self:
            if rec.total_seats < 0:
                raise ValidationError(_("Total seats cannot be negative. "
                                        "Use 0 for an unlimited licence."))

    @api.constrains("start_date", "expiry_date")
    def _check_dates(self):
        for rec in self:
            if rec.start_date and rec.expiry_date and \
                    rec.expiry_date < rec.start_date:
                raise ValidationError(_("Expiry date cannot be before the "
                                        "start date."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("reference", _("New")) == _("New"):
                vals["reference"] = self.env["ir.sequence"].next_by_code(
                    "asset.license") or _("New")
        return super().create(vals_list)

    # ── Actions ───────────────────────────────────────────────────────────
    def action_view_assignments(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Assignments - %s") % self.name,
            "res_model": "asset.license.assignment",
            "view_mode": "list,form",
            "domain": [("license_id", "=", self.id)],
            "context": {"default_license_id": self.id},
        }

    def action_assign_asset(self):
        self.ensure_one()
        if not self.is_unlimited and self.available_seats <= 0:
            raise UserError(_(
                "No seats left on %(name)s (%(assigned)s of %(total)s used). "
                "Release a seat or buy more before assigning."
            ) % {"name": self.name, "assigned": self.assigned_seats,
                 "total": self.total_seats})
        return {
            "type": "ir.actions.act_window",
            "name": _("Assign License"),
            "res_model": "asset.license.assignment",
            "view_mode": "form",
            "target": "new",
            "context": {"default_license_id": self.id},
        }

    @api.model
    def cron_license_expiry_alert(self):
        """Chatter alert on licences approaching expiry."""
        today = fields.Date.today()
        window = self._license_alert_days()
        licenses = self.sudo().search([
            ("expiry_date", "!=", False),
            ("expiry_date", ">=", today),
            ("expiry_date", "<=", today + timedelta(days=window)),
            ("active", "=", True),
        ])
        for lic in licenses:
            days = (lic.expiry_date - today).days
            lic.message_post(
                body=_("License expires in %(days)s day(s) on %(date)s. "
                       "%(seats)s seat(s) currently assigned. "
                       "Auto-renew: %(auto)s") % {
                    "days": days, "date": lic.expiry_date,
                    "seats": lic.assigned_seats,
                    "auto": _("Yes") if lic.auto_renew else _("No"),
                },
                subject=_("License Expiry Alert"),
                message_type="notification",
            )
        return True


class AssetLicenseAssignment(models.Model):
    _name = "asset.license.assignment"
    _description = "License Assignment"
    _inherit = ["mail.thread"]
    _order = "assigned_date desc, id desc"
    _rec_name = "license_id"

    license_id = fields.Many2one(
        "asset.license", string="License", required=True,
        ondelete="cascade", index=True, tracking=True)
    asset_id = fields.Many2one(
        "asset.asset", string="Asset", required=True,
        ondelete="cascade", index=True, tracking=True,
        help="Which machine this seat is installed on.")

    # Derived from the asset rather than entered separately, so the licence
    # register cannot drift out of step with who actually holds the machine.
    employee_id = fields.Many2one(
        related="asset_id.assigned_employee_id", store=True, string="User")
    department_id = fields.Many2one(
        related="asset_id.department_id", store=True)
    asset_code = fields.Char(related="asset_id.asset_code", store=True)
    serial_number = fields.Char(related="asset_id.serial_number", store=True)

    assigned_date = fields.Date(
        required=True, default=fields.Date.context_today, tracking=True)
    released_date = fields.Date(readonly=True, tracking=True)
    state = fields.Selection(
        [("active", "Active"), ("released", "Released")],
        default="active", required=True, tracking=True, index=True)
    notes = fields.Char()

    license_state = fields.Selection(
        related="license_id.state", store=True, string="License Status")
    expiry_date = fields.Date(related="license_id.expiry_date", store=True)

    _asset_license_uniq = models.Constraint(
        "UNIQUE(license_id, asset_id, state)",
        "This asset already holds an active seat on this license.")

    @api.constrains("license_id", "asset_id", "state")
    def _check_seat_available(self):
        """Block assignment when no seats remain.

        The unique constraint above stops the same asset taking two seats on
        one licence, but not the fleet as a whole exceeding the seat count -
        which is the thing that actually costs money in an audit.
        """
        for rec in self:
            if rec.state != "active":
                continue
            lic = rec.license_id
            if lic.is_unlimited:
                continue
            active_count = self.search_count([
                ("license_id", "=", lic.id),
                ("state", "=", "active"),
            ])
            if active_count > lic.total_seats:
                raise ValidationError(_(
                    "%(name)s has only %(total)s seat(s); this would make "
                    "%(count)s. Release a seat or increase the seat count."
                ) % {"name": lic.name, "total": lic.total_seats,
                     "count": active_count})

    def action_release(self):
        for rec in self:
            if rec.state == "released":
                raise UserError(_("This seat is already released."))
            rec.write({"state": "released",
                       "released_date": fields.Date.today()})
        return True

    def action_reactivate(self):
        return self.write({"state": "active", "released_date": False})


class AssetAssetLicense(models.Model):
    _inherit = "asset.asset"

    license_assignment_ids = fields.One2many(
        "asset.license.assignment", "asset_id", string="Licenses")
    license_count = fields.Integer(
        compute="_compute_license_count", string="Licenses")

    @api.depends("license_assignment_ids.state")
    def _compute_license_count(self):
        for rec in self:
            rec.license_count = len(rec.license_assignment_ids.filtered(
                lambda a: a.state == "active"))

    def action_view_licenses(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Licenses - %s") % self.asset_name,
            "res_model": "asset.license.assignment",
            "view_mode": "list,form",
            "domain": [("asset_id", "=", self.id)],
            "context": {"default_asset_id": self.id},
        }
