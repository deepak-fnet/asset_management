/** @odoo-module **/

/**
 * OneIT dashboard - Odoo 19 (OWL 2).
 *
 * Rewritten from the Odoo 14 AbstractAction. What changed:
 *   - AbstractAction         -> OWL Component + registry("actions")
 *   - rpc.query({model,...}) -> useService("orm").call(...)
 *   - this.$(...) / jQuery   -> useRef + plain DOM
 *   - manual _render()       -> reactive useState
 *   - Chart.js from a global -> loadJS on the bundled asset
 */

import { Component, onWillStart, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadJS } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";

// Palette. Deliberately not Odoo's default chart colours: AD Folder /
// Citrix / File Server must stay visually distinct wherever they appear,
// so the same colour means the same access type on every chart.
const COLORS = {
    ad_folder: "#7C5CBF",
    citrix: "#2E9BDA",
    file_server: "#17A98C",
    draft: "#9AA0A6",
    pending: "#F2A93B",
    tl_approved: "#F2A93B",
    approved: "#2E9BDA",
    ready_to_delete: "#E8724C",
    done: "#17A98C",
    rejected: "#D94F4F",
    onboarding: "#17A98C",
    offboarding: "#E8724C",
    team_update: "#2E9BDA",
};

export class OneitDashboard extends Component {
    static template = "oneit_ad_automation.Dashboard";
    static props = {
        "*": true,
    };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            data: null,
            lookup: null,
            loading: false,
            searching: false,
            mode: "user", // 'user' = access for a person, 'group' = members of a group
            verifyLive: false,
            term: "",
        });

        this.charts = [];
        this.chartRefs = {
            access: useRef("chartAccess"),
            states: useRef("chartStates"),
            types: useRef("chartTypes"),
            groups: useRef("chartGroups"),
            teams: useRef("chartTeams"),
        };

        onWillStart(async () => {
            // Chart.js ships with web but is not in the backend bundle by
            // default in 19, so it is pulled in explicitly rather than
            // assumed to be on window.
            await loadJS("/web/static/lib/Chart/Chart.js");
            await this.loadData();
        });

        onMounted(() => this.renderCharts());
        onWillUnmount(() => this.destroyCharts());
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------
    async loadData() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "oneit.dashboard", "get_dashboard_data", []
            );
        } finally {
            this.state.loading = false;
        }
    }

    async onRefresh() {
        await this.loadData();
        this.scheduleChartRender();
    }

    // ------------------------------------------------------------------
    // Charts
    // ------------------------------------------------------------------
    destroyCharts() {
        for (const chart of this.charts) {
            try {
                chart.destroy();
            } catch {
                // already gone
            }
        }
        this.charts = [];
    }

    scheduleChartRender() {
        // Canvas elements only exist after OWL has patched the DOM, and
        // Chart.js needs a sized canvas, so this waits a frame rather
        // than drawing during render.
        requestAnimationFrame(() => this.renderCharts());
    }

    makeChart(canvas, config) {
        if (!canvas || typeof Chart === "undefined") {
            return;
        }
        this.charts.push(new Chart(canvas.getContext("2d"), config));
    }

    doughnutConfig(items) {
        return {
            type: "doughnut",
            data: {
                labels: items.map((i) => i.label),
                datasets: [{
                    data: items.map((i) => i.value),
                    backgroundColor: items.map((i) => COLORS[i.key] || "#9AA0A6"),
                    borderWidth: 0,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                // Chart.js 3+ renamed cutoutPercentage and moved legend
                // under plugins.
                cutout: "62%",
                plugins: {
                    legend: {
                        position: "bottom",
                        labels: { boxWidth: 12, padding: 14 },
                    },
                },
            },
        };
    }

    barConfig(items, { horizontal = false, color = null } = {}) {
        return {
            type: "bar",
            data: {
                labels: items.map((i) => i.label),
                datasets: [{
                    data: items.map((i) => i.value),
                    backgroundColor: color
                        ? color
                        : items.map((i) => COLORS[i.key] || "#9AA0A6"),
                    borderRadius: 4,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                // horizontalBar was removed as a type in Chart.js 3; it is
                // now indexAxis on a normal bar chart.
                indexAxis: horizontal ? "y" : "x",
                plugins: { legend: { display: false } },
                scales: {
                    x: {
                        beginAtZero: true,
                        ticks: { precision: 0 },
                        grid: { display: horizontal },
                    },
                    y: {
                        beginAtZero: true,
                        ticks: { precision: 0 },
                        grid: { display: !horizontal },
                    },
                },
            },
        };
    }

    renderCharts() {
        this.destroyCharts();
        const d = this.state.data;
        if (!d) {
            return;
        }

        const lookup = this.state.lookup;
        if (this.state.mode === "user" && lookup && lookup.found && lookup.by_type) {
            const byType = lookup.by_type.filter((t) => t.value > 0);
            if (byType.length) {
                this.makeChart(this.chartRefs.access.el, this.doughnutConfig(byType));
            }
        }

        this.makeChart(
            this.chartRefs.states.el,
            this.barConfig(d.states.filter((s) => s.value > 0))
        );
        this.makeChart(
            this.chartRefs.types.el,
            this.doughnutConfig(d.types.filter((t) => t.value > 0))
        );
        this.makeChart(
            this.chartRefs.groups.el,
            this.barConfig(d.group_types, { horizontal: true })
        );
        if (d.teams.length) {
            this.makeChart(
                this.chartRefs.teams.el,
                this.barConfig(d.teams, { horizontal: true, color: "#7C5CBF" })
            );
        }
    }

    // ------------------------------------------------------------------
    // Lookup
    // ------------------------------------------------------------------
    onModeChange(mode) {
        if (mode === this.state.mode) {
            return;
        }
        this.state.mode = mode;
        // Results from the other mode have a different shape, so clear
        // rather than trying to reinterpret them.
        this.state.lookup = null;
        this.scheduleChartRender();
    }

    onTermInput(ev) {
        this.state.term = ev.target.value;
    }

    onTermKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.onLookup();
        }
    }

    onToggleVerify(ev) {
        this.state.verifyLive = ev.target.checked;
    }

    async onLookup() {
        const term = (this.state.term || "").trim();
        if (!term) {
            return;
        }
        this.state.searching = true;
        try {
            const method = this.state.mode === "group"
                ? "group_member_lookup"
                : "employee_access_lookup";
            const args = this.state.mode === "group"
                ? [term]
                : [term, this.state.verifyLive];
            this.state.lookup = await this.orm.call("oneit.dashboard", method, args);
            this.scheduleChartRender();
        } finally {
            this.state.searching = false;
        }
    }

    onClearLookup() {
        this.state.lookup = null;
        this.state.term = "";
        this.scheduleChartRender();
    }

    // ------------------------------------------------------------------
    // Drill-down
    // ------------------------------------------------------------------
    openRequests(domain) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Requests"),
            res_model: "oneit.request",
            // Odoo 18 renamed the list view type to list.
            views: [[false, "list"], [false, "form"]],
            domain: domain || [],
            target: "current",
        });
    }

    openRequest(id) {
        if (!id) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "oneit.request",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add("oneit_dashboard", OneitDashboard);
