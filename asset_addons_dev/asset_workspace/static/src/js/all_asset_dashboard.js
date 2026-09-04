/** @odoo-module **/

import { Component, useState, useRef, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadJS } from "@web/core/assets";

const CHART_REFS = ["stateChart", "deptChart", "categoryChart", "locationChart", "trendChart", "ageChart"];

/**
 * All Asset Dashboard - filters, KPI cards, distribution charts, and an
 * age profile with drill-down, across every asset this suite tracks.
 *
 * Same concept as the separate asset_advanced_dashboard module's Analytics
 * Dashboard (filters row, KPI grid, distribution charts, age breakdown
 * table) - rebuilt fresh against THIS suite's own asset.asset instead of
 * reused directly: that module's dashboard is bound to asset.addition, an
 * unrelated model from a different, legacy "asset" module.
 */
class AllAssetDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.rootRefs = {};
        for (const key of CHART_REFS) {
            this.rootRefs[key] = useRef(key);
        }
        this.charts = {};
        this.chartsDirty = false;

        this.state = useState({
            loading: true,
            filters: {
                department_id: "", plant_id: "", location_id: "", state: "",
                date_from: "", date_to: "",
            },
            filterOptions: { departments: [], plants: [], locations: [], states: [] },
            kpis: {},
            charts: {},
            age_profile: [],
            risk: [],
        });

        onWillStart(async () => {
            await loadJS("/web/static/lib/Chart/Chart.js");
            await Promise.all([this.loadFilterOptions(), this.load()]);
        });

        onMounted(() => this.renderCharts());
        onWillUnmount(() => this.destroyCharts());
    }

    // ── Palette - read validated CSS tokens off the live DOM, same trick
    // the platform tiles use, so charts render correctly in both themes. ──
    get palette() {
        const el = this.rootRefs.stateChart.el
            || document.querySelector(".o_all_asset_dashboard");
        const style = el ? getComputedStyle(el) : null;
        const read = (name, fallback) => {
            const value = style && style.getPropertyValue(name).trim();
            return value || fallback;
        };
        return [
            read("--hub-chart-1", "#2c7be5"), read("--hub-chart-2", "#00d97e"),
            read("--hub-chart-3", "#f6c343"), read("--hub-chart-4", "#e63757"),
            read("--hub-chart-5", "#39afd1"), read("--hub-chart-6", "#95aac9"),
            read("--hub-chart-7", "#6b5eae"), read("--hub-chart-8", "#fd7e14"),
        ];
    }

    async loadFilterOptions() {
        try {
            this.state.filterOptions = await this.orm.call(
                "asset.hub.dashboard", "get_asset_analytics_filters", []
            );
        } catch (err) {
            console.error(err);
        }
    }

    async load() {
        this.state.loading = true;
        try {
            const data = await this.orm.call(
                "asset.hub.dashboard", "get_asset_analytics_data", [this.state.filters]
            );
            this.state.kpis = data.kpis || {};
            this.state.charts = data.charts || {};
            this.state.age_profile = data.age_profile || [];
            this.state.risk = data.risk || [];
            this.chartsDirty = true;
        } catch (err) {
            this.notification.add("Could not load the All Asset Dashboard.",
                                  { type: "danger" });
            console.error(err);
        } finally {
            this.state.loading = false;
            if (this.chartsDirty) {
                this.chartsDirty = false;
                // Wait a tick for the (now non-loading) DOM to mount the
                // canvases before Chart.js looks for them.
                setTimeout(() => this.renderCharts());
            }
        }
    }

    onFilterChange(key, ev) {
        this.state.filters[key] = ev.target.value;
    }

    async onApply() {
        await this.load();
    }

    onClear() {
        this.state.filters = {
            department_id: "", plant_id: "", location_id: "", state: "",
            date_from: "", date_to: "",
        };
        this.load();
    }

    async onRefresh() {
        await this.load();
    }

    // ── Charts ──────────────────────────────────────────────────────────
    destroyCharts() {
        Object.values(this.charts).forEach((chart) => chart && chart.destroy());
        this.charts = {};
    }

    hasData(key) {
        const chart = this.state.charts[key];
        return !!(chart && chart.data && chart.data.some((v) => v > 0));
    }

    renderCharts() {
        this.destroyCharts();
        const colours = this.palette;
        const c = this.state.charts;

        this._pie("stateChart", c.by_state, colours);
        this._bar("deptChart", c.by_department, colours[0]);
        this._bar("categoryChart", c.by_category, colours[1]);
        if (c.by_location) { this._bar("locationChart", c.by_location, colours[4]); }
        this._line("trendChart", c.monthly_trend, colours[0]);
        this._ageBar();
    }

    _canvas(key) {
        const el = this.rootRefs[key] && this.rootRefs[key].el;
        return el || null;
    }

    _pie(key, series, colours) {
        const canvas = this._canvas(key);
        if (!canvas || !series || !series.labels.length) { return; }
        this.charts[key] = new Chart(canvas, {
            type: "doughnut",
            data: {
                labels: series.labels,
                datasets: [{ data: series.data, backgroundColor: colours }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { position: "bottom" } },
            },
        });
    }

    _bar(key, series, colour) {
        const canvas = this._canvas(key);
        if (!canvas || !series || !series.labels.length) { return; }
        this.charts[key] = new Chart(canvas, {
            type: "bar",
            data: {
                labels: series.labels,
                datasets: [{ data: series.data, backgroundColor: colour }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: "y",
                plugins: { legend: { display: false } },
                scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
            },
        });
    }

    _line(key, series, colour) {
        const canvas = this._canvas(key);
        if (!canvas || !series) { return; }
        this.charts[key] = new Chart(canvas, {
            type: "line",
            data: {
                labels: series.labels,
                datasets: [{
                    data: series.data, borderColor: colour,
                    backgroundColor: colour, tension: 0.3, fill: false,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
            },
        });
    }

    _ageBar() {
        const canvas = this._canvas("ageChart");
        if (!canvas || !this.state.age_profile.length) { return; }
        const labels = this.state.age_profile.map((b) => b.name);
        const data = this.state.age_profile.map((b) => b.count);
        const shades = ["#c7d9f7", "#9fbdf0", "#6f96e6", "#4a76d6", "#2c58b8"];
        this.charts.ageChart = new Chart(canvas, {
            type: "bar",
            data: {
                labels,
                datasets: [{ data, backgroundColor: shades }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
                onClick: (_ev, elements) => {
                    if (elements.length) { this.openBucket(elements[0].index); }
                },
            },
        });
    }

    // ── Drill-down ──────────────────────────────────────────────────────
    async openBucket(index) {
        const bucket = this.state.age_profile[index];
        if (!bucket) { return; }
        const bounds = {
            1: [null, 3], 2: [3, 5], 3: [5, 10], 4: [10, 15], 5: [15, null],
        }[bucket.id] || [null, null];
        try {
            const action = await this.orm.call(
                "asset.hub.dashboard", "open_analytics_bucket",
                [this.state.filters, bounds[0], bounds[1]]
            );
            this.action.doAction(action);
        } catch (err) {
            this.notification.add("Could not open that age band.",
                                  { type: "danger" });
            console.error(err);
        }
    }

    async openAllAssets() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "All Assets",
            res_model: "asset.asset",
            views: [[false, "list"], [false, "form"]],
            domain: [],
        });
    }

    async openRisk(key) {
        try {
            const action = await this.orm.call(
                "asset.hub.dashboard", "open_risk_bucket", [this.state.filters, key]
            );
            this.action.doAction(action);
        } catch (err) {
            this.notification.add("Could not open that item.",
                                  { type: "danger" });
            console.error(err);
        }
    }
}

AllAssetDashboard.template = "asset_workspace.AllAssetDashboard";
registry.category("actions").add("all_asset_dashboard", AllAssetDashboard);

export default AllAssetDashboard;
