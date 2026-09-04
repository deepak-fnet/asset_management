# -*- coding: utf-8 -*-
"""Financial depreciation for general assets.

Straight Line (SLM) and Declining Balance (DLM), both day-prorated against
the Indian financial year (Apr 1 - Mar 31) rather than the calendar year -
an asset bought partway through a year only depreciates for the days it
was actually owned in that year, same as the standalone it.asset module
this was ported from.

Reuses purchase_cost / purchase_date already on asset.asset (asset_management)
instead of duplicating them - the ported source model had its own
purchase_price/purchase_date because it was a separate, unrelated model.
"""

from datetime import date, timedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AssetDepreciationLine(models.Model):
    _name = "asset.depreciation.line"
    _description = "Asset Depreciation Line"
    _order = "id"

    asset_id = fields.Many2one("asset.asset", ondelete="cascade")
    year = fields.Char(string="Financial Year")
    days = fields.Integer()
    depreciation_amount = fields.Float()
    accumulated_value = fields.Float()
    remaining_value = fields.Float()


class AssetAssetDepreciation(models.Model):
    _inherit = "asset.asset"

    salvage_value = fields.Float(
        default=0.0,
        help="Estimated value once fully depreciated. Defaulted to 0.5% "
             "of purchase cost when the cost is entered, same as the "
             "original spreadsheet convention - override freely.")
    useful_life_years = fields.Integer()
    depreciation_method = fields.Selection(
        [("slm", "Straight Line Method"),
         ("dlm", "Declining Method")],
        default="slm",
        help="DLM (Declining Method) also needs Declining Rate (%).")
    depreciation_rate = fields.Float(
        string="Declining Rate (%)",
        help="Used only for the Declining Method.")

    current_value = fields.Float(readonly=True)
    accumulated_depreciation = fields.Float(readonly=True)
    depreciation_line_ids = fields.One2many(
        "asset.depreciation.line", "asset_id", string="Depreciation Schedule")
    is_depreciated = fields.Boolean(
        string="Depreciation Calculated", default=False, copy=False)
    fully_depreciated = fields.Boolean(compute="_compute_fully_depreciated")
    depreciation_percentage = fields.Float(
        string="Depreciation %", compute="_compute_depreciation_percentage")
    remaining_value = fields.Float(compute="_compute_remaining_value")

    @api.onchange("purchase_cost")
    def _onchange_purchase_cost_salvage(self):
        if self.purchase_cost:
            self.salvage_value = self.purchase_cost * 0.005

    def _compute_fully_depreciated(self):
        for rec in self:
            rec.fully_depreciated = (
                rec.is_depreciated and rec.current_value == rec.salvage_value)

    @api.depends("purchase_cost", "accumulated_depreciation")
    def _compute_depreciation_percentage(self):
        for rec in self:
            if rec.purchase_cost:
                rec.depreciation_percentage = (
                    rec.accumulated_depreciation / rec.purchase_cost) * 100
            else:
                rec.depreciation_percentage = 0.0

    @api.depends("current_value")
    def _compute_remaining_value(self):
        for rec in self:
            rec.remaining_value = rec.current_value

    @staticmethod
    def _get_fy_dates(dt):
        """India financial year (Apr 1 - Mar 31) containing `dt`."""
        if dt.month >= 4:
            return date(dt.year, 4, 1), date(dt.year + 1, 3, 31)
        return date(dt.year - 1, 4, 1), date(dt.year, 3, 31)

    def action_calculate_depreciation(self):
        for rec in self:
            rec.depreciation_line_ids.unlink()
            rec.is_depreciated = True
            if rec.depreciation_method == "slm":
                rec._calculate_slm()
            else:
                rec._calculate_dlm()

    def _calculate_slm(self):
        self.ensure_one()
        if not self.purchase_date:
            raise UserError(_("Please enter Purchase Date."))
        if not self.purchase_cost:
            raise UserError(_("Please enter Purchase Cost."))
        if not self.salvage_value:
            raise UserError(_("Please enter Salvage Value."))
        if not self.useful_life_years:
            raise UserError(_("Please enter Useful Life (Years)."))

        Line = self.env["asset.depreciation.line"]
        cost = self.purchase_cost
        salvage = self.salvage_value
        life = self.useful_life_years
        annual_dep = (cost - salvage) / life

        current_value = cost
        accumulated = 0.0
        purchase_date = self.purchase_date

        fy_end = date(purchase_date.year, 3, 31)
        if purchase_date > fy_end:
            fy_end = date(purchase_date.year + 1, 3, 31)
        days_first_year = (fy_end - purchase_date).days + 1

        dep = (annual_dep / 365) * days_first_year
        if current_value - dep < salvage:
            dep = current_value - salvage
        accumulated += dep
        current_value -= dep

        Line.create({
            "asset_id": self.id,
            "year": f"{fy_end.year - 1}-{fy_end.year}",
            "days": days_first_year,
            "depreciation_amount": dep,
            "accumulated_value": accumulated,
            "remaining_value": current_value,
        })

        for i in range(1, life + 1):
            if current_value <= salvage:
                break
            dep = annual_dep
            if current_value - dep < salvage:
                dep = current_value - salvage
            accumulated += dep
            current_value -= dep
            Line.create({
                "asset_id": self.id,
                "year": f"{fy_end.year + i - 1}-{fy_end.year + i}",
                "days": 365,
                "depreciation_amount": dep,
                "accumulated_value": accumulated,
                "remaining_value": current_value,
            })

        self.accumulated_depreciation = accumulated
        self._compute_current_value_slm()

    def _calculate_dlm(self):
        self.ensure_one()
        if not self.purchase_cost:
            raise UserError(_("Please enter Purchase Cost."))
        if not self.purchase_date:
            raise UserError(_("Please enter Purchase Date."))
        if not self.salvage_value:
            raise UserError(_("Please enter Salvage Value."))
        if not self.depreciation_rate:
            raise UserError(_("Please enter Declining Rate (%)."))

        Line = self.env["asset.depreciation.line"]
        rate = self.depreciation_rate / 100
        salvage = self.salvage_value
        current_value = self.purchase_cost
        accumulated = 0.0
        purchase_date = self.purchase_date

        fy_end = date(purchase_date.year, 3, 31)
        if purchase_date > fy_end:
            fy_end = date(purchase_date.year + 1, 3, 31)
        days_first_year = (fy_end - purchase_date).days + 1

        dep = (current_value * rate / 365) * days_first_year
        if current_value - dep < salvage:
            dep = current_value - salvage
        accumulated += dep
        current_value -= dep

        Line.create({
            "asset_id": self.id,
            "year": f"{fy_end.year - 1}-{fy_end.year}",
            "days": days_first_year,
            "depreciation_amount": dep,
            "accumulated_value": accumulated,
            "remaining_value": current_value,
        })

        i = 1
        while current_value > salvage:
            dep = current_value * rate
            if current_value - dep < salvage:
                dep = current_value - salvage
            accumulated += dep
            current_value -= dep
            Line.create({
                "asset_id": self.id,
                "year": f"{fy_end.year + i - 1}-{fy_end.year + i}",
                "days": 365,
                "depreciation_amount": dep,
                "accumulated_value": accumulated,
                "remaining_value": current_value,
            })
            i += 1

        self.accumulated_depreciation = accumulated
        self._compute_current_value_dlm()

    def _compute_current_value_slm(self):
        self.ensure_one()
        cost = self.purchase_cost
        salvage = self.salvage_value
        life = self.useful_life_years

        if not self.purchase_date:
            self.current_value = cost
            return

        today = date.today()
        annual_dep = (cost - salvage) / life
        daily_dep = annual_dep / 365

        accumulated = 0.0
        start = self.purchase_date
        while start <= today and accumulated < (cost - salvage):
            fy_start, fy_end = self._get_fy_dates(start)
            period_start = max(start, fy_start)
            period_end = min(today, fy_end)
            days = (period_end - period_start).days + 1
            dep = daily_dep * days
            if accumulated + dep > (cost - salvage):
                dep = (cost - salvage) - accumulated
            accumulated += dep
            start = fy_end + timedelta(days=1)

        self.current_value = cost - accumulated

    def _compute_current_value_dlm(self):
        self.ensure_one()
        if not self.purchase_date:
            self.current_value = self.purchase_cost
            return

        rate = self.depreciation_rate / 100
        salvage = self.salvage_value
        today = date.today()
        current_value = self.purchase_cost
        start = self.purchase_date

        while start <= today and current_value > salvage:
            fy_start, fy_end = self._get_fy_dates(start)
            period_start = max(start, fy_start)
            period_end = min(today, fy_end)
            days = (period_end - period_start).days + 1
            dep = (current_value * rate / 365) * days
            if current_value - dep < salvage:
                dep = current_value - salvage
            current_value -= dep
            start = fy_end + timedelta(days=1)

        self.current_value = current_value
