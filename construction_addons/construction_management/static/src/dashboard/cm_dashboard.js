/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Layout } from "@web/search/layout";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { loadBundle } from "@web/core/assets";
import { Component, useState, useRef, onWillStart, useEffect } from "@odoo/owl";

export class ConstructionDashboard extends Component {
    static template = "construction_management.Dashboard";
    static components = { Layout };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        // No control panel: the site-board header below already carries the title and the
        // refresh control, so the stock breadcrumb strip would just eat vertical space.
        this.display = { controlPanel: false };
        this.env.config.setDisplayName(this.props.action.name || _t("Dashboard"));

        this.chartRefs = {
            pnl: useRef("pnlChart"),
            stageBars: useRef("stageChart"),
            projectBars: useRef("projectChart"),
            timeline: useRef("timelineChart"),
            pnlByStage: useRef("pnlByStageChart"),
            costHeads: useRef("costHeadChart"),
        };
        this.fullscreenRef = useRef("fullscreenChart");
        this.charts = {};
        // Above this many bars the inline chart becomes unreadable, so it shows the biggest
        // ones and points you at the sub-project filter / full screen instead.
        this.INLINE_STAGE_LIMIT = 20;

        this.state = useState({
            mis: null,
            query: "",
            suggestions: [],
            showSuggestions: false,
            selectedMasterId: null,
            selectedLabel: "",
            detail: null,
            detailLoading: false,
            stageFilter: "",
            fullscreenChart: null,
        });

        onWillStart(async () => {
            await Promise.all([this.fetchMis(), loadBundle("web.chartjs_lib")]);
        });

        useEffect(
            () => {
                this.renderAllCharts();
                return () => this.destroyCharts();
            },
            () => [
                this.state.mis,
                this.state.detail,
                this.state.selectedMasterId,
                this.state.stageFilter,
                this.state.fullscreenChart,
            ]
        );
    }

    destroyCharts() {
        for (const key of Object.keys(this.charts)) {
            this.charts[key].destroy();
        }
        this.charts = {};
    }

    async fetchMis() {
        this.state.mis = await this.orm.call("cm.dashboard", "get_mis_data", []);
    }

    async refreshAll() {
        await this.fetchMis();
        if (this.state.selectedMasterId) {
            await this.fetchDetail(this.state.selectedMasterId);
        }
    }

    // ------------------------------------------------------------ search box
    async onSearchInput(ev) {
        const query = ev.target.value;
        this.state.query = query;
        if (!query || query.length < 1) {
            this.state.suggestions = [];
            this.state.showSuggestions = false;
            return;
        }
        const suggestions = await this.orm.call("cm.dashboard", "search_projects", [query]);
        this.state.suggestions = suggestions;
        this.state.showSuggestions = true;
    }

    onSearchFocus() {
        if (this.state.suggestions.length) {
            this.state.showSuggestions = true;
        }
    }

    onSearchBlur() {
        // small delay so a click on a suggestion registers before the list unmounts
        setTimeout(() => { this.state.showSuggestions = false; }, 150);
    }

    async pickProject(item) {
        this.state.query = "";
        this.state.suggestions = [];
        this.state.showSuggestions = false;
        const title = item.lead_name || item.name;
        this.state.selectedLabel = `${title} — ${item.partner}`;
        this.state.selectedMasterId = item.id;
        await this.fetchDetail(item.id);
    }

    clearProject() {
        this.state.selectedMasterId = null;
        this.state.selectedLabel = "";
        this.state.query = "";
        this.state.suggestions = [];
        this.state.detail = null;
    }

    async fetchDetail(masterId) {
        this.state.detailLoading = true;
        this.state.detail = await this.orm.call("cm.dashboard", "get_project_detail", [masterId]);
        this.state.detailLoading = false;
    }

    // --------------------------------------------------------------- charts
    /**
     * Chart.js rasterises to the canvas backing store at devicePixelRatio. On a 1x display
     * (and in some zoom/scaling combinations) that yields visibly soft text and bar edges,
     * so we force at least 2x and let the browser scale it down - the usual "retina canvas"
     * trick.
     *
     * Every chart now gets a full-width row (no more two-up columns, which crushed labels
     * into illegibility), so inline and full-screen typography can be close to the same
     * size - full-screen just gets a little more still, since it has even more room.
     */
    chartOptions(fullscreen = false) {
        const dpr = Math.max(2, Math.ceil(window.devicePixelRatio || 1));
        const tickFont = fullscreen ? 14 : 13;
        const legendFont = fullscreen ? 14 : 13;
        return {
            responsive: true,
            maintainAspectRatio: false,
            devicePixelRatio: dpr,
            interaction: { mode: "index", intersect: false },
            layout: { padding: { top: 8, right: 12, bottom: 4, left: 4 } },
            plugins: {
                legend: {
                    position: "bottom",
                    labels: {
                        usePointStyle: true,
                        boxWidth: 10,
                        padding: fullscreen ? 18 : 14,
                        color: "#17293a",
                        font: { size: legendFont, weight: "600" },
                    },
                },
                tooltip: {
                    titleFont: { size: fullscreen ? 15 : 13 },
                    bodyFont: { size: fullscreen ? 14 : 13 },
                    padding: fullscreen ? 12 : 10,
                    callbacks: {
                        label: (ctx) =>
                            `${ctx.dataset.label}: ${this.currency}${this.formatMoney(ctx.parsed.y ?? ctx.parsed)}`,
                    },
                },
            },
            scales: {
                y: {
                    ticks: {
                        callback: (v) => this.formatCompact(v),
                        color: "#3d4f5f",
                        font: { size: tickFont, weight: "600" },
                    },
                    grid: { color: "#e5e9ee" },
                    border: { color: "#dbe2e9" },
                },
                x: {
                    grid: { display: false },
                    border: { color: "#dbe2e9" },
                    ticks: {
                        color: "#17293a",
                        font: { size: tickFont, weight: "600" },
                        maxRotation: fullscreen ? 40 : 55,
                        minRotation: 0,
                        autoSkip: !fullscreen,
                    },
                },
            },
        };
    }

    get baseChartOptions() {
        return this.chartOptions(false);
    }

    /**
     * Options for the per-project P&amp;L-by-stage line chart specifically: values are a
     * cumulative % of budget (not rupees, so wildly different-sized projects stay on the
     * same axis - see _pnl_by_stage_position on the backend), so ticks/tooltip format as
     * a percentage, and a bold zero line marks the winning/losing boundary.
     */
    pnlByStageOptions(fullscreen) {
        const options = this.chartOptions(fullscreen);
        options.scales.y.grid = {
            color: (ctx) => (ctx.tick.value === 0 ? "#17293a" : "#e5e9ee"),
            lineWidth: (ctx) => (ctx.tick.value === 0 ? 2 : 1),
        };
        options.scales.y.ticks.callback = (v) => `${v}%`;
        options.plugins.tooltip.callbacks.label = (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y}%`;
        options.plugins.legend.labels.boxWidth = 8;
        options.plugins.legend.position = fullscreen ? "right" : "bottom";
        options.plugins.legend.labels.font.size = fullscreen ? 12 : 11;
        options.plugins.legend.maxHeight = fullscreen ? undefined : 90;
        return options;
    }

    /** Distinct, construction-themed colours for however many project lines there are -
     * cycles a base set, then rotates hue for anything beyond it so 10+ projects still
     * stay visually distinguishable rather than repeating identical colours. */
    projectColorPalette(count) {
        const base = ["#2f4a63", "#e2622b", "#1e8f5e", "#b4531f", "#6b4fa0", "#c0392b", "#0d7377", "#8e6b23"];
        const colors = [];
        for (let i = 0; i < count; i++) {
            if (i < base.length) {
                colors.push(base[i]);
            } else {
                const hue = ((i - base.length) * 47) % 360;
                colors.push(`hsl(${hue}, 62%, 42%)`);
            }
        }
        return colors;
    }

    /**
     * Stage bars, scoped to the picked sub-project. A project can carry hundreds of stages,
     * so the inline chart shows only the biggest few - the filter and the full-screen view
     * are how you get to the rest.
     */
    stageChartData(all = false) {
        const sc = this.state.detail && this.state.detail.stage_chart;
        if (!sc || !sc.labels.length) {
            return null;
        }
        const filter = this.state.stageFilter;
        let idx = sc.labels.map((_, i) => i);
        if (filter) {
            idx = idx.filter((i) => String(sc.subproject_ids[i]) === String(filter));
        }
        const total = idx.length;
        let truncated = false;
        if (!all && total > this.INLINE_STAGE_LIMIT) {
            idx = [...idx].sort((a, b) => sc.budget[b] - sc.budget[a]).slice(0, this.INLINE_STAGE_LIMIT);
            truncated = true;
        }
        return {
            labels: idx.map((i) => sc.labels[i]),
            budget: idx.map((i) => sc.budget[i]),
            purchased: idx.map((i) => sc.purchased[i]),
            billed: idx.map((i) => sc.billed[i]),
            shown: idx.length,
            total,
            truncated,
        };
    }

    get stageChartInfo() {
        return this.stageChartData(false) || { shown: 0, total: 0, truncated: false };
    }

    /** Width the full-screen bar chart needs so 200+ stages stay readable (then it scrolls). */
    get fullscreenChartWidth() {
        if (this.state.fullscreenChart !== "stageBars") {
            return "100%";
        }
        const d = this.stageChartData(true);
        const count = d ? d.labels.length : 0;
        return `${Math.max(100, count * 52)}px`;
    }

    _configFor(key, all = false) {
        const mis = this.state.mis;
        const detail = this.state.detail;
        const options = this.chartOptions(all);
        const bar = (labels, datasets) => ({ type: "bar", data: { labels, datasets }, options });
        const line = (labels, datasets) => ({ type: "line", data: { labels, datasets }, options });

        if (key === "projectBars" && mis && mis.charts.projects.labels.length) {
            const p = mis.charts.projects;
            return bar(p.labels, [
                { label: "Contract", data: p.contract, backgroundColor: "#2f4a63" },
                { label: "Cost", data: p.cost, backgroundColor: "#e2622b" },
                { label: "Billed", data: p.billed, backgroundColor: "#f6a609" },
            ]);
        }
        if (key === "pnlByStage" && mis && mis.charts.pnl_by_stage.labels.length) {
            const s = mis.charts.pnl_by_stage;
            const palette = this.projectColorPalette(s.series.length);
            return {
                type: "line",
                data: {
                    labels: s.labels,
                    datasets: s.series.map((proj, i) => ({
                        label: proj.name,
                        data: proj.data,
                        borderColor: palette[i],
                        backgroundColor: palette[i],
                        borderWidth: 2.5,
                        tension: 0.2,
                        pointRadius: 2,
                        pointHoverRadius: 5,
                        fill: false,
                        spanGaps: false,
                    })),
                },
                options: this.pnlByStageOptions(all),
            };
        }
        if (key === "timeline" && mis && mis.charts.timeline.labels.length) {
            const t = mis.charts.timeline;
            return line(t.labels, [
                { label: "Cumulative Cost", data: t.cost, borderColor: "#e2622b", backgroundColor: "rgba(226,98,43,.08)", borderWidth: 2, tension: 0.25, pointRadius: 3 },
                { label: "Cumulative Revenue", data: t.revenue, borderColor: "#f6a609", backgroundColor: "rgba(246,166,9,.08)", borderWidth: 2, tension: 0.25, pointRadius: 3 },
                { label: "Margin", data: t.margin, borderColor: "#1e8f5e", backgroundColor: "rgba(30,143,94,.12)", borderWidth: 2, borderDash: [5, 4], tension: 0.25, pointRadius: 3, fill: "origin" },
            ]);
        }
        if (key === "pnl" && detail && detail.trend.labels.length) {
            const t = detail.trend;
            return line(t.labels, [
                { label: "Cumulative Cost", data: t.cost, borderColor: "#e2622b", backgroundColor: "rgba(226,98,43,.08)", borderWidth: 2, tension: 0.25, pointRadius: 3 },
                { label: "Cumulative Revenue (Billed)", data: t.revenue, borderColor: "#f6a609", backgroundColor: "rgba(246,166,9,.08)", borderWidth: 2, tension: 0.25, pointRadius: 3 },
                { label: "Margin", data: t.margin, borderColor: "#1e8f5e", backgroundColor: "rgba(30,143,94,.12)", borderWidth: 2, borderDash: [5, 4], tension: 0.25, pointRadius: 3, fill: "origin" },
            ]);
        }
        if (key === "stageBars") {
            const d = this.stageChartData(all);
            if (!d || !d.labels.length) {
                return null;
            }
            return bar(d.labels, [
                { label: "Budget", data: d.budget, backgroundColor: "#2f4a63" },
                { label: "Purchased", data: d.purchased, backgroundColor: "#f6a609" },
                { label: "Billed (Actual Cost)", data: d.billed, backgroundColor: "#e2622b" },
            ]);
        }
        if (key === "costHeads") {
            const ch = detail ? detail.cost_heads : mis && mis.charts.cost_heads;
            if (!ch || !ch.labels.length) {
                return null;
            }
            return bar(ch.labels, [
                { label: "Planned", data: ch.planned, backgroundColor: "#2f4a63" },
                { label: "Actual", data: ch.actual, backgroundColor: "#f6a609" },
            ]);
        }
        return null;
    }

    _mount(key, canvas, all = false) {
        if (!canvas) {
            return;
        }
        const config = this._configFor(key, all);
        if (config) {
            this.charts[key === "fullscreen" ? "fullscreen" : key] = new Chart(canvas, config);
        }
    }

    renderAllCharts() {
        this.destroyCharts();

        const inline = this.state.selectedMasterId
            ? ["pnl", "stageBars", "costHeads"]
            : ["pnlByStage", "projectBars", "timeline", "costHeads"];
        for (const key of inline) {
            const ref = this.chartRefs[key];
            this._mount(key, ref && ref.el);
        }

        if (this.state.fullscreenChart) {
            const canvas = this.fullscreenRef.el;
            if (canvas) {
                const config = this._configFor(this.state.fullscreenChart, true);
                if (config) {
                    const chart = new Chart(canvas, config);
                    this.charts.fullscreen = chart;
                    // The modal's flex layout settles a frame after mount; without this the
                    // canvas keeps the pre-layout size and the browser stretches (blurs) it.
                    requestAnimationFrame(() => {
                        if (this.charts.fullscreen === chart) {
                            chart.resize();
                        }
                    });
                }
            }
        }
    }

    openFullscreen(key) {
        this.state.fullscreenChart = key;
    }

    closeFullscreen() {
        this.state.fullscreenChart = null;
    }

    onChangeStageFilter(ev) {
        this.state.stageFilter = ev.target.value;
    }

    get fullscreenTitle() {
        return {
            stageBars: "Budget vs Actual by Stage",
            projectBars: "Contract vs Cost vs Billed",
            timeline: "Cost vs Revenue Trend",
            pnlByStage: "Profit & Loss Trend by Stage (All Projects)",
            costHeads: "Planned vs Actual by Cost Head",
            pnl: "Profit & Loss Trend",
        }[this.state.fullscreenChart] || "Chart";
    }

    // -------------------------------------------------------------- helpers
    get currency() {
        const src = this.state.detail || this.state.mis;
        return (src && src.currency_symbol) || "";
    }

    formatMoney(value) {
        if (!value && value !== 0) {
            return "-";
        }
        return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(value);
    }

    /** Compact form for the big KPI tiles: 1.25 Cr / 8.40 L / 42,000 */
    formatCompact(value) {
        const n = Number(value) || 0;
        const abs = Math.abs(n);
        const sign = n < 0 ? "-" : "";
        if (abs >= 10000000) {
            return `${sign}${(abs / 10000000).toFixed(2)} Cr`;
        }
        if (abs >= 100000) {
            return `${sign}${(abs / 100000).toFixed(2)} L`;
        }
        return `${sign}${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(abs)}`;
    }

    statusColor(status) {
        return {
            draft: "secondary",
            not_started: "secondary",
            active: "primary",
            in_progress: "primary",
            completed: "info",
            certified: "success",
            closed: "success",
        }[status] || "secondary";
    }

    progressColor(pct) {
        if (pct >= 100) return "success";
        if (pct >= 50) return "primary";
        if (pct >= 25) return "warning";
        return "danger";
    }

    marginClass(value) {
        if (value > 0) return "o_cm_pos";
        if (value < 0) return "o_cm_neg";
        return "o_cm_flat";
    }

    /** Deadline wording: -3 => "3 days late", 0 => "due today", 5 => "in 5 days". */
    deadlineLabel(days) {
        if (days === false || days === null || days === undefined) return "-";
        if (days < 0) return `${Math.abs(days)} day${Math.abs(days) === 1 ? "" : "s"} late`;
        if (days === 0) return "due today";
        return `in ${days} day${days === 1 ? "" : "s"}`;
    }

    deadlineClass(days) {
        if (days === false || days === null || days === undefined) return "o_cm_flat";
        if (days < 0) return "o_cm_neg";
        if (days <= 7) return "o_cm_warn";
        return "o_cm_pos";
    }

    barWidth(value, max) {
        if (!max) return 0;
        return Math.max(2, Math.round((Math.abs(value) / max) * 100));
    }

    get maxContract() {
        const rows = (this.state.mis && this.state.mis.projects) || [];
        return rows.reduce((m, r) => Math.max(m, r.contract || 0), 0);
    }

    flowWidth(value, totals) {
        const t = totals || {};
        const scale = Math.max(t.client_received || 0, t.vendor_paid || 0, t.cost || 0, t.client_billed || 0);
        return this.barWidth(value, scale);
    }

    cappedPct(value) {
        return Math.min(100, Math.max(0, Number(value) || 0));
    }

    initials(name) {
        if (!name) return "?";
        return name.trim().charAt(0).toUpperCase();
    }

    openRecord(model, id) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add("cm_dashboard_client_action", ConstructionDashboard);
