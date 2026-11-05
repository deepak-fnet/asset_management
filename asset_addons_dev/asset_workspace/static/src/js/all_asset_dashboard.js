/** @odoo-module **/

import { Component, useState, useRef, onWillStart, onMounted, onPatched, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadJS } from "@web/core/assets";

const CHART_REFS = ["stateChart", "deptChart", "locationChart", "trendChart", "ageChart"];

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
            employees: { rows: [], total_employees: 0, common: 0 },
            // What is typed in the searchable Department / Plant / Location boxes.
            filterText: { department_id: "", plant_id: "", location_id: "" },
        });

        onWillStart(async () => {
            await loadJS("/web/static/lib/Chart/Chart.js");
            await Promise.all([this.loadFilterOptions(), this.load()]);
        });

        onMounted(() => { this.renderCharts(); this.animateNumbers(); });
        // Draw the charts only once the new canvases are actually in the
        // DOM: after Apply the dashboard re-renders, and drawing earlier
        // (setTimeout) found no canvas and left every chart empty.
        onPatched(() => {
            if (this.chartsDirty) {
                this.chartsDirty = false;
                this.renderCharts();
            }
            this.animateNumbers();
        });
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
            read("--hub-chart-1", "#6366f1"), read("--hub-chart-2", "#10b981"),
            read("--hub-chart-3", "#f59e0b"), read("--hub-chart-4", "#f43f5e"),
            read("--hub-chart-5", "#0ea5e9"), read("--hub-chart-6", "#94a3b8"),
            read("--hub-chart-7", "#8b5cf6"), read("--hub-chart-8", "#f97316"),
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
            this.state.employees = data.employees || { rows: [], total_employees: 0, common: 0 };
            this.chartsDirty = true;
        } catch (err) {
            this.notification.add("Could not load the All Asset Dashboard.",
                                  { type: "danger" });
            console.error(err);
        } finally {
            this.state.loading = false;
        }
    }

    onFilterChange(key, ev) {
        this.state.filters[key] = ev.target.value;
    }

    // Searchable Many2one-like filter: type to search, pick a suggestion.
    optionsFor(key) {
        const o = this.state.filterOptions;
        return { department_id: o.departments, plant_id: o.plants, location_id: o.locations }[key] || [];
    }

    onFilterText(key, ev) {
        const text = ev.target.value.trim();
        this.state.filterText[key] = ev.target.value;
        const match = this.optionsFor(key).find((opt) => opt.name.toLowerCase() === text.toLowerCase());
        this.state.filters[key] = match ? String(match.id) : "";
    }

    clearFilterText(key) {
        this.state.filterText[key] = "";
        this.state.filters[key] = "";
    }

    async onApply() {
        await this.load();
    }

    onClear() {
        this.state.filters = {
            department_id: "", plant_id: "", location_id: "", state: "",
            date_from: "", date_to: "",
        };
        this.state.filterText = { department_id: "", plant_id: "", location_id: "" };
        this.load();
    }

    async onRefresh() {
        await this.load();
    }

    // ── Display helpers (template stays free of Math / String calls) ────
    get greeting() {
        const h = new Date().getHours();
        return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
    }

    money(v) {
        v = Number(v || 0);
        if (v >= 1e7) { return "₹" + (v / 1e7).toFixed(2) + " Cr"; }
        if (v >= 1e5) { return "₹" + (v / 1e5).toFixed(2) + " L"; }
        return "₹" + Math.round(v).toLocaleString("en-IN");
    }

    get qualityItems() {
        return (this.state.risk || []).filter((r) => r.key.startsWith("missing"));
    }

    // share of the register with tag, serial, location and category filled
    get quality() {
        const total = this.state.kpis.total_assets || 0;
        const items = this.qualityItems;
        if (!total || !items.length) { return 100; }
        const gaps = items.reduce((sum, r) => sum + Math.min(r.count, total), 0);
        return Math.max(0, Math.round(100 - (gaps * 100) / (items.length * total)));
    }

    get qualityTone() {
        const q = this.quality;
        return q >= 80 ? "ok" : q >= 50 ? "warn" : "bad";
    }

    // the single biggest gap - what to fix next
    get nextFix() {
        const worst = [...this.qualityItems].sort((a, b) => b.count - a.count)[0];
        return worst && worst.count ? worst : null;
    }

    get stateSegments() {
        const s = this.state.charts.by_state || { labels: [], data: [] };
        const total = s.data.reduce((a, b) => a + b, 0) || 1;
        const pal = this.palette;
        return s.labels.map((label, i) => ({
            label, count: s.data[i], pct: (s.data[i] * 100) / total, colour: pal[i % pal.length],
        }));
    }

    get leaderMax() {
        const rows = this.state.employees.rows || [];
        return Math.max(1, ...rows.map((r) => r.count), this.state.employees.common || 0);
    }

    get ageMax() {
        return Math.max(1, ...(this.state.age_profile || []).map((b) => b.count));
    }

    pctOf(n, max) {
        return Math.round((Number(n || 0) * 100) / (max || 1));
    }

    riskPct(r) {
        return this.pctOf(r.count, this.state.kpis.total_assets || 1);
    }

    riskIcon(key) {
        const map = {
            tag: "fa-tag", serial: "fa-barcode", location: "fa-map-marker", category: "fa-folder-open",
            warranty: "fa-shield", amc: "fa-wrench", licence: "fa-id-card", old: "fa-hourglass-end",
        };
        const hit = Object.keys(map).find((k) => key.includes(k));
        return "fa " + (hit ? map[hit] : "fa-exclamation");
    }

    initials(name) {
        return (name || "?").split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
    }

    avatarColour(i) {
        return ["#6366f1", "#0ea5e9", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6", "#14b8a6"][i % 7];
    }

    medal(i) {
        return ["🥇", "🥈", "🥉"][i] || "";
    }

    // count-up for elements carrying data-count, after each render
    animateNumbers() {
        const root = document.querySelector(".o_all_asset_dashboard");
        if (!root) { return; }
        root.querySelectorAll("[data-count]").forEach((el) => {
            const target = Number(el.dataset.count);
            if (!isFinite(target) || el.dataset.shown === String(target)) { return; }
            el.dataset.shown = String(target);
            const t0 = performance.now();
            const tick = (t) => {
                const k = Math.min((t - t0) / 800, 1);
                el.textContent = Math.round(target * (1 - Math.pow(1 - k, 3))).toLocaleString("en-IN");
                if (k < 1) { requestAnimationFrame(tick); }
            };
            requestAnimationFrame(tick);
        });
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

    _tip() {
        return {
            backgroundColor: "#0f172a", padding: 10, cornerRadius: 8, displayColors: false,
            titleFont: { weight: "700" }, bodyFont: { size: 12 },
        };
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
                datasets: [{ data: series.data, backgroundColor: colours, borderWidth: 3,
                             borderColor: "#fff", hoverOffset: 10, borderRadius: 6 }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "72%",
                animation: { animateRotate: true, duration: 900 },
                plugins: { legend: { display: false }, tooltip: this._tip() },
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
                datasets: [{ data: series.data, backgroundColor: colour + "cc", hoverBackgroundColor: colour,
                             borderRadius: 8, borderSkipped: false, barThickness: 14 }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: "y",
                plugins: { legend: { display: false }, tooltip: this._tip() },
                scales: {
                    x: { beginAtZero: true, ticks: { precision: 0, color: "#94a3b8" }, grid: { color: "#f1f5f9" }, border: { display: false } },
                    y: { ticks: { color: "#334155" }, grid: { display: false }, border: { display: false } },
                },
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
                    data: series.data, borderColor: colour, borderWidth: 3,
                    backgroundColor: (ctx) => {
                        const { chart } = ctx;
                        if (!chart.chartArea) { return colour + "22"; }
                        const g = chart.ctx.createLinearGradient(0, chart.chartArea.top, 0, chart.chartArea.bottom);
                        g.addColorStop(0, colour + "55");
                        g.addColorStop(1, colour + "00");
                        return g;
                    },
                    tension: 0.4, fill: true, pointRadius: 3, pointHoverRadius: 7,
                    pointBackgroundColor: "#fff", pointBorderColor: colour, pointBorderWidth: 2,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: "index", intersect: false },
                plugins: { legend: { display: false }, tooltip: this._tip() },
                scales: {
                    y: { beginAtZero: true, ticks: { precision: 0, color: "#94a3b8" }, grid: { color: "#f1f5f9" }, border: { display: false } },
                    x: { ticks: { color: "#64748b" }, grid: { display: false }, border: { display: false } },
                },
            },
        });
    }

    _ageBar() {
        const canvas = this._canvas("ageChart");
        if (!canvas || !this.state.age_profile.length) { return; }
        const labels = this.state.age_profile.map((b) => b.name);
        const data = this.state.age_profile.map((b) => b.count);
        const shades = ["#c7d2fe", "#a5b4fc", "#818cf8", "#6366f1", "#4338ca"];
        this.charts.ageChart = new Chart(canvas, {
            type: "bar",
            data: {
                labels,
                datasets: [{ data, backgroundColor: shades, borderRadius: 10, borderSkipped: false, maxBarThickness: 46 }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                onHover: (ev, els) => { ev.native.target.style.cursor = els.length ? "pointer" : "default"; },
                plugins: { legend: { display: false }, tooltip: this._tip() },
                scales: {
                    y: { beginAtZero: true, ticks: { precision: 0, color: "#94a3b8" }, grid: { color: "#f1f5f9" }, border: { display: false } },
                    x: { ticks: { color: "#64748b" }, grid: { display: false }, border: { display: false } },
                },
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

    async _open(method, args = []) {
        try {
            const action = await this.orm.call("asset.hub.dashboard", method, args);
            this.action.doAction(action);
        } catch (err) {
            this.notification.add("Could not open those assets.", { type: "danger" });
            console.error(err);
        }
    }

    openAllAssets() { return this._open("open_all_assets"); }
    openEmployeeRow(employeeId) {
        return this._open("open_employee_assets_of", [this.state.filters, employeeId]);
    }
    openAllEmployees() { return this._open("open_all_employee_assets", [this.state.filters]); }
    openCommonAssets() { return this._open("open_common_assets", [this.state.filters]); }
    openEmployeeAssets() { return this._open("open_employee_assets", [this.state.filters]); }

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
