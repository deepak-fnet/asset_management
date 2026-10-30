/** @odoo-module **/

import { Component, onWillStart, onMounted, onWillUnmount, useState, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadJS } from "@web/core/assets";

export class ExpenseDashboard extends Component {

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");

        // Chart canvas refs
        this.barChartRef = useRef("barChart");
        this.categoryChartRef = useRef("categoryChart");
        this.statusChartRef = useRef("statusChart");
        this.deptChartRef = useRef("deptChart");

        // Chart instances (for cleanup)
        this._charts = {};

        this.state = useState({
            // Filter state
            filterType: 'date_range',
            dateRange: 'month',
            selectedMonth: new Date().getMonth() + 1,
            selectedYear: new Date().getFullYear(),
            dateFrom: '',
            dateTo: '',
            employeeId: false,

            // Data
            kpiData: {},
            chartData: {},
            categoryData: [],
            statusData: [],
            topEmployees: [],
            recentExpenses: [],
            deptBreakdown: [],
            availableYears: [],
            availableEmployees: [],
            loading: true,

            // ── Role flags ──
            isBillingUser: false,
            isManager: false,
            currentEmployeeId: false,

            // ── Advance Payment Data ──
            myAdvanceData: {},
            allAdvanceData: {},

            // ── Manager: Pending Approval ──
            pendingApprovalData: {},

            // UI
            activeTab: 'overview',
        });

        onWillStart(async () => {
            await loadJS("/web/static/lib/Chart/Chart.js");
            await this.loadDashboardData();
        });

        onMounted(() => {
            this.renderCharts();
        });

        onWillUnmount(() => {
            this.destroyCharts();
        });
    }

    // ══════════════════════════════════════════
    //  DATA LOADING
    // ══════════════════════════════════════════
    _buildFilters() {
        const f = {
            filter_type: this.state.filterType,
        };
        if (this.state.filterType === 'date_range') {
            f.date_range = this.state.dateRange;
        } else if (this.state.filterType === 'month') {
            f.month = this.state.selectedMonth;
            f.year = this.state.selectedYear;
        } else if (this.state.filterType === 'year') {
            f.year = this.state.selectedYear;
        } else if (this.state.filterType === 'custom') {
            f.date_from = this.state.dateFrom;
            f.date_to = this.state.dateTo;
        }
        if (this.state.employeeId) {
            f.employee_id = this.state.employeeId;
        }
        return f;
    }

    async loadDashboardData() {
        this.state.loading = true;
        try {
            const filters = this._buildFilters();
            const data = await this.orm.call(
                'hr.expense.dashboard',
                'get_dashboard_data',
                [filters]
            );
            Object.assign(this.state, {
                kpiData: data.kpi_data || {},
                chartData: data.chart_data || {},
                categoryData: data.expense_by_category || [],
                statusData: data.expense_by_status || [],
                topEmployees: data.top_employees || [],
                recentExpenses: data.recent_expenses || [],
                deptBreakdown: data.department_breakdown || [],
                availableYears: data.available_years || [],
                availableEmployees: data.available_employees || [],

                // Role flags
                isBillingUser: data.is_billing_user || false,
                isManager: data.is_manager || false,
                currentEmployeeId: data.current_employee_id || false,

                // Advance Payment data
                myAdvanceData: data.my_advance_data || {},
                allAdvanceData: data.all_advance_data || {},

                // Manager pending
                pendingApprovalData: data.pending_approval_data || {},
            });
        } catch (err) {
            console.error('Dashboard load error:', err);
        }
        this.state.loading = false;

        // Re-render charts after data update
        await new Promise(r => setTimeout(r, 50));
        this.renderCharts();
    }

    // ══════════════════════════════════════════
    //  FILTER HANDLERS
    // ══════════════════════════════════════════
    onFilterTypeChange(ev) {
        this.state.filterType = ev.target.value;
    }

    onDateRangeChange(ev) {
        this.state.dateRange = ev.target.value;
        this.loadDashboardData();
    }

    onMonthChange(ev) {
        this.state.selectedMonth = parseInt(ev.target.value);
    }

    onYearChange(ev) {
        this.state.selectedYear = parseInt(ev.target.value);
    }

    onDateFromChange(ev) {
        this.state.dateFrom = ev.target.value;
    }

    onDateToChange(ev) {
        this.state.dateTo = ev.target.value;
    }

    onEmployeeChange(ev) {
        this.state.employeeId = ev.target.value ? parseInt(ev.target.value) : false;
    }

    onApplyFilters() {
        this.loadDashboardData();
    }

    onResetFilters() {
        Object.assign(this.state, {
            filterType: 'date_range',
            dateRange: 'month',
            selectedMonth: new Date().getMonth() + 1,
            selectedYear: new Date().getFullYear(),
            dateFrom: '',
            dateTo: '',
            employeeId: false,
        });
        this.loadDashboardData();
    }

    // Tab switching
    onTabChange(tab) {
        this.state.activeTab = tab;
        if (tab === 'overview') {
            setTimeout(() => this.renderCharts(), 50);
        }
    }

    // ══════════════════════════════════════════
    //  CHART RENDERING  (Chart.js)
    // ══════════════════════════════════════════
    destroyCharts() {
        Object.values(this._charts).forEach(c => c && c.destroy());
        this._charts = {};
    }

    renderCharts() {
        this.destroyCharts();
        this._renderBarChart();
        this._renderCategoryChart();
        this._renderStatusChart();
        this._renderDeptChart();
    }

    _renderBarChart() {
        const el = this.barChartRef.el;
        if (!el) return;
        const ctx = el.getContext('2d');
        const cd = this.state.chartData;
        if (!cd.labels) return;

        this._charts.bar = new Chart(ctx, {
            type: cd.type || 'bar',
            data: {
                labels: cd.labels,
                datasets: (cd.datasets || []).map(ds => ({
                    label: ds.label,
                    data: ds.data,
                    backgroundColor: ds.color,
                    borderColor: ds.color,
                    borderWidth: 1,
                    borderRadius: 4,
                    barPercentage: 0.7,
                })),
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    title: { display: true, text: cd.title || '', font: { size: 14, weight: '600' } },
                    legend: { position: 'bottom' },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => `${ctx.dataset.label}: ₹${ctx.raw.toLocaleString('en-IN')}`,
                        },
                    },
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            callback: v => '₹' + v.toLocaleString('en-IN'),
                        },
                    },
                },
            },
        });
    }

    _renderCategoryChart() {
        const el = this.categoryChartRef.el;
        if (!el || !this.state.categoryData.length) return;
        const ctx = el.getContext('2d');

        this._charts.category = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: this.state.categoryData.map(c => c.name),
                datasets: [{
                    data: this.state.categoryData.map(c => c.value),
                    backgroundColor: this.state.categoryData.map(c => c.color),
                    borderWidth: 2,
                    borderColor: '#fff',
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '60%',
                plugins: {
                    title: { display: true, text: 'Expenses by Category', font: { size: 14, weight: '600' } },
                    legend: { position: 'bottom', labels: { boxWidth: 12, padding: 12 } },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => `${ctx.label}: ₹${ctx.raw.toLocaleString('en-IN')}`,
                        },
                    },
                },
            },
        });
    }

    _renderStatusChart() {
        const el = this.statusChartRef.el;
        if (!el || !this.state.statusData.length) return;
        const ctx = el.getContext('2d');

        this._charts.status = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: this.state.statusData.map(s => `${s.name} (${s.count || 0})`),
                datasets: [{
                    data: this.state.statusData.map(s => s.value),
                    backgroundColor: this.state.statusData.map(s => s.color),
                    borderWidth: 2,
                    borderColor: '#fff',
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '60%',
                plugins: {
                    title: { display: true, text: 'Expenses by Status', font: { size: 14, weight: '600' } },
                    legend: { position: 'bottom', labels: { boxWidth: 12, padding: 12 } },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => `${ctx.label}: ₹${ctx.raw.toLocaleString('en-IN')}`,
                        },
                    },
                },
            },
        });
    }

    _renderDeptChart() {
        const el = this.deptChartRef.el;
        if (!el || !this.state.deptBreakdown.length) return;
        const ctx = el.getContext('2d');

        this._charts.dept = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: this.state.deptBreakdown.map(d => d.name),
                datasets: [{
                    label: 'Amount',
                    data: this.state.deptBreakdown.map(d => d.amount),
                    backgroundColor: this.state.deptBreakdown.map(d => d.color),
                    borderRadius: 4,
                    barPercentage: 0.6,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: {
                    title: { display: true, text: 'Expenses by Department', font: { size: 14, weight: '600' } },
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => `₹${ctx.raw.toLocaleString('en-IN')}`,
                        },
                    },
                },
                scales: {
                    x: {
                        beginAtZero: true,
                        ticks: { callback: v => '₹' + v.toLocaleString('en-IN') },
                    },
                },
            },
        });
    }

    // ══════════════════════════════════════════
    //  FORMATTING HELPERS
    // ══════════════════════════════════════════
    formatCurrency(value) {
        return '₹' + (value || 0).toLocaleString('en-IN', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    formatNumber(value) {
        return (value || 0).toLocaleString();
    }

    get monthNames() {
        return [
            { value: 1, label: 'January' }, { value: 2, label: 'February' },
            { value: 3, label: 'March' }, { value: 4, label: 'April' },
            { value: 5, label: 'May' }, { value: 6, label: 'June' },
            { value: 7, label: 'July' }, { value: 8, label: 'August' },
            { value: 9, label: 'September' }, { value: 10, label: 'October' },
            { value: 11, label: 'November' }, { value: 12, label: 'December' },
        ];
    }

    get filterSummary() {
        const f = this.state;
        const monthLabel = this.monthNames.find(m => m.value === f.selectedMonth)?.label || '';
        switch (f.filterType) {
            case 'month': return `${monthLabel} ${f.selectedYear}`;
            case 'year': return `Year ${f.selectedYear}`;
            case 'custom': return `${f.dateFrom || '...'} → ${f.dateTo || '...'}`;
            default: {
                const labels = { week: 'This Week', month: 'This Month', quarter: 'This Quarter', year: 'This Year' };
                return labels[f.dateRange] || 'This Month';
            }
        }
    }

    // ══════════════════════════════════════════
    //  KPI CLICK-THROUGH ACTIONS — EXPENSES
    // ══════════════════════════════════════════
    async _getDomain(extraDomain = []) {
        const filters = this._buildFilters();
        const baseDomain = await this.orm.call(
            'hr.expense.dashboard', 'get_filter_domain', [filters]
        );
        return [...baseDomain, ...extraDomain];
    }

    async onTotalExpensesClick() {
        const domain = await this._getDomain();
        this.action.doAction({
            name: `All Expenses — ${this.filterSummary}`,
            type: 'ir.actions.act_window',
            res_model: 'hr.expense',
            views: [[false, 'list'], [false, 'form']],
            domain,
            target: 'current',
        });
    }

    async onPendingClick() {
        const domain = await this._getDomain([['state', 'in', ['submitted', 'approved']]]);
        this.action.doAction({
            name: `Pending Expenses — ${this.filterSummary}`,
            type: 'ir.actions.act_window',
            res_model: 'hr.expense',
            views: [[false, 'list'], [false, 'form']],
            domain,
            target: 'current',
        });
    }

    async onApprovedClick() {
        const domain = await this._getDomain([['state', '=', 'done']]);
        this.action.doAction({
            name: `Approved Expenses — ${this.filterSummary}`,
            type: 'ir.actions.act_window',
            res_model: 'hr.expense',
            views: [[false, 'list'], [false, 'form']],
            domain,
            target: 'current',
        });
    }

    async onRejectedClick() {
        const domain = await this._getDomain([['state', '=', 'refused']]);
        this.action.doAction({
            name: `Rejected Expenses — ${this.filterSummary}`,
            type: 'ir.actions.act_window',
            res_model: 'hr.expense',
            views: [[false, 'list'], [false, 'form']],
            domain,
            target: 'current',
        });
    }

    async onDraftClick() {
        const domain = await this._getDomain([['state', '=', 'draft']]);
        this.action.doAction({
            name: `Draft Expenses — ${this.filterSummary}`,
            type: 'ir.actions.act_window',
            res_model: 'hr.expense',
            views: [[false, 'list'], [false, 'form']],
            domain,
            target: 'current',
        });
    }

    // ══════════════════════════════════════════
    //  ADVANCE PAYMENT CLICK-THROUGH ACTIONS
    // ══════════════════════════════════════════

    // ── My Advance Payments (current user's employee) ──
    onMyAdvanceTotalClick() {
        this.action.doAction({
            name: 'My Advance Payments',
            type: 'ir.actions.act_window',
            res_model: 'advance.payment',
            views: [[false, 'list'], [false, 'form']],
            domain: [['line_ids.employee_id', '=', this.state.currentEmployeeId]],
            target: 'current',
        });
    }

    onMyAdvanceDraftClick() {
        this.action.doAction({
            name: 'My Draft Advances',
            type: 'ir.actions.act_window',
            res_model: 'advance.payment',
            views: [[false, 'list'], [false, 'form']],
            domain: [
                ['line_ids.employee_id', '=', this.state.currentEmployeeId],
                ['state', '=', 'draft'],
            ],
            target: 'current',
        });
    }

    onMyAdvanceRequestClick() {
        this.action.doAction({
            name: 'My Requested Advances',
            type: 'ir.actions.act_window',
            res_model: 'advance.payment',
            views: [[false, 'list'], [false, 'form']],
            domain: [
                ['line_ids.employee_id', '=', this.state.currentEmployeeId],
                ['state', '=', 'request'],
            ],
            target: 'current',
        });
    }

    onMyAdvanceApprovedClick() {
        this.action.doAction({
            name: 'My Approved Advances',
            type: 'ir.actions.act_window',
            res_model: 'advance.payment',
            views: [[false, 'list'], [false, 'form']],
            domain: [
                ['line_ids.employee_id', '=', this.state.currentEmployeeId],
                ['state', '=', 'approved'],
            ],
            target: 'current',
        });
    }

    onMyAdvanceConfirmedClick() {
        this.action.doAction({
            name: 'My Confirmed Advances',
            type: 'ir.actions.act_window',
            res_model: 'advance.payment',
            views: [[false, 'list'], [false, 'form']],
            domain: [
                ['line_ids.employee_id', '=', this.state.currentEmployeeId],
                ['state', '=', 'confirmed'],
            ],
            target: 'current',
        });
    }

    onMyTalliedClick() {
        this.action.doAction({
            name: 'My Adjusted Advances',
            type: 'ir.actions.act_window',
            res_model: 'advance.payment.line',
            views: [[false, 'list'], [false, 'form']],
            domain: [
                ['employee_id', '=', this.state.currentEmployeeId],
                ['expense_id', '!=', false],
            ],
            target: 'current',
        });
    }

    onMyNotTalliedClick() {
        this.action.doAction({
            name: 'My Not-Adjusted Advances',
            type: 'ir.actions.act_window',
            res_model: 'advance.payment.line',
            views: [[false, 'list'], [false, 'form']],
            domain: [
                ['employee_id', '=', this.state.currentEmployeeId],
                ['line_state', '=', 'confirmed'],
                ['expense_id', '=', false],
            ],
            target: 'current',
        });
    }

    // ── All Advance Payments (billing group — all employees) ──
    onAllAdvanceTotalClick() {
        this.action.doAction({
            name: 'All Advance Payments',
            type: 'ir.actions.act_window',
            res_model: 'advance.payment',
            views: [[false, 'list'], [false, 'form']],
            domain: [],
            target: 'current',
        });
    }

    onAllAdvanceDraftClick() {
        this.action.doAction({
            name: 'All Draft Advances',
            type: 'ir.actions.act_window',
            res_model: 'advance.payment',
            views: [[false, 'list'], [false, 'form']],
            domain: [['state', '=', 'draft']],
            target: 'current',
        });
    }

    onAllAdvanceRequestClick() {
        this.action.doAction({
            name: 'All Requested Advances',
            type: 'ir.actions.act_window',
            res_model: 'advance.payment',
            views: [[false, 'list'], [false, 'form']],
            domain: [['state', '=', 'request']],
            target: 'current',
        });
    }

    onAllAdvanceApprovedClick() {
        this.action.doAction({
            name: 'All Approved Advances',
            type: 'ir.actions.act_window',
            res_model: 'advance.payment',
            views: [[false, 'list'], [false, 'form']],
            domain: [['state', '=', 'approved']],
            target: 'current',
        });
    }

    onAllAdvanceConfirmedClick() {
        this.action.doAction({
            name: 'All Confirmed Advances',
            type: 'ir.actions.act_window',
            res_model: 'advance.payment',
            views: [[false, 'list'], [false, 'form']],
            domain: [['state', '=', 'confirmed']],
            target: 'current',
        });
    }

    onAllTalliedClick() {
        this.action.doAction({
            name: 'All Adjusted Advances',
            type: 'ir.actions.act_window',
            res_model: 'advance.payment.line',
            views: [[false, 'list'], [false, 'form']],
            domain: [['expense_id', '!=', false]],
            target: 'current',
        });
    }

    onAllNotTalliedClick() {
        this.action.doAction({
            name: 'All Not-Adjusted Advances',
            type: 'ir.actions.act_window',
            res_model: 'advance.payment.line',
            views: [[false, 'list'], [false, 'form']],
            domain: [
                ['line_state', '=', 'confirmed'],
                ['expense_id', '=', false],
            ],
            target: 'current',
        });
    }

    // ── Manager: Pending Approval ──
    onPendingApprovalClick() {
        this.action.doAction({
            name: 'Expenses Pending Approval',
            type: 'ir.actions.act_window',
            res_model: 'hr.expense',
            views: [[false, 'list'], [false, 'form']],
            domain: [['state', 'in', ['submitted', 'approved']]],
            target: 'current',
        });
    }

    onPendingSubmittedClick() {
        this.action.doAction({
            name: 'Submitted Expenses (Awaiting Approval)',
            type: 'ir.actions.act_window',
            res_model: 'hr.expense',
            views: [[false, 'list'], [false, 'form']],
            domain: [['state', '=', 'submitted']],
            target: 'current',
        });
    }

    onPendingApprovedClick() {
        this.action.doAction({
            name: 'Approved Expenses (Awaiting Posting)',
            type: 'ir.actions.act_window',
            res_model: 'hr.expense',
            views: [[false, 'list'], [false, 'form']],
            domain: [['state', '=', 'approved']],
            target: 'current',
        });
    }

    // Click on employee row
    async onEmployeeClick(empId) {
        const domain = await this._getDomain([['employee_id', '=', empId]]);
        this.action.doAction({
            name: 'Employee Expenses',
            type: 'ir.actions.act_window',
            res_model: 'hr.expense',
            views: [[false, 'list'], [false, 'form']],
            domain,
            target: 'current',
        });
    }

    // ══════════════════════════════════════════
    //  CREATE EXPENSE
    // ══════════════════════════════════════════
    onCreateExpense() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'hr.expense',
            views: [[false, 'form']],
            target: 'current',
        });
    }

    // Click on recent expense
    onExpenseClick(expenseId) {
        this.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'hr.expense',
            res_id: expenseId,
            views: [[false, 'form']],
            target: 'current',
        });
    }
}

ExpenseDashboard.template = "hr_expense_dashboard.Dashboard";

registry.category("actions").add("hr_expense_dashboard", ExpenseDashboard);