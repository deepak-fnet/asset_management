from odoo import models, fields, api, _
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta


class HrExpenseDashboard(models.TransientModel):
    _name = 'hr.expense.dashboard'
    _description = 'HR Expense Dashboard'

    # ══════════════════════════════════════════════════════════════════
    #  PUBLIC ENTRY POINT
    # ══════════════════════════════════════════════════════════════════
    @api.model
    def get_dashboard_data(self, filters=None):
        """Single RPC call that returns every block the dashboard needs."""
        filters = filters or {}
        domain = self._build_domain(filters)

        user = self.env.user
        employee = self.env['hr.employee'].search([('user_id', '=', user.id)], limit=1)
        is_billing = user.has_group('account.group_account_invoice')
        is_manager = (
            user.has_group('hr_expense.group_hr_expense_team_approver')
            or user.has_group('hr_expense.group_hr_expense_user')
            or user.has_group('hr_expense.group_hr_expense_manager')
        )

        return {
            'kpi_data':            self._get_kpi_data(domain),
            'chart_data':          self._get_chart_data(filters, domain),
            'expense_by_category': self._get_expense_by_category(domain),
            'expense_by_status':   self._get_expense_by_status(domain),
            'top_employees':       self._get_top_employees(domain),
            'recent_expenses':     self._get_recent_expenses(domain),
            'department_breakdown': self._get_department_breakdown(domain),
            'available_years':     self._get_available_years(),
            'available_employees': self._get_available_employees(),

            # Role flags
            'is_billing_user':     is_billing,
            'is_manager':          is_manager,
            'current_employee_id': employee.id or False,

            # Advance Payment blocks (state + tally split)
            'my_advance_data':     self._get_my_advance_data(employee.id),
            'all_advance_data':    self._get_all_advance_data() if is_billing else {},

            # Manager pending approvals
            'pending_approval_data': self._get_pending_approval_data() if is_manager else {},
        }

    @api.model
    def get_filter_domain(self, filters=None):
        """Used by KPI click-through actions to inherit the active filter."""
        return self._build_domain(filters or {})

    # ══════════════════════════════════════════════════════════════════
    #  DOMAIN BUILDER
    # ══════════════════════════════════════════════════════════════════
    def _build_domain(self, filters):
        domain = []
        ftype = filters.get('filter_type', 'date_range')
        today = fields.Date.today()

        if ftype == 'date_range':
            r = filters.get('date_range', 'month')
            if r == 'week':
                start = today - timedelta(days=today.weekday())
                end = start + timedelta(days=6)
            elif r == 'quarter':
                q = (today.month - 1) // 3
                start = date(today.year, q * 3 + 1, 1)
                end = (start + relativedelta(months=3)) - timedelta(days=1)
            elif r == 'year':
                start = date(today.year, 1, 1)
                end = date(today.year, 12, 31)
            else:  # month
                start = date(today.year, today.month, 1)
                end = (start + relativedelta(months=1)) - timedelta(days=1)
            domain += [('date', '>=', start), ('date', '<=', end)]

        elif ftype == 'month':
            month = int(filters.get('month') or today.month)
            year = int(filters.get('year') or today.year)
            start = date(year, month, 1)
            end = (start + relativedelta(months=1)) - timedelta(days=1)
            domain += [('date', '>=', start), ('date', '<=', end)]

        elif ftype == 'year':
            year = int(filters.get('year') or today.year)
            domain += [('date', '>=', date(year, 1, 1)),
                       ('date', '<=', date(year, 12, 31))]

        elif ftype == 'custom':
            if filters.get('date_from'):
                domain.append(('date', '>=', filters['date_from']))
            if filters.get('date_to'):
                domain.append(('date', '<=', filters['date_to']))

        if filters.get('employee_id'):
            domain.append(('employee_id', '=', int(filters['employee_id'])))

        return domain

    # ══════════════════════════════════════════════════════════════════
    #  KPI
    # ══════════════════════════════════════════════════════════════════
    def _get_kpi_data(self, domain):
        Expense = self.env['hr.expense']
        all_recs = Expense.search(domain)

        pending = all_recs.filtered(lambda e: e.state in ('submitted', 'approved'))
        approved = all_recs.filtered(lambda e: e.state == 'done')
        rejected = all_recs.filtered(lambda e: e.state == 'refused')

        # Approval rate = approved / (approved + rejected)
        decided = len(approved) + len(rejected)
        approval_rate = round(len(approved) * 100.0 / decided, 1) if decided else 0.0

        # Avg processing days (submit → done) using create_date as proxy
        diffs = []
        for e in approved:
            if e.create_date and e.write_date:
                diffs.append((e.write_date - e.create_date).days)
        avg_processing = round(sum(diffs) / len(diffs), 1) if diffs else 0

        return {
            'total_expenses':      sum(all_recs.mapped('total_amount_currency')),
            'total_count':         len(all_recs),
            'pending_approval':    sum(pending.mapped('total_amount_currency')),
            'pending_count':       len(pending),
            'approved_amount':     sum(approved.mapped('total_amount_currency')),
            'approved_count':      len(approved),
            'rejected_amount':     sum(rejected.mapped('total_amount_currency')),
            'rejected_count':      len(rejected),
            'avg_processing_time': avg_processing,
            'approval_rate':       approval_rate,
            'active_employees':    len(all_recs.mapped('employee_id')),
        }

    # ══════════════════════════════════════════════════════════════════
    #  MAIN BAR CHART (trend over the period)
    # ══════════════════════════════════════════════════════════════════
    def _get_chart_data(self, filters, domain):
        Expense = self.env['hr.expense']
        recs = Expense.search(domain)

        # Group by month within the filter
        buckets = {}
        for e in recs:
            if not e.date:
                continue
            key = e.date.strftime('%b %Y')
            buckets.setdefault(key, {'total': 0.0, 'approved': 0.0, 'pending': 0.0, 'rejected': 0.0})
            buckets[key]['total'] += e.total_amount_currency or 0.0
            if e.state == 'done':
                buckets[key]['approved'] += e.total_amount_currency or 0.0
            elif e.state in ('submitted', 'approved'):
                buckets[key]['pending'] += e.total_amount_currency or 0.0
            elif e.state == 'refused':
                buckets[key]['rejected'] += e.total_amount_currency or 0.0

        # Sort labels chronologically
        labels = sorted(buckets.keys(), key=lambda k: datetime.strptime(k, '%b %Y'))

        return {
            'type': 'bar',
            'title': 'Expense Trend',
            'labels': labels,
            'datasets': [
                {'label': 'Approved', 'data': [buckets[l]['approved'] for l in labels], 'color': '#10b981'},
                {'label': 'Pending',  'data': [buckets[l]['pending']  for l in labels], 'color': '#f59e0b'},
                {'label': 'Rejected', 'data': [buckets[l]['rejected'] for l in labels], 'color': '#ef4444'},
            ],
        }

    # ══════════════════════════════════════════════════════════════════
    #  CATEGORY DOUGHNUT
    # ══════════════════════════════════════════════════════════════════
    def _get_expense_by_category(self, domain):
        palette = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444',
                   '#8b5cf6', '#ec4899', '#06b6d4', '#84cc16',
                   '#f97316', '#6366f1']
        recs = self.env['hr.expense'].search(domain)
        buckets = {}
        for e in recs:
            name = e.product_id.display_name or 'Uncategorised'
            buckets.setdefault(name, 0.0)
            buckets[name] += e.total_amount_currency or 0.0

        return [
            {'name': k, 'value': round(v, 2), 'color': palette[i % len(palette)]}
            for i, (k, v) in enumerate(sorted(buckets.items(), key=lambda x: -x[1]))
        ]

    # ══════════════════════════════════════════════════════════════════
    #  STATUS DOUGHNUT
    # ══════════════════════════════════════════════════════════════════
    def _get_expense_by_status(self, domain):
        recs = self.env['hr.expense'].search(domain)
        cfg = [
            ('draft',     'Draft',     '#6b7280'),
            ('submitted', 'Submitted', '#f59e0b'),
            ('approved',  'Approved',  '#3b82f6'),
            ('done',      'Posted',    '#10b981'),
            ('refused',   'Refused',   '#ef4444'),
        ]
        out = []
        for state, label, color in cfg:
            sub = recs.filtered(lambda e: e.state == state)
            if not sub:
                continue
            out.append({
                'name':  label,
                'value': round(sum(sub.mapped('total_amount_currency')), 2),
                'count': len(sub),
                'color': color,
            })
        return out

    # ══════════════════════════════════════════════════════════════════
    #  TOP EMPLOYEES
    # ══════════════════════════════════════════════════════════════════
    def _get_top_employees(self, domain, limit=10):
        recs = self.env['hr.expense'].search(domain)
        buckets = {}
        for e in recs:
            emp = e.employee_id
            if not emp:
                continue
            b = buckets.setdefault(emp.id, {
                'id': emp.id, 'name': emp.name,
                'dept': emp.department_id.name or '-',
                'amount': 0.0, 'approved': 0.0, 'count': 0,
            })
            b['amount']   += e.total_amount_currency or 0.0
            b['count']    += 1
            if e.state == 'done':
                b['approved'] += e.total_amount_currency or 0.0

        ordered = sorted(buckets.values(), key=lambda x: -x['amount'])[:limit]
        max_amount = ordered[0]['amount'] if ordered else 0.0
        for i, row in enumerate(ordered, start=1):
            row['rank'] = i
            row['percent'] = round(row['amount'] * 100.0 / max_amount, 1) if max_amount else 0.0
        return ordered

    # ══════════════════════════════════════════════════════════════════
    #  RECENT EXPENSES
    # ══════════════════════════════════════════════════════════════════
    def _get_recent_expenses(self, domain, limit=15):
        recs = self.env['hr.expense'].search(domain, order='date desc, id desc', limit=limit)
        state_color = {
            'draft': '#6b7280', 'submitted': '#f59e0b', 'approved': '#3b82f6',
            'done': '#10b981', 'refused': '#ef4444',
        }
        state_label = dict(self.env['hr.expense']._fields['state'].selection)
        out = []
        for e in recs:
            out.append({
                'id':          e.id,
                'name':        e.name or '-',
                'employee':    e.employee_id.name or '-',
                'category':    e.product_id.display_name or '-',
                'date':        e.date and e.date.strftime('%Y-%m-%d') or '-',
                'amount':      e.total_amount_currency or 0.0,
                'state':       state_label.get(e.state, e.state),
                'state_color': state_color.get(e.state, '#6b7280'),
            })
        return out

    # ══════════════════════════════════════════════════════════════════
    #  DEPARTMENT BREAKDOWN
    # ══════════════════════════════════════════════════════════════════
    def _get_department_breakdown(self, domain):
        palette = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444',
                   '#8b5cf6', '#ec4899', '#06b6d4', '#84cc16']
        recs = self.env['hr.expense'].search(domain)
        buckets = {}
        for e in recs:
            name = e.department_id.name or 'Unassigned'
            buckets.setdefault(name, 0.0)
            buckets[name] += e.total_amount_currency or 0.0

        return [
            {'name': k, 'amount': round(v, 2), 'color': palette[i % len(palette)]}
            for i, (k, v) in enumerate(sorted(buckets.items(), key=lambda x: -x[1]))
        ]

    # ══════════════════════════════════════════════════════════════════
    #  FILTER OPTIONS
    # ══════════════════════════════════════════════════════════════════
    def _get_available_years(self):
        self.env.cr.execute("""
            SELECT DISTINCT EXTRACT(YEAR FROM date)::int AS y
            FROM hr_expense
            WHERE date IS NOT NULL
            ORDER BY y DESC
        """)
        years = [r[0] for r in self.env.cr.fetchall()]
        current = date.today().year
        if current not in years:
            years.insert(0, current)
        return years

    def _get_available_employees(self):
        emps = self.env['hr.employee'].search([], order='name')
        return [{'id': e.id, 'name': e.name} for e in emps]

    # ══════════════════════════════════════════════════════════════════
    #  ADVANCE PAYMENTS — MY (current employee)
    # ══════════════════════════════════════════════════════════════════
    def _get_my_advance_data(self, employee_id):
        if not employee_id:
            return {}

        Advance = self.env['advance.payment']
        Line = self.env['advance.payment.line']
        base = [('line_ids.employee_id', '=', employee_id)]

        # State counts (advance.payment level)
        states = ['draft', 'request', 'approved', 'confirmed']
        state_data = {}
        for s in states:
            recs = Advance.search(base + [('state', '=', s)])
            state_data[f'{s}_count'] = len(recs)
            state_data[f'{s}_amount'] = sum(recs.mapped('total_amount'))

        total_recs = Advance.search(base)

        # Tally counts (advance.payment.line level)
        tallied_lines = Line.search([
            ('employee_id', '=', employee_id),
            ('expense_id', '!=', False),
        ])
        not_tallied_lines = Line.search([
            ('employee_id', '=', employee_id),
            ('line_state', '=', 'confirmed'),
            ('expense_id', '=', False),
        ])

        return {
            'total_count':       len(total_recs),
            'total_amount':      sum(total_recs.mapped('total_amount')),
            **state_data,
            'tallied_count':     len(tallied_lines),
            'tallied_amount':    sum(tallied_lines.mapped('amount')),
            'not_tallied_count': len(not_tallied_lines),
            'not_tallied_amount': sum(not_tallied_lines.mapped('amount')),
        }

    # ══════════════════════════════════════════════════════════════════
    #  ADVANCE PAYMENTS — ALL (billing access only)
    # ══════════════════════════════════════════════════════════════════
    def _get_all_advance_data(self):
        Advance = self.env['advance.payment']
        Line = self.env['advance.payment.line']

        states = ['draft', 'request', 'approved', 'confirmed']
        state_data = {}
        for s in states:
            recs = Advance.search([('state', '=', s)])
            state_data[f'{s}_count'] = len(recs)
            state_data[f'{s}_amount'] = sum(recs.mapped('total_amount'))

        total_recs = Advance.search([])

        tallied_lines = Line.search([('expense_id', '!=', False)])
        not_tallied_lines = Line.search([
            ('line_state', '=', 'confirmed'),
            ('expense_id', '=', False),
        ])

        return {
            'total_count':       len(total_recs),
            'total_amount':      sum(total_recs.mapped('total_amount')),
            **state_data,
            'tallied_count':     len(tallied_lines),
            'tallied_amount':    sum(tallied_lines.mapped('amount')),
            'not_tallied_count': len(not_tallied_lines),
            'not_tallied_amount': sum(not_tallied_lines.mapped('amount')),
        }

    # ══════════════════════════════════════════════════════════════════
    #  MANAGER — PENDING FOR APPROVAL
    # ══════════════════════════════════════════════════════════════════
    def _get_pending_approval_data(self):
        Expense = self.env['hr.expense']
        submitted = Expense.search([('state', '=', 'submitted')])
        approved = Expense.search([('state', '=', 'approved')])

        return {
            'pending_count':     len(submitted) + len(approved),
            'pending_amount':    sum(submitted.mapped('total_amount_currency'))
                                 + sum(approved.mapped('total_amount_currency')),
            'submitted_count':   len(submitted),
            'submitted_amount':  sum(submitted.mapped('total_amount_currency')),
            'approved_count':    len(approved),
            'approved_amount':   sum(approved.mapped('total_amount_currency')),
        }