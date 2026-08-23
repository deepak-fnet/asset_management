# -*- coding: utf-8 -*-
"""Warranty management.

Design note: no new "warranty contract" model. asset_management already
carries the warranty data on asset.asset itself (warranty_start_date,
warranty_end_date, warranty_period_months, warranty_status, amc_expiry_date,
vendor_id, warranty_file) and the agent + asset.request pipelines already
write to those. This module only ADDS the provider/coverage detail that was
missing, plus a claims history - it does not duplicate or replace anything.
"""

from datetime import timedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class AssetAssetWarranty(models.Model):
    _inherit = "asset.asset"

    # ── Provider / coverage detail ────────────────────────────────────────
    warranty_provider_id = fields.Many2one(
        "res.partner", string="Warranty Provider", tracking=True,
        help="Who honours the warranty - often the manufacturer (Dell, HP) "
             "rather than the reseller the asset was bought from.")
    warranty_type = fields.Selection(
        [("manufacturer", "Manufacturer Warranty"),
         ("extended", "Extended Warranty"),
         ("amc", "AMC / Service Contract"),
         ("none", "No Warranty")],
        string="Warranty Type", default="manufacturer", tracking=True)
    warranty_contract_no = fields.Char(
        string="Contract / Reference No", tracking=True,
        help="Service tag, contract number or reference quoted when raising "
             "a claim with the provider.")
    warranty_support_contact = fields.Char(
        string="Support Contact",
        help="Phone/email/portal used to raise claims for this asset.")
    warranty_coverage_notes = fields.Text(
        string="Coverage Notes",
        help="What is and is not covered - accidental damage, battery, "
             "on-site vs return-to-base, response SLA.")

    # ── Claims ────────────────────────────────────────────────────────────
    warranty_claim_ids = fields.One2many(
        "asset.warranty.claim", "asset_id", string="Warranty Claims")
    warranty_claim_count = fields.Integer(
        compute="_compute_warranty_claim_count", string="Claims")

    # ── Expiry helpers ────────────────────────────────────────────────────
    warranty_days_left = fields.Integer(
        compute="_compute_warranty_days_left", store=True,
        string="Days To Expiry",
        help="Negative once expired. Stored so it can be filtered and sorted.")
    warranty_expiring_soon = fields.Boolean(
        compute="_compute_warranty_days_left", store=True,
        string="Expiring Soon", index=True)

    @api.depends("warranty_claim_ids")
    def _compute_warranty_claim_count(self):
        for rec in self:
            rec.warranty_claim_count = len(rec.warranty_claim_ids)

    @api.model
    def _warranty_alert_days(self):
        return int(self.env["ir.config_parameter"].sudo().get_param(
            "asset_warranty_license.warranty_alert_days", default="30"))

    @api.depends("warranty_end_date", "warranty_status")
    def _compute_warranty_days_left(self):
        """Days until warranty expiry, and a flag for the alert window.

        NOTE: asset_management's cron_check_warranty_expiry() filters on
        warranty_status == 'expiring', but that value does not exist in the
        warranty_status selection (it only has active/expired/no_warranty/
        not_applicable). That domain therefore matches nothing and the
        warranty expiry alert has never actually fired. Rather than change
        the existing selection - which other views and the agent may rely on
        - this module adds its own warranty_expiring_soon boolean, and its
        own cron below that uses it. The broken cron in asset_management is
        left alone; see cron_warranty_expiry_alert().
        """
        today = fields.Date.today()
        window = self._warranty_alert_days()
        for rec in self:
            if not rec.warranty_end_date:
                rec.warranty_days_left = 0
                rec.warranty_expiring_soon = False
                continue
            days = (rec.warranty_end_date - today).days
            rec.warranty_days_left = days
            rec.warranty_expiring_soon = 0 <= days <= window

    # ── Actions ───────────────────────────────────────────────────────────
    def action_view_warranty_claims(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Warranty Claims - %s") % self.asset_name,
            "res_model": "asset.warranty.claim",
            "view_mode": "list,form",
            "domain": [("asset_id", "=", self.id)],
            "context": {"default_asset_id": self.id},
        }

    def action_raise_warranty_claim(self):
        self.ensure_one()
        if self.warranty_status == "expired":
            raise UserError(_(
                "Warranty for %s expired on %s. Raise it as a repair instead, "
                "or update the warranty dates if it has been renewed."
            ) % (self.asset_name, self.warranty_end_date))
        return {
            "type": "ir.actions.act_window",
            "name": _("New Warranty Claim"),
            "res_model": "asset.warranty.claim",
            "view_mode": "form",
            "target": "new",
            "context": {"default_asset_id": self.id},
        }

    @api.model
    def cron_warranty_expiry_alert(self):
        """Post a chatter alert on assets whose warranty is expiring.

        Replaces asset_management.cron_check_warranty_expiry(), which filters
        on a warranty_status value that does not exist and so never matches.
        Disable that one in Settings > Technical > Scheduled Actions to avoid
        two crons doing overlapping work.
        """
        today = fields.Date.today()
        window = self._warranty_alert_days()
        assets = self.sudo().search([
            ("warranty_end_date", "!=", False),
            ("warranty_end_date", ">=", today),
            ("warranty_end_date", "<=", today + timedelta(days=window)),
            ("state", "!=", "scrapped"),
        ])
        for asset in assets:
            days = (asset.warranty_end_date - today).days
            asset.message_post(
                body=_("Warranty expires in %(days)s day(s), on %(date)s. "
                       "Provider: %(provider)s") % {
                    "days": days,
                    "date": asset.warranty_end_date,
                    "provider": asset.warranty_provider_id.name or _("not set"),
                },
                subject=_("Warranty Expiry Alert"),
                message_type="notification",
            )
        return True


class AssetWarrantyClaim(models.Model):
    _name = "asset.warranty.claim"
    _description = "Asset Warranty Claim"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "claim_date desc, id desc"

    name = fields.Char(
        string="Claim Reference", required=True, copy=False, readonly=True,
        default=lambda self: _("New"))

    asset_id = fields.Many2one(
        "asset.asset", string="Asset", required=True, ondelete="cascade",
        index=True, tracking=True)
    asset_code = fields.Char(related="asset_id.asset_code", store=True,
                             string="Asset Code")
    serial_number = fields.Char(related="asset_id.serial_number", store=True)
    employee_id = fields.Many2one(
        related="asset_id.assigned_employee_id", store=True, string="User")

    claim_date = fields.Date(
        required=True, default=fields.Date.context_today, tracking=True)
    issue_type = fields.Selection(
        [("keyboard", "Keyboard"),
         ("battery", "Battery"),
         ("screen", "Screen / Display"),
         ("storage", "Storage / Disk"),
         ("motherboard", "Motherboard"),
         ("power", "Power / Adapter"),
         ("other", "Other")],
        required=True, tracking=True)
    description = fields.Text(string="Issue Description", required=True)

    # Snapshotted from the asset at claim time. The asset's own provider /
    # contract number can legitimately change later (renewal, transfer), and
    # a historical claim must keep showing what was actually quoted to the
    # provider on the day it was raised.
    provider_id = fields.Many2one(
        "res.partner", string="Provider", tracking=True)
    contract_no = fields.Char(string="Contract / Reference No")
    provider_ticket_no = fields.Char(
        string="Provider Ticket No", tracking=True,
        help="Reference returned by the provider, for follow-up.")

    state = fields.Selection(
        [("draft", "Draft"),
         ("submitted", "Submitted"),
         ("in_progress", "In Progress"),
         ("approved", "Approved"),
         ("rejected", "Rejected"),
         ("completed", "Completed")],
        default="draft", required=True, tracking=True, index=True)

    resolution = fields.Selection(
        [("repaired", "Repaired"),
         ("replaced_part", "Part Replaced"),
         ("replaced_unit", "Unit Replaced"),
         ("no_fault", "No Fault Found"),
         ("not_covered", "Not Covered")],
        tracking=True)
    resolution_notes = fields.Text()
    replaced_part = fields.Char(
        help="What was actually replaced, e.g. 'Keyboard assembly', "
             "'57Wh battery'.")

    submitted_date = fields.Date(readonly=True, tracking=True)
    completed_date = fields.Date(readonly=True, tracking=True)
    turnaround_days = fields.Integer(
        compute="_compute_turnaround_days", store=True,
        string="Turnaround (days)",
        help="Submitted to completed. Useful for holding a provider to an SLA.")

    cost = fields.Monetary(
        currency_field="currency_id",
        help="Out-of-pocket cost. Should normally be zero under warranty - a "
             "non-zero value here usually means part of the claim was "
             "rejected as not covered.")
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id)

    attachment_file = fields.Binary(string="Document")
    attachment_filename = fields.Char()

    warranty_valid_on_claim = fields.Boolean(
        compute="_compute_warranty_valid", store=True,
        string="In Warranty",
        help="Whether the asset's warranty covered the claim date.")

    @api.depends("submitted_date", "completed_date")
    def _compute_turnaround_days(self):
        for rec in self:
            if rec.submitted_date and rec.completed_date:
                rec.turnaround_days = (rec.completed_date - rec.submitted_date).days
            else:
                rec.turnaround_days = 0

    @api.depends("claim_date", "asset_id.warranty_start_date",
                 "asset_id.warranty_end_date")
    def _compute_warranty_valid(self):
        for rec in self:
            asset = rec.asset_id
            if not rec.claim_date or not asset or not asset.warranty_end_date:
                rec.warranty_valid_on_claim = False
                continue
            starts_ok = (not asset.warranty_start_date
                         or asset.warranty_start_date <= rec.claim_date)
            rec.warranty_valid_on_claim = bool(
                starts_ok and rec.claim_date <= asset.warranty_end_date)

    @api.constrains("claim_date")
    def _check_claim_date(self):
        for rec in self:
            if rec.claim_date and rec.claim_date > fields.Date.today():
                raise ValidationError(_("Claim date cannot be in the future."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "asset.warranty.claim") or _("New")
        return super().create(vals_list)

    @api.onchange("asset_id")
    def _onchange_asset_id(self):
        if self.asset_id:
            self.provider_id = self.asset_id.warranty_provider_id
            self.contract_no = self.asset_id.warranty_contract_no

    # ── Workflow ──────────────────────────────────────────────────────────
    def action_submit(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only draft claims can be submitted."))
            rec.write({"state": "submitted",
                       "submitted_date": fields.Date.today()})
        return True

    def action_start_progress(self):
        return self.write({"state": "in_progress"})

    def action_approve(self):
        return self.write({"state": "approved"})

    def action_reject(self):
        return self.write({"state": "rejected",
                           "completed_date": fields.Date.today()})

    def action_complete(self):
        for rec in self:
            if not rec.resolution:
                raise UserError(_(
                    "Set a resolution before completing claim %s.") % rec.name)
            rec.write({"state": "completed",
                       "completed_date": fields.Date.today()})
        return True

    def action_reset_draft(self):
        return self.write({"state": "draft"})
