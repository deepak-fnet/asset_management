# -*- coding: utf-8 -*-
"""Data endpoints for the asset analytics dashboard.

The payload carries numbers and labels only. Colour is chosen in the client from
validated CSS tokens, so the same data renders correctly in light and dark.
"""

from datetime import date, timedelta

from odoo import http
from odoo.http import request

# Asset workflow states in progression order, with their display labels.
ASSET_STATES = [
    ('draft', 'Draft'),
    ('submit', 'Submitted'),
    ('dept_approved', 'Dept Approved'),
    ('incharge_approved', 'FI Approved'),
    ('cfo_approved', 'CFO Approved'),
    ('done', 'Done'),
    ('removed', 'Removed'),
    ('cancel', 'Cancelled'),
]

# Age buckets in months: (id, label, low_exclusive, high_inclusive)
AGE_BUCKETS = [
    (1, '0-3 yrs', None, 36),
    (2, '3-5 yrs', 36, 60),
    (3, '5-10 yrs', 60, 120),
    (4, '10-15 yrs', 120, 180),
    (5, '15+ yrs', 180, None),
]

PENDING_STATES = ['submit', 'dept_approved', 'incharge_approved', 'cfo_approved']


class AssetAdvancedDashboardController(http.Controller):
    """Controller for Asset Advanced Dashboard API endpoints"""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_domain(self, filters):
        """Build an asset search domain from the dashboard filters."""
        domain = []
        for key, field in (
            ('department_id', 'department_id'),
            ('plant_id', 'plant_id'),
            ('physical_location_id', 'physical_location_id'),
        ):
            if filters.get(key):
                domain.append((field, '=', int(filters[key])))
        if filters.get('state'):
            domain.append(('state', '=', filters['state']))
        if filters.get('date_from'):
            domain.append(('acquisition_date', '>=', filters['date_from']))
        if filters.get('date_to'):
            domain.append(('acquisition_date', '<=', filters['date_to']))
        return domain

    def _age_domain(self, low, high):
        domain = []
        if low is not None:
            domain.append(('laptop_age_total_months', '>', low))
        if high is not None:
            domain.append(('laptop_age_total_months', '<=', high))
        return domain

    def _grouped(self, model, domain, field):
        """[(label, count)] for a many2one/selection grouping, biggest first."""
        rows = model._read_group(domain, [field], ['__count'])
        out = []
        for value, count in rows:
            if not value:
                label = 'Unassigned'
            elif hasattr(value, 'display_name'):
                label = value.display_name
            else:
                label = str(value)
            out.append((label, count))
        out.sort(key=lambda row: -row[1])
        return out

    @staticmethod
    def _series(pairs, limit=8):
        """Pack (label, count) pairs into a chart series, folding the tail.

        Categorical palettes stop at 8 slots, so anything past the limit is
        collapsed into a single "Other" bucket rather than given a new colour.
        """
        head, tail = pairs[:limit], pairs[limit:]
        labels = [label for label, _count in head]
        data = [count for _label, count in head]
        if tail:
            labels.append('Other')
            data.append(sum(count for _label, count in tail))
        return {'labels': labels, 'data': data}

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @http.route('/asset_advanced_dashboard/get_dashboard_data', type='jsonrpc', auth='user')
    def get_dashboard_data(self, filters=None):
        """Main endpoint to get all dashboard data at once"""
        filters = filters or {}
        return {
            'kpis': self._get_kpi_data(filters),
            'charts': self._get_chart_data(filters),
            'age_analysis': self._get_age_analysis(filters),
            'risk_indicators': self._get_risk_indicators(filters),
        }

    @http.route('/asset_advanced_dashboard/get_filter_options', type='jsonrpc', auth='user')
    def get_filter_options(self):
        """Get available filter options for dropdowns"""
        def options(model):
            return [{'id': rec.id, 'name': rec.display_name}
                    for rec in request.env[model].sudo().search([])]

        return {
            'departments': options('hr.department'),
            'plants': options('plant.master'),
            'locations': options('physical.location'),
            'states': [{'value': code, 'label': label} for code, label in ASSET_STATES],
        }

    # ------------------------------------------------------------------
    # KPIs
    # ------------------------------------------------------------------

    def _get_kpi_data(self, filters):
        Asset = request.env['asset.addition'].sudo()
        Transfer = request.env['asset.internal.transfer'].sudo()
        Removal = request.env['asset.removal'].sudo()
        Verification = request.env['physical.verification'].sudo()

        domain = self._build_domain(filters)
        today = date.today()
        soon = today + timedelta(days=30)

        def count(extra):
            return Asset.search_count(domain + extra)

        def expiring(flag, end_field):
            return count([(flag, '=', True), (end_field, '<=', soon), (end_field, '>=', today)])

        live = domain + [('state', '!=', 'removed')]
        total_value = sum(Asset.search(live).mapped('value'))

        return {
            'total_assets': Asset.search_count(live),
            'active_assets': count([('state', '=', 'done')]),
            'removed_assets': count([('state', '=', 'removed')]),
            'assets_under_transfer': Transfer.search_count([('state', 'not in', ('done', 'cancel'))]),
            'removals_in_progress': Removal.search_count([('state', 'not in', ('done', 'cancel'))]),
            'verification_due': Verification.search_count([('state', 'not in', ('done', 'cancel'))]),
            'pending_approvals': count([('state', 'in', PENDING_STATES)]),
            'assigned_assets': count([('employee_id', '!=', False)]),
            'unassigned_assets': count([('employee_id', '=', False), ('state', '!=', 'removed')]),
            'missing_tags': count(['|', ('tag_number', '=', False), ('tag_number', '=', '')]),
            'total_value': total_value,
            'amc_assets': count([('is_amc', '=', True)]),
            'amc_expiring': expiring('is_amc', 'amc_end_date'),
            'warranty_assets': count([('is_warranty', '=', True)]),
            'warranty_expiring': expiring('is_warranty', 'warranty_end_date'),
            'licence_assets': count([('is_licence', '=', True)]),
            'licence_expiring': expiring('is_licence', 'licence_end_date'),
        }

    # ------------------------------------------------------------------
    # Charts
    # ------------------------------------------------------------------

    def _get_chart_data(self, filters):
        Asset = request.env['asset.addition'].sudo()
        domain = self._build_domain(filters)

        # Status: keep the workflow order, drop empty states.
        by_code = {code: 0 for code, _label in ASSET_STATES}
        raw = Asset._read_group(domain, ['state'], ['__count'])
        for value, cnt in raw:
            by_code[value] = cnt
        state_series = {'labels': [], 'data': []}
        for code, label in ASSET_STATES:
            if by_code.get(code):
                state_series['labels'].append(label)
                state_series['data'].append(by_code[code])

        live = domain + [('state', '!=', 'removed')]
        return {
            'by_state': state_series,
            'by_department': self._series(self._grouped(Asset, live, 'department_id')),
            'by_location': self._series(self._grouped(Asset, live, 'physical_location_id')),
            'by_plant': self._series(self._grouped(Asset, live, 'plant_id')),
            'by_category': self._series(self._grouped(Asset, live, 'category_id')),
            'monthly_trend': self._get_monthly_trend(Asset, domain),
        }

    def _get_monthly_trend(self, Asset, domain):
        """Assets acquired per month over the last 12 months."""
        today = date.today()
        first = (today.replace(day=1) - timedelta(days=365)).replace(day=1)
        rows = Asset._read_group(
            domain + [('acquisition_date', '>=', first)],
            ['acquisition_date:month'], ['__count'])
        counts = {month.strftime('%Y-%m'): cnt for month, cnt in rows if month}

        labels, data = [], []
        year, month = today.year, today.month
        months = []
        for _i in range(12):
            months.append((year, month))
            month -= 1
            if month == 0:
                month, year = 12, year - 1
        for year, month in reversed(months):
            key = date(year, month, 1)
            labels.append(key.strftime('%b %y'))
            data.append(counts.get(key.strftime('%Y-%m'), 0))
        return {'labels': labels, 'data': data}

    # ------------------------------------------------------------------
    # Age profile
    # ------------------------------------------------------------------

    def _get_age_analysis(self, filters):
        Asset = request.env['asset.addition'].sudo()
        base = self._build_domain(filters) + [('state', '!=', 'removed')]

        result = []
        for bucket_id, label, low, high in AGE_BUCKETS:
            domain = base + self._age_domain(low, high)
            assets = Asset.search(domain)
            breakdown = [
                {'name': name, 'count': cnt}
                for name, cnt in self._grouped(Asset, domain, 'category_id')[:4]
            ]
            result.append({
                'id': bucket_id,
                'name': label,
                'count': len(assets),
                'total_value': sum(assets.mapped('value')),
                'breakdown': breakdown,
            })
        return result

    # ------------------------------------------------------------------
    # Risk
    # ------------------------------------------------------------------

    def _get_risk_indicators(self, filters):
        Asset = request.env['asset.addition'].sudo()
        domain = self._build_domain(filters) + [('state', '!=', 'removed')]
        today = date.today()
        soon = today + timedelta(days=30)

        def count(extra):
            return Asset.search_count(domain + extra)

        def expiring(flag, end_field):
            return count([(flag, '=', True), (end_field, '<=', soon), (end_field, '>=', today)])

        return {
            'missing_tags': count(['|', ('tag_number', '=', False), ('tag_number', '=', '')]),
            'missing_serial': count(['|', ('serial_number', '=', False), ('serial_number', '=', '')]),
            'missing_location': count([('physical_location_id', '=', False)]),
            'missing_category': count([('category_id', '=', False)]),
            'amc_expiring': expiring('is_amc', 'amc_end_date'),
            'warranty_expiring': expiring('is_warranty', 'warranty_end_date'),
            'licence_expiring': expiring('is_licence', 'licence_end_date'),
            'old_assets': count([('laptop_age_total_months', '>', 180)]),
        }

    # ------------------------------------------------------------------
    # Drill-down
    # ------------------------------------------------------------------

    def _action(self, name, domain, res_model='asset.addition'):
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': res_model,
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': domain,
            'target': 'current',
            'context': {},
        }

    @http.route('/asset_advanced_dashboard/open_records', type='jsonrpc', auth='user')
    def open_records(self, record_type, filters=None):
        """Open the asset list behind a KPI or risk tile."""
        filters = filters or {}
        domain = self._build_domain(filters)
        live = domain + [('state', '!=', 'removed')]
        today = date.today()
        soon = today + timedelta(days=30)

        def expiring(flag, end_field):
            return live + [(flag, '=', True),
                           (end_field, '<=', soon.isoformat()),
                           (end_field, '>=', today.isoformat())]

        specs = {
            'total_assets': ("All Assets", live, 'asset.addition'),
            'active_assets': ("Active Assets", domain + [('state', '=', 'done')], 'asset.addition'),
            'removed_assets': ("Removed Assets", domain + [('state', '=', 'removed')], 'asset.addition'),
            'pending_approvals': ("Pending Approvals", domain + [('state', 'in', PENDING_STATES)], 'asset.addition'),
            'assigned_assets': ("Assigned Assets", live + [('employee_id', '!=', False)], 'asset.addition'),
            'unassigned_assets': ("Unassigned Assets", live + [('employee_id', '=', False)], 'asset.addition'),
            'missing_tags': ("Assets Without Tag", live + ['|', ('tag_number', '=', False), ('tag_number', '=', '')], 'asset.addition'),
            'missing_serial': ("Assets Without Serial", live + ['|', ('serial_number', '=', False), ('serial_number', '=', '')], 'asset.addition'),
            'missing_location': ("Assets Without Location", live + [('physical_location_id', '=', False)], 'asset.addition'),
            'missing_category': ("Assets Without Category", live + [('category_id', '=', False)], 'asset.addition'),
            'old_assets': ("Assets Over 15 Years", live + [('laptop_age_total_months', '>', 180)], 'asset.addition'),
            'amc_assets': ("Assets Under AMC", live + [('is_amc', '=', True)], 'asset.addition'),
            'warranty_assets': ("Assets Under Warranty", live + [('is_warranty', '=', True)], 'asset.addition'),
            'licence_assets': ("Assets With Licence", live + [('is_licence', '=', True)], 'asset.addition'),
            'amc_expiring': ("AMC Expiring Soon", expiring('is_amc', 'amc_end_date'), 'asset.addition'),
            'warranty_expiring': ("Warranty Expiring Soon", expiring('is_warranty', 'warranty_end_date'), 'asset.addition'),
            'licence_expiring': ("Licence Expiring Soon", expiring('is_licence', 'licence_end_date'), 'asset.addition'),
            'assets_under_transfer': ("Assets Under Transfer", [('state', 'not in', ('done', 'cancel'))], 'asset.internal.transfer'),
            'removals_in_progress': ("Removals In Progress", [('state', 'not in', ('done', 'cancel'))], 'asset.removal'),
            'verification_due': ("Verification Due", [('state', 'not in', ('done', 'cancel'))], 'physical.verification'),
        }
        name, domain, model = specs.get(record_type, specs['total_assets'])
        return self._action(name, domain, model)

    @http.route('/asset_advanced_dashboard/open_age_records', type='jsonrpc', auth='user')
    def open_age_records(self, bucket_id, filters=None):
        """Open assets in one age bucket."""
        filters = filters or {}
        buckets = {b[0]: b for b in AGE_BUCKETS}
        _id, label, low, high = buckets.get(bucket_id, AGE_BUCKETS[0])
        domain = (self._build_domain(filters)
                  + [('state', '!=', 'removed')]
                  + self._age_domain(low, high))
        return self._action("Assets - %s" % label, domain)

    @http.route('/asset_advanced_dashboard/open_category_records', type='jsonrpc', auth='user')
    def open_category_records(self, bucket_id, category, filters=None):
        """Open assets in one age bucket AND one asset category."""
        filters = filters or {}
        buckets = {b[0]: b for b in AGE_BUCKETS}
        _id, label, low, high = buckets.get(bucket_id, AGE_BUCKETS[0])
        domain = (self._build_domain(filters)
                  + [('state', '!=', 'removed')]
                  + self._age_domain(low, high))
        if category == 'Unassigned':
            domain.append(('category_id', '=', False))
        else:
            domain.append(('category_id.name', '=', category))
        return self._action("%s - %s" % (category, label), domain)
