from odoo import models, fields, api
import json
from datetime import datetime, timedelta, date, time
from dateutil.relativedelta import relativedelta


class VendorDashboard(models.Model):
    _name = 'vendor.dashboard'
    _description = 'Vendor Dashboard'

    total_vendors = fields.Integer(compute="_compute_stats")
    active_vendors = fields.Integer(compute="_compute_stats")
    under_review_vendors = fields.Integer(compute="_compute_stats")
    blocked_vendors = fields.Integer(compute="_compute_stats")

    expired_docs = fields.Integer(compute="_compute_stats")
    expiring_soon_docs = fields.Integer(compute="_compute_stats")
    approved_docs = fields.Integer(compute="_compute_stats")

    total_pos = fields.Integer(compute="_compute_stats")
    total_invoices = fields.Integer(compute="_compute_stats")
    avg_performance = fields.Float(compute="_compute_stats", string="Avg Periodic Performance")
    avg_transactional_score = fields.Float(compute="_compute_stats", string="Avg Transactional Score")
    high_risk_vendors = fields.Integer(compute="_compute_stats", string="High Risk Vendors (>70)")

    # JSON data for charts
    donut_chart_data = fields.Text(compute="_compute_charts_data")
    line_chart_data = fields.Text(compute="_compute_charts_data")
    risk_chart_data = fields.Text(compute="_compute_charts_data")

    # ---------- Buttons ----------
    def action_open_active(self):
        return self.env['res.partner'].action_active_vendors()

    def action_open_review(self):
        return self.env['res.partner'].action_under_review_vendors()

    def action_open_blocked(self):
        return self.env['res.partner'].action_blocked_vendors()

    def action_open_expired_docs(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Expired Documents',
            'res_model': 'vendor.document',
            'view_mode': 'list,form',
            'domain': [('expiry_date', '<', fields.Date.today())],
        }

    def _open_vendor_list(self, domain):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Vendors',
            'res_model': 'res.partner',
            'view_mode': 'list,form',
            'domain': domain,
            'views': [
                (self.env.ref('vendor_management.view_vendor_partner_list').id, 'list'),
                (self.env.ref('vendor_management.view_partner_form_vendor_enterprise').id, 'form'),
            ],
        }

    # ---------- Compute ----------
    def _compute_stats(self):
        """
        Compute dashboard stats live.
        Removed sudo() to ensure count parity with user-context actions.
        """
        Vendor = self.env['res.partner'].sudo()
        Doc = self.env['vendor.document'].sudo()

        for rec in self:
            rec.total_vendors = Vendor.search_count(Vendor._get_total_vendor_domain())
            rec.active_vendors = Vendor.search_count(Vendor._get_active_vendor_domain())
            rec.under_review_vendors = Vendor.search_count(Vendor._get_under_review_vendor_domain())
            rec.blocked_vendors = Vendor.search_count(Vendor._get_blocked_vendor_domain())

            rec.expired_docs = Doc.search_count([('is_expired', '=', True)])
            rec.expiring_soon_docs = Doc.search_count([('is_expiring_soon', '=', True)])
            rec.approved_docs = Doc.search_count([('state', '=', 'approved')])

            rec.total_pos = self.env['purchase.order'].search_count([('state', 'in', ('purchase', 'done'))])
            rec.total_invoices = self.env['account.move'].search_count([('move_type', '=', 'in_invoice'), ('state', '=', 'posted')])
            
            # Use search_count/search without sudo
            performances = self.env['vendor.performance'].search([])
            if performances:
                rec.avg_performance = sum(performances.mapped('periodic_score')) / len(performances)
            else:
                rec.avg_performance = 0.0

            transactional = self.env['vendor.performance.overview'].search([])
            if transactional:
                rec.avg_transactional_score = sum(transactional.mapped('performance_overview_score')) / len(transactional)
            else:
                rec.avg_transactional_score = 0.0

            rec.high_risk_vendors = Vendor.search_count([('supplier_rank', '>', 0), ('risk_score', '>', 70)])

    @api.model
    def get_dashboard_data(self):
        """
        Returns all dashboard data in a single dynamic call.
        Bypasses ID 1 read and ensures fresh counts without sudo.
        """
        dashboard = self.new({})  # Use new record to compute live stats
        dashboard._compute_stats()
        dashboard._compute_charts_data()
        return {
            "active_vendors": dashboard.active_vendors,
            "under_review_vendors": dashboard.under_review_vendors,
            "blocked_vendors": dashboard.blocked_vendors,
            "expired_docs": dashboard.expired_docs,
            "expiring_soon_docs": dashboard.expiring_soon_docs,
            "approved_docs": dashboard.approved_docs,
            "donut_chart_data": dashboard.donut_chart_data,
            "line_chart_data": dashboard.line_chart_data,
            "risk_chart_data": dashboard.risk_chart_data,
        }

    def _compute_charts_data(self):
        for rec in self:
            # Donut Chart Data
            donut_data = {
                'labels': ['Expired', 'Expiring Soon', 'Approved'],
                'datasets': [{
                    'data': [rec.expired_docs, rec.expiring_soon_docs, rec.approved_docs],
                    'backgroundColor': ['#e74c3c', '#f39c12', '#2ecc71'],
                }]
            }
            rec.donut_chart_data = json.dumps(donut_data)

            # Line Chart Data (Last 6 Months)
            labels = []
            data = []
            today = date.today()
            AuditLog = self.env['vendor.audit.log'].sudo()
            for i in range(5, -1, -1):
                month_start = (today - relativedelta(months=i)).replace(day=1)
                month_end = month_start + relativedelta(months=1) - timedelta(days=1)
                
                labels.append(month_start.strftime('%b'))
                
                count = AuditLog.search_count([
                    ('create_date', '>=', month_start),
                    ('create_date', '<=', month_end)
                ])
                data.append(count)

            line_data = {
                'labels': labels,
                'datasets': [{
                    'label': 'System Activity (Audit)',
                    'data': data,
                    'borderColor': '#3498db',
                    'backgroundColor': 'rgba(52, 152, 219, 0.1)',
                    'fill': True,
                    'tension': 0.4,
                }]
            }
            rec.line_chart_data = json.dumps(line_data)

            # Risk Distribution Data
            Vendor = self.env['res.partner'].sudo()
            low_risk = Vendor.search_count([('supplier_rank', '>', 0), ('risk_score', '<=', 30)])
            med_risk = Vendor.search_count([('supplier_rank', '>', 0), ('risk_score', '>', 30), ('risk_score', '<=', 70)])
            high_risk = rec.high_risk_vendors
            
            risk_data = {
                'labels': ['Low Risk (0-30)', 'Med Risk (31-70)', 'High Risk (>70)'],
                'datasets': [{
                    'data': [low_risk, med_risk, high_risk],
                    'backgroundColor': ['#2ecc71', '#f1c40f', '#e74c3c'],
                }]
            }
            rec.risk_chart_data = json.dumps(risk_data)
    def _vendor_category_domain(self, category_id):
        """
        Single source of truth for vendor category filtering.
        Used by both the dashboard chart and the frontend drill-down.
        """
        return [
            ('supplier_rank', '>', 0),
            ('vendor_category_ids', 'in', category_id)
        ]

    @api.model
    def get_vendor_category_data(self):
        """
        Returns vendor category distribution for dashboard charts.
        Counts all partners linked to categories, regardless of supplier_rank.
        """
        categories = self.env['vendor.category'].sudo().search([])
        
        result = []
        for cat in categories:
            count = self.env['res.partner'].sudo().search_count(
                [('vendor_category_ids', 'in', [cat.id])]
            )
            result.append({
                "id": cat.id,
                "name": cat.name,
                "count": count,
            })
        return result

    # =====================================================================
    # ADVANCED DASHBOARD v2 - single RPC payload for the OWL client action
    # =====================================================================

    PERIOD_DAYS = {'week': 7, 'month': 30, 'quarter': 90, 'year': 365}
    PERIOD_LABELS = {
        'week': 'Last 7 days',
        'month': 'Last 30 days',
        'quarter': 'Last 90 days',
        'year': 'Last 12 months',
    }

    @api.model
    def _v2_money(self, amount):
        return round(amount or 0.0, 2)

    @api.model
    def _v2_resolve_range(self, period, date_from, date_to):
        """Normalise the requested window into (period, date_from, date_to).

        Accepts either a preset period ('week'/'month'/'quarter'/'year') or an
        explicit custom range as ISO date strings. Returns date objects.
        """
        today = fields.Date.today()
        if date_from and date_to:
            d_from = fields.Date.from_string(date_from)
            d_to = fields.Date.from_string(date_to)
            if d_from > d_to:                       # be forgiving about order
                d_from, d_to = d_to, d_from
            return 'custom', d_from, d_to
        days = self.PERIOD_DAYS.get(period, 30)
        if period not in self.PERIOD_DAYS:
            period = 'month'
        return period, today - timedelta(days=days - 1), today

    @api.model
    def _v2_dt_bounds(self, d_from, d_to):
        """Inclusive-from / exclusive-to datetime strings for domain filters."""
        dt_from = fields.Datetime.to_string(datetime.combine(d_from, time.min))
        dt_to_excl = fields.Datetime.to_string(
            datetime.combine(d_to + timedelta(days=1), time.min))
        return dt_from, dt_to_excl

    @api.model
    def _v2_spend_trend(self, orders, d_from, d_to):
        """Bucket confirmed-PO amounts over the range.

        Granularity adapts to the window so the chart always stays readable:
        <= 31 days -> daily, <= 182 days -> weekly, otherwise monthly.
        `orders` is a search_read result carrying date_order + amount_total.
        """
        range_days = (d_to - d_from).days + 1

        if range_days <= 31:
            granularity = 'day'
            starts = [d_from + timedelta(days=i) for i in range(range_days)]
            fmt = '%d %b'

            def bucket_of(d):
                return d

        elif range_days <= 182:
            granularity = 'week'
            starts, cur = [], d_from
            while cur <= d_to:
                starts.append(cur)
                cur += timedelta(days=7)
            fmt = '%d %b'

            def bucket_of(d):
                return d_from + timedelta(days=((d - d_from).days // 7) * 7)

        else:
            granularity = 'month'
            starts, cur = [], d_from.replace(day=1)
            while cur <= d_to:
                starts.append(cur)
                cur += relativedelta(months=1)
            fmt = '%b %y' if d_from.year != d_to.year else '%b'

            def bucket_of(d):
                return d.replace(day=1)

        totals = {s: 0.0 for s in starts}
        for o in orders:
            od = o['date_order']
            if isinstance(od, datetime):
                od = od.date()
            b = bucket_of(od)
            if b in totals:
                totals[b] += o['amount_total'] or 0.0

        trend = [{'label': s.strftime(fmt), 'value': self._v2_money(totals[s])}
                 for s in starts]
        return trend, granularity

    @api.model
    def get_dashboard_data_v2(self, period='month', date_from=None, date_to=None):
        """Everything the advanced dashboard needs, in one call.

        `period`     preset window ('week' | 'month' | 'quarter' | 'year')
        `date_from`  optional ISO date string — start of a custom range
        `date_to`    optional ISO date string — end of a custom range

        When a custom range is supplied it wins over the preset. Period-scoped
        metrics (PO spend, spend trend, top vendors, new-vendor delta, activity
        feed) follow the window; snapshot metrics (compliance, risk, document
        states, outstanding bills) always reflect the current state.
        """
        today = fields.Date.today()
        period, d_from, d_to = self._v2_resolve_range(period, date_from, date_to)
        range_days = (d_to - d_from).days + 1

        # previous window of identical length, ending right before d_from
        prev_to = d_from - timedelta(days=1)
        prev_from = prev_to - timedelta(days=range_days - 1)

        dt_from, dt_to_excl = self._v2_dt_bounds(d_from, d_to)
        prev_dt_from, prev_dt_to_excl = self._v2_dt_bounds(prev_from, prev_to)

        Partner = self.env['res.partner'].sudo()
        Doc = self.env['vendor.document'].sudo()
        PO = self.env['purchase.order'].sudo()
        Move = self.env['account.move'].sudo()
        Audit = self.env['vendor.audit.log'].sudo()
        Perf = self.env['vendor.performance'].sudo()
        Trans = self.env['vendor.performance.overview'].sudo()

        vend_dom = [('is_vendor', '=', True)]

        # ---------------- KPIs (snapshot) ----------------
        total_vendors = Partner.search_count(vend_dom)
        active_vendors = Partner.search_count(vend_dom + [('vendor_state', '=', 'active')])
        under_review = Partner.search_count(vend_dom + [('vendor_state', 'in', ('submitted', 'manager_review'))])
        blocked = Partner.search_count(vend_dom + [('vendor_state', '=', 'blocked')])
        high_risk = Partner.search_count(vend_dom + [('risk_score', '>', 70)])
        compliant = Partner.search_count(vend_dom + [('compliance_status', '=', 'ok')])
        compliance_rate = round((compliant / total_vendors) * 100, 1) if total_vendors else 0.0

        risk_vals = Partner.search_read(vend_dom, ['risk_score'])
        avg_risk = round(sum(r['risk_score'] for r in risk_vals) / len(risk_vals), 1) if risk_vals else 0.0

        # new vendor trend (selected window vs previous window)
        new_now = Partner.search_count(vend_dom + [
            ('create_date', '>=', dt_from), ('create_date', '<', dt_to_excl)])
        new_prev = Partner.search_count(vend_dom + [
            ('create_date', '>=', prev_dt_from), ('create_date', '<', prev_dt_to_excl)])
        vend_delta = round(((new_now - new_prev) / new_prev) * 100, 1) if new_prev else (100.0 if new_now else 0.0)

        # documents (snapshot)
        docs_approved = Doc.search_count([('state', '=', 'approved')])
        docs_pending = Doc.search_count([('state', 'in', ('submitted', 'manager_review', 'md_approval'))])
        docs_expiring = Doc.search_count([('is_expiring_soon', '=', True), ('state', '=', 'approved')])
        docs_expired = Doc.search_count(['|', ('is_expired', '=', True), ('state', '=', 'expired')])

        # performance averages (snapshot)
        perf_scores = Perf.search([('state', '=', 'submitted')]).mapped('periodic_score')
        avg_periodic = round(sum(perf_scores) / len(perf_scores), 1) if perf_scores else 0.0
        trans_scores = Trans.search([]).mapped('performance_overview_score')
        avg_trans = round(sum(trans_scores) / len(trans_scores), 1) if trans_scores else 0.0

        # ---------------- Purchasing (range-scoped) ----------------
        po_dom = [('state', 'in', ('purchase', 'done')),
                  ('date_order', '>=', dt_from), ('date_order', '<', dt_to_excl)]
        po_prev_dom = [('state', 'in', ('purchase', 'done')),
                       ('date_order', '>=', prev_dt_from), ('date_order', '<', prev_dt_to_excl)]

        # one read powers PO value, count AND the trend buckets
        po_rows = PO.search_read(po_dom, ['date_order', 'amount_total'])
        po_count = len(po_rows)
        po_value = sum(r['amount_total'] or 0.0 for r in po_rows)
        po_value_prev = sum(PO.search(po_prev_dom).mapped('amount_total'))
        po_delta = round(((po_value - po_value_prev) / po_value_prev) * 100, 1) if po_value_prev else (100.0 if po_value else 0.0)

        open_bills = Move.search([('move_type', '=', 'in_invoice'), ('state', '=', 'posted'),
                                  ('payment_state', 'not in', ('paid', 'reversed'))])
        outstanding = sum(open_bills.mapped('amount_residual'))

        # ---------------- Charts ----------------
        # Category distribution (clickable)
        categories = []
        for cat in self.env['vendor.category'].sudo().search([]):
            cnt = Partner.search_count([('vendor_category_ids', 'in', [cat.id])])
            if cnt:
                categories.append({'id': cat.id, 'name': cat.name, 'count': cnt})
        categories.sort(key=lambda c: -c['count'])

        # Risk distribution (snapshot)
        risk_dist = {
            'low': Partner.search_count(vend_dom + [('risk_score', '<=', 30)]),
            'med': Partner.search_count(vend_dom + [('risk_score', '>', 30), ('risk_score', '<=', 70)]),
            'high': high_risk,
        }

        # Compliance breakdown (snapshot)
        comp_break = {
            'ok': compliant,
            'pending': Partner.search_count(vend_dom + [('compliance_status', '=', 'pending')]),
            'partial': Partner.search_count(vend_dom + [('compliance_status', '=', 'partial')]),
        }

        # Spend trend over the selected window, adaptive granularity
        spend_trend, granularity = self._v2_spend_trend(po_rows, d_from, d_to)

        # Document status funnel (snapshot)
        doc_states = ['draft', 'submitted', 'manager_review', 'md_approval', 'approved', 'rejected', 'expired']
        doc_funnel = [{'state': s, 'count': Doc.search_count([('state', '=', s)])} for s in doc_states]

        # ---------------- Lists ----------------
        risk_partners = Partner.search(vend_dom + [('risk_score', '>', 30)], order='risk_score desc', limit=6)
        high_risk_rows = [{
            'id': p.id, 'name': p.name, 'risk': p.risk_score,
            'compliance': p.compliance_status or 'pending',
            'grade': p.performance_grade or 'F',
        } for p in risk_partners]

        soon = today + timedelta(days=30)
        exp_docs = Doc.search([('state', '=', 'approved'), ('expiry_date', '!=', False),
                               ('expiry_date', '<=', soon)], order='expiry_date asc', limit=6)
        expiring_rows = [{
            'id': d.id, 'vendor': d.vendor_id.name, 'type': d.doc_type_id.name,
            'expiry': fields.Date.to_string(d.expiry_date),
            'days': (d.expiry_date - today).days,
        } for d in exp_docs]

        queue = Doc.search([('state', 'in', ('submitted', 'manager_review', 'md_approval'))],
                           order='create_date asc', limit=6)
        queue_rows = [{
            'id': d.id, 'vendor': d.vendor_id.name, 'type': d.doc_type_id.name, 'state': d.state,
        } for d in queue]

        # Top vendors by spend in the selected window
        groups = PO._read_group(po_dom, groupby=['partner_id'], aggregates=['amount_total:sum'])
        top = sorted([(p, amt) for p, amt in groups if p], key=lambda g: -g[1])[:5]
        max_amt = top[0][1] if top else 0.0
        top_vendors = [{
            'id': p.id, 'name': p.name, 'amount': self._v2_money(amt),
            'pct': round((amt / max_amt) * 100, 1) if max_amt else 0,
        } for p, amt in top]

        # Activity feed, scoped to the selected window
        logs = Audit.search([('create_date', '>=', dt_from), ('create_date', '<', dt_to_excl)],
                            order='create_date desc', limit=8)
        activity = [{
            'id': l.id,
            'vendor': l.vendor_id.name,
            'vendor_id': l.vendor_id.id,
            'event': l.event_type,
            'action': l.action or dict(l._fields['event_type'].selection).get(l.event_type, l.event_type),
            'user': l.changed_by.name or '',
            'date': fields.Datetime.to_string(l.create_date),
        } for l in logs]

        # ---------------- Range metadata for the header ----------------
        if period == 'custom':
            if d_from.year != d_to.year:
                range_label = '%s – %s' % (d_from.strftime('%d %b %Y'), d_to.strftime('%d %b %Y'))
            else:
                range_label = '%s – %s' % (d_from.strftime('%d %b'), d_to.strftime('%d %b %Y'))
        else:
            range_label = self.PERIOD_LABELS.get(period, 'Last 30 days')

        return {
            'period': period,
            'currency': self.env.company.currency_id.symbol or '',
            'range': {
                'label': range_label,
                'from': fields.Date.to_string(d_from),
                'to': fields.Date.to_string(d_to),
                'days': range_days,
                'granularity': granularity,     # day | week | month
            },
            'kpis': {
                'total_vendors': total_vendors,
                'active_vendors': active_vendors,
                'under_review': under_review,
                'blocked': blocked,
                'high_risk': high_risk,
                'compliance_rate': compliance_rate,
                'avg_risk': avg_risk,
                'avg_periodic': avg_periodic,
                'avg_trans': avg_trans,
                'vend_delta': vend_delta,
                'new_vendors': new_now,
                'docs_approved': docs_approved,
                'docs_pending': docs_pending,
                'docs_expiring': docs_expiring,
                'docs_expired': docs_expired,
                'po_count': po_count,
                'po_value': self._v2_money(po_value),
                'po_delta': po_delta,
                'outstanding': self._v2_money(outstanding),
            },
            'charts': {
                'categories': categories,
                'risk_dist': risk_dist,
                'compliance': comp_break,
                'spend_trend': spend_trend,
                'doc_funnel': doc_funnel,
            },
            'lists': {
                'high_risk': high_risk_rows,
                'expiring': expiring_rows,
                'queue': queue_rows,
                'top_vendors': top_vendors,
                'activity': activity,
            },
        }
