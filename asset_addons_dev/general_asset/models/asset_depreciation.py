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

    salvage_value_percent = fields.Float(
        string="Salvage Value (%)",
        compute="_compute_salvage_value_percent_from_category",
        store=True, readonly=False,
        help="Estimated value once fully depreciated, as a percentage of "
             "purchase cost (e.g. 0.5 for 0.5%) - defaults from the "
             "asset's category (falling back to the original 0.5% "
             "spreadsheet convention if the category has none set), but "
             "can be overridden per asset. Same plain-number convention "
             "as Declining Rate (%): stored as 5 for 5%, not 0.05.")
    # Absolute amount, derived from the percentage above - every
    # depreciation calculation below already correctly treats this as a
    # currency amount, so keeping it as a stored compute (rather than
    # touching every one of those calculations) is the minimal change.
    salvage_value = fields.Float(
        string="Salvage Value", compute="_compute_salvage_value", store=True)
    useful_life_years = fields.Integer(
        compute="_compute_useful_life_years_from_category",
        store=True, readonly=False,
        help="Used only for the Straight Line Method. Defaults from the "
             "asset's category, but can be overridden per asset.")
    depreciation_method = fields.Selection(
        [("slm", "Straight Line Method"),
         ("dlm", "Declining Method")],
        compute="_compute_depreciation_method_from_category",
        store=True, readonly=False,
        help="DLM (Declining Method) also needs Declining Rate (%). "
             "Defaults from the asset's category, but can be overridden "
             "per asset.")
    depreciation_rate = fields.Float(
        string="Declining Rate (%)",
        compute="_compute_depreciation_rate_from_category",
        store=True, readonly=False,
        help="Used only for the Declining Method. Defaults from the "
             "asset's category, but can be overridden per asset.")

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

    @api.depends("purchase_cost", "salvage_value_percent")
    def _compute_salvage_value(self):
        for rec in self:
            rec.salvage_value = (rec.purchase_cost or 0.0) * rec.salvage_value_percent / 100

    # Two separate compute methods, not one shared by both fields: a
    # store=True/readonly=False field that shares a compute call with a
    # sibling output gets treated by the ORM as "already resolved" the
    # moment either one is written directly, which can silently skip
    # recomputing the other on a later, unrelated trigger - the exact bug
    # already hit and fixed for warranty_start_date/warranty_end_date
    # elsewhere in this codebase (see asset_management's asset_asset.py).
    # Splitting these means editing one manually never risks the other
    # going stale.
    @api.depends("category_id")
    def _compute_depreciation_method_from_category(self):
        for rec in self:
            rec.depreciation_method = rec.category_id.depreciation_method or "slm"

    @api.depends("category_id")
    def _compute_salvage_value_percent_from_category(self):
        for rec in self:
            rec.salvage_value_percent = rec.category_id.salvage_value_percent or 0.5

    @api.depends("category_id")
    def _compute_useful_life_years_from_category(self):
        for rec in self:
            rec.useful_life_years = rec.category_id.useful_life_years

    @api.depends("category_id")
    def _compute_depreciation_rate_from_category(self):
        for rec in self:
            rec.depreciation_rate = rec.category_id.depreciation_rate

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
        if not self.salvage_value_percent:
            raise UserError(_("Please enter Salvage Value (%)."))
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
        if not self.salvage_value_percent:
            raise UserError(_("Please enter Salvage Value (%)."))
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


class AssetCategoryDepreciation(models.Model):
    """Per-category depreciation defaults, pushed onto an asset when its
    category is set/changed (see AssetAssetDepreciation._compute_
    depreciation_method_from_category / _compute_salvage_value_percent_
    from_category above) - so a "Laptop" category can standardise on e.g.
    Declining Method + 5% salvage without every asset re-entering it."""
    _inherit = "asset.category"

    depreciation_method = fields.Selection(
        [("slm", "Straight Line Method"),
         ("dlm", "Declining Method")],
        default="slm", string="Depreciation Method")
    useful_life_years = fields.Integer(
        string="Useful Life Years",
        help="Used only for the Straight Line Method.")
    depreciation_rate = fields.Float(
        string="Declining Rate (%)",
        help="Used only for the Declining Method.")
    salvage_value_percent = fields.Float(
        string="Salvage Value (%)", default=0.5,
        help="Percentage of purchase cost, e.g. 0.5 for 0.5%.")
