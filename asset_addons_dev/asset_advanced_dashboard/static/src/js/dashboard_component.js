/** @odoo-module **/

import { Component, onMounted, onPatched, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadJS } from "@web/core/assets";
import { rpc } from "@web/core/network/rpc";

// KPI tiles: the headline numbers, each drilling into its record list.
const KPI_TILES = [
    { key: "active_assets", label: "Active" },
    { key: "pending_approvals", label: "Pending approval" },
    { key: "assigned_assets", label: "Assigned to staff" },
    { key: "unassigned_assets", label: "Unassigned" },
    { key: "assets_under_transfer", label: "Under transfer" },
    { key: "verification_due", label: "Verification due" },
    { key: "removals_in_progress", label: "Removals open" },
    { key: "removed_assets", label: "Removed" },
];

// Risk tiles. `level` maps a count onto the reserved status palette; each tile
// always carries an icon and a label, so colour is never the only signal.
const RISK_TILES = [
    { key: "missing_tags", label: "Missing RFID tag", icon: "fa-tag", severity: "critical" },
    { key: "missing_serial", label: "Missing serial number", icon: "fa-barcode", severity: "serious" },
    { key: "missing_location", label: "No physical location", icon: "fa-map-marker", severity: "serious" },
    { key: "missing_category", label: "No category", icon: "fa-folder-open", severity: "warning" },
    { key: "amc_expiring", label: "AMC expiring in 30 days", icon: "fa-wrench", severity: "warning" },
    { key: "warranty_expiring", label: "Warranty expiring in 30 days", icon: "fa-shield", severity: "warning" },
    { key: "licence_expiring", label: "Licence expiring in 30 days", icon: "fa-certificate", severity: "warning" },
    { key: "old_assets", label: "Over 15 years old", icon: "fa-hourglass-end", severity: "critical" },
];

const CHART_REFS = ["stateChart", "deptChart", "categoryChart", "locationChart", "trendChart", "ageChart"];

export class AssetAdvancedDashboard extends Component {
    static template = "asset_advanced_dashboard.DashboardTemplate";
    static props = { "*": true };

    setup() {
        this.action = useService("action");
        this.notification = useService("notification");
        this.kpiTiles = KPI_TILES;
        this.riskTiles = RISK_TILES;

        this.rootRefs = {};
        for (const name of CHART_REFS) {
            this.rootRefs[name] = useRef(name);
        }

        this.state = useState({
            loading: true,
            kpis: {},
            charts: {},
            ageAnalysis: [],
            riskIndicators: {},
            filterOptions: {},
            filters: {
                department_id: null,
                plant_id: null,
                physical_location_id: null,
                state: null,
                date_from: null,
                date_to: null,
            },
        });

        this.charts = {};

        onWillStart(async () => {
            await loadJS("/web/static/lib/Chart/Chart.js");
            await this.loadFilterOptions();
            await this.loadDashboardData();
        });

        this.chartsDirty = false;
        onMounted(() => this.renderCharts());
        onPatched(() => {
            if (this.chartsDirty) {
                this.chartsDirty = false;
                this.renderCharts();
            }
        });
        onWillUnmount(() => this.destroyCharts());
    }

    // ------------------------------------------------------------------
    // Palette — read the validated tokens off the live DOM so the charts
    // follow the active light/dark theme instead of hard-coding hexes.
    // ------------------------------------------------------------------

    palette() {
        const el = this.rootRefs.stateChart.el || document.querySelector(".o_asset_dashboard");
        const css = getComputedStyle(el || document.body);
        const token = (name, fallback) => (css.getPropertyValue(name) || fallback).trim();
        return {
            surface: token("--ad-surface", "#fcfcfb"),
            ink: token("--ad-ink", "#0b0b0b"),
            ink2: token("--ad-ink-2", "#52514e"),
            muted: token("--ad-ink-muted", "#898781"),
            grid: token("--ad-grid", "#e1e0d9"),
            axis: token("--ad-axis", "#c3c2b7"),
            series: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => token(`--ad-s${i}`, "#2a78d6")),
            ordinal: [1, 2, 3, 4, 5].map((i) => token(`--ad-o${i}`, "#2a78d6")),
        };
    }

    /** [{label, value, slot}] - slot is the 1-based categorical token index. */
    get stateLegend() {
        const data = this.state.charts.by_state;
        if (!data || !data.labels) {
            return [];
        }
        return data.labels.map((label, i) => ({
            label,
            value: data.data[i],
            slot: (i % 8) + 1,
        }));
    }

    /** 1-based ordinal token index for an age band. */
    ordinalSlot(index) {
        return Math.min(index, 4) + 1;
    }

    riskLevel(risk) {
        const value = this.state.riskIndicators[risk.key] || 0;
        return value === 0 ? "good" : risk.severity;
    }

    hasData(key) {
        const chart = this.state.charts[key];
        return !!(chart && chart.data && chart.data.some((v) => v > 0));
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------

    async loadFilterOptions() {
        try {
            this.state.filterOptions = await rpc("/asset_advanced_dashboard/get_filter_options", {});
        } catch (error) {
            console.error("Failed to load filter options:", error);
            this.state.filterOptions = {};
        }
    }

    async loadDashboardData() {
        this.state.loading = true;
        try {
            const data = await rpc("/asset_advanced_dashboard/get_dashboard_data", {
                filters: this.state.filters,
            });
            this.state.kpis = data.kpis || {};
            this.state.charts = data.charts || {};
            this.state.ageAnalysis = data.age_analysis || [];
            this.state.riskIndicators = data.risk_indicators || {};
        } catch (error) {
            console.error("Failed to load dashboard data:", error);
            this.notification.add(
                "Could not load the dashboard data. If the asset modules were just updated, upgrade them and reload.",
                { type: "danger", sticky: true }
            );
        } finally {
            this.state.loading = false;
            this.chartsDirty = true;
        }
    }

    async refreshDashboard() {
        await this.loadDashboardData();
    }

    async applyFilters() {
        await this.loadDashboardData();
    }

    async clearFilters() {
        for (const key of Object.keys(this.state.filters)) {
            this.state.filters[key] = null;
        }
        for (const el of document.querySelectorAll(".o_asset_dashboard .ad-filters select, .o_asset_dashboard .ad-filters input")) {
            el.value = "";
        }
        await this.loadDashboardData();
    }

    onFilterChange(name, event) {
        const value = event.target.value;
        this.state.filters[name] = value === "" ? null : value;
    }

    printDashboard() {
        window.print();
    }

    // ------------------------------------------------------------------
    // Drill-down
    // ------------------------------------------------------------------

    async doAction(route, params) {
        try {
            const action = await rpc(route, { ...params, filters: this.state.filters });
            await this.action.doAction(action);
        } catch (error) {
            console.error("Drill-down failed:", error);
        }
    }

    openRecords(recordType) {
        return this.doAction("/asset_advanced_dashboard/open_records", { record_type: recordType });
    }

    openAgeRecords(bucketId) {
        return this.doAction("/asset_advanced_dashboard/open_age_records", { bucket_id: bucketId });
    }

    openCategoryRecords(bucketId, category) {
        return this.doAction("/asset_advanced_dashboard/open_category_records", {
            bucket_id: bucketId,
            category,
        });
    }

    // ------------------------------------------------------------------
    // Formatting
    // ------------------------------------------------------------------

    formatNumber(value) {
        return new Intl.NumberFormat("en-IN").format(value || 0);
    }

    formatCurrency(value) {
        const n = value || 0;
        const compact = Math.abs(n) >= 100000;
        return new Intl.NumberFormat("en-IN", {
            style: "currency",
            currency: "INR",
            notation: compact ? "compact" : "standard",
            maximumFractionDigits: compact ? 1 : 0,
        }).format(n);
    }

    // ------------------------------------------------------------------
    // Charts
    // ------------------------------------------------------------------

    destroyCharts() {
        Object.values(this.charts).forEach((chart) => chart?.destroy());
        this.charts = {};
    }

    /** Shared chrome: hairline solid grid, recessive axes, ink-token text. */
    baseOptions(p) {
        return {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 220 },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: p.ink,
                    titleColor: p.surface,
                    bodyColor: p.surface,
                    padding: 10,
                    cornerRadius: 6,
                    displayColors: true,
                    boxWidth: 8,
                    boxHeight: 8,
                    usePointStyle: true,
                },
            },
        };
    }

    scale(p, { horizontal = false } = {}) {
        const value = {
            beginAtZero: true,
            border: { display: false },
            grid: { color: p.grid, drawTicks: false, lineWidth: 1 },
            ticks: { color: p.muted, font: { size: 11 }, precision: 0, padding: 6 },
        };
        const category = {
            border: { color: p.axis, width: 1 },
            grid: { display: false },
            ticks: { color: p.ink2, font: { size: 11 }, autoSkip: false },
        };
        return horizontal ? { x: value, y: category } : { x: category, y: value };
    }

    renderCharts() {
        this.destroyCharts();
        if (typeof Chart === "undefined") {
            return;
        }
        const p = this.palette();
        this.renderStatusBar(p);
        this.renderMagnitudeBar("deptChart", this.state.charts.by_department, p);
        this.renderMagnitudeBar("categoryChart", this.state.charts.by_category, p);
        this.renderMagnitudeBar("locationChart", this.state.charts.by_location, p);
        this.renderTrend(p);
        this.renderAge(p);
    }

    /**
     * Status distribution as a single horizontal stacked bar (part-to-whole).
     * Categorical slots in fixed order; a 2px surface gap separates segments.
     */
    renderStatusBar(p) {
        const canvas = this.rootRefs.stateChart.el;
        const data = this.state.charts.by_state;
        if (!canvas || !data || !data.labels.length) {
            return;
        }

        const total = data.data.reduce((a, b) => a + b, 0) || 1;
        const datasets = data.labels.map((label, i) => {
            const color = p.series[i % p.series.length];
            return {
                label,
                data: [data.data[i]],
                backgroundColor: color,
                borderColor: p.surface,
                borderWidth: 2,          // reads as the 2px surface gap between segments
                borderRadius: 4,
                borderSkipped: false,
                barThickness: 24,
            };
        });

        this.charts.stateChart = new Chart(canvas.getContext("2d"), {
            type: "bar",
            data: { labels: [""], datasets },
            options: {
                ...this.baseOptions(p),
                indexAxis: "y",
                scales: {
                    // Pinning max to the total makes the bar span the full width,
                    // so segment widths read as true shares of the register.
                    x: { stacked: true, display: false, beginAtZero: true, max: total },
                    y: { stacked: true, display: false },
                },
                plugins: {
                    ...this.baseOptions(p).plugins,
                    tooltip: {
                        ...this.baseOptions(p).plugins.tooltip,
                        callbacks: {
                            title: (items) => items[0].dataset.label,
                            label: (item) => ` ${this.formatNumber(item.raw)} assets`
                                + ` (${Math.round((item.raw / total) * 100)}%)`,
                        },
                    },
                },
            },
        });
    }

    /**
     * Nominal categories (department / category / location) — one hue for every
     * bar. A value-ramp here would double-encode bar length as colour.
     */
    renderMagnitudeBar(refName, data, p) {
        const canvas = this.rootRefs[refName].el;
        if (!canvas || !data || !data.labels.length) {
            return;
        }
        this.charts[refName] = new Chart(canvas.getContext("2d"), {
            type: "bar",
            data: {
                labels: data.labels,
                datasets: [{
                    label: "Assets",
                    data: data.data,
                    backgroundColor: p.series[0],
                    borderRadius: 4,
                    borderSkipped: "start",
                    barThickness: data.labels.length > 8 ? 14 : 20,
                    maxBarThickness: 24,
                }],
            },
            options: {
                ...this.baseOptions(p),
                indexAxis: "y",
                layout: { padding: { right: 18 } },
                scales: this.scale(p, { horizontal: true }),
                plugins: {
                    ...this.baseOptions(p).plugins,
                    tooltip: {
                        ...this.baseOptions(p).plugins.tooltip,
                        callbacks: { label: (item) => ` ${this.formatNumber(item.raw)} assets` },
                    },
                },
            },
        });
    }

    /** Single series over time: 2px line, 10% area wash, no legend box. */
    renderTrend(p) {
        const canvas = this.rootRefs.trendChart.el;
        const data = this.state.charts.monthly_trend;
        if (!canvas || !data) {
            return;
        }
        const ctx = canvas.getContext("2d");
        const wash = ctx.createLinearGradient(0, 0, 0, canvas.height || 240);
        wash.addColorStop(0, this.withAlpha(p.series[0], 0.18));
        wash.addColorStop(1, this.withAlpha(p.series[0], 0));

        this.charts.trendChart = new Chart(ctx, {
            type: "line",
            data: {
                labels: data.labels,
                datasets: [{
                    label: "Assets acquired",
                    data: data.data,
                    borderColor: p.series[0],
                    backgroundColor: wash,
                    borderWidth: 2,
                    tension: 0,
                    fill: true,
                    pointRadius: 3,
                    pointBackgroundColor: p.series[0],
                    pointBorderColor: p.surface,
                    pointBorderWidth: 2,
                    pointHoverRadius: 5,
                    pointHoverBackgroundColor: p.series[0],
                    pointHoverBorderColor: p.surface,
                    pointHoverBorderWidth: 2,   // 2px surface ring on the hovered marker
                }],
            },
            options: {
                ...this.baseOptions(p),
                interaction: { mode: "index", intersect: false },
                scales: this.scale(p),
                plugins: {
                    ...this.baseOptions(p).plugins,
                    tooltip: {
                        ...this.baseOptions(p).plugins.tooltip,
                        callbacks: { label: (item) => ` ${this.formatNumber(item.raw)} acquired` },
                    },
                },
            },
        });
    }

    /** Ordered age bands — the ordinal blue ramp, monotone light to dark. */
    renderAge(p) {
        const canvas = this.rootRefs.ageChart.el;
        const buckets = this.state.ageAnalysis || [];
        if (!canvas || !buckets.length) {
            return;
        }
        this.charts.ageChart = new Chart(canvas.getContext("2d"), {
            type: "bar",
            data: {
                labels: buckets.map((b) => b.name),
                datasets: [{
                    label: "Assets",
                    data: buckets.map((b) => b.count),
                    backgroundColor: buckets.map((_b, i) => p.ordinal[Math.min(i, p.ordinal.length - 1)]),
                    borderRadius: 4,
                    borderSkipped: "start",
                    barThickness: 34,
                    maxBarThickness: 44,
                }],
            },
            options: {
                ...this.baseOptions(p),
                layout: { padding: { top: 18 } },
                scales: this.scale(p),
                onClick: (_evt, elements) => {
                    if (elements.length) {
                        this.openAgeRecords(buckets[elements[0].index].id);
                    }
                },
                plugins: {
                    ...this.baseOptions(p).plugins,
                    tooltip: {
                        ...this.baseOptions(p).plugins.tooltip,
                        callbacks: {
                            label: (item) => {
                                const b = buckets[item.dataIndex];
                                return [` ${this.formatNumber(b.count)} assets`,
                                        ` ${this.formatCurrency(b.total_value)}`];
                            },
                        },
                    },
                },
            },
        });
    }

    withAlpha(color, alpha) {
        const hex = (color || "").trim();
        if (!/^#[0-9a-f]{6}$/i.test(hex)) {
            return hex;
        }
        const n = parseInt(hex.slice(1), 16);
        return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
    }
}

registry.category("actions").add("asset_advanced_dashboard", AssetAdvancedDashboard);
