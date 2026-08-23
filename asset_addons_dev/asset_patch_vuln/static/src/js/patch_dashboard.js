/** @odoo-module **/

import { Component, useState, onWillStart, onMounted, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/**
 * Patch Management Dashboard.
 *
 * Follows the same shape as the existing antivirus/app-deployment dashboards
 * in this module: one ORM call to a model method that returns everything,
 * then Chart.js for the two charts.
 */
class PatchDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.severityCanvas = useRef("severityChart");
        this.trendCanvas = useRef("trendChart");
        this._charts = {};

        this.state = useState({
            loading: true,
            kpis: {
                missing_total: 0, missing_critical: 0, missing_high: 0,
                approved: 0, deployed: 0, failed: 0, compliance_pct: 0,
            },
            by_severity: { total: 0, items: [] },
            by_platform: { total: 0, items: [] },
            trend: { labels: [], values: [], has_data: false },
            deployment_summary: { in_progress: 0, pending: 0, successful: 0, failed: 0 },
            recent_deployments: [],
            top_missing: [],
            policies: [],
            lastRefresh: null,
        });

        onWillStart(async () => { await this.loadData(); });
        onMounted(() => { this.renderCharts(); });
    }

    async loadData() {
        this.state.loading = true;
        try {
            const data = await this.orm.call(
                "asset.patch.dashboard", "get_dashboard_data", []
            );
            Object.assign(this.state, data);
            this.state.lastRefresh = new Date().toLocaleTimeString();
        } catch (err) {
            this.notification.add(
                "Could not load patch dashboard data.", { type: "danger" }
            );
            console.error(err);
        } finally {
            this.state.loading = false;
        }
    }

    async onRefresh() {
        await this.loadData();
        this.renderCharts();
        this.notification.add("Dashboard refreshed.", { type: "success" });
    }

    renderCharts() {
        if (typeof Chart === "undefined") { return; }

        // Donut — missing patches by severity
        const sevEl = this.severityCanvas.el;
        if (sevEl && this.state.by_severity.items.length) {
            if (this._charts.severity) { this._charts.severity.destroy(); }
            const items = this.state.by_severity.items;
            this._charts.severity = new Chart(sevEl, {
                type: "doughnut",
                data: {
                    labels: items.map((i) => i.label),
                    datasets: [{
                        data: items.map((i) => i.count),
                        backgroundColor: items.map((i) => i.color),
                        borderWidth: 0,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: "68%",
                    plugins: { legend: { display: false } },
                },
            });
        }

        // Line — compliance over time
        const trendEl = this.trendCanvas.el;
        if (trendEl && this.state.trend.has_data) {
            if (this._charts.trend) { this._charts.trend.destroy(); }
            this._charts.trend = new Chart(trendEl, {
                type: "line",
                data: {
                    labels: this.state.trend.labels,
                    datasets: [{
                        label: "Compliance %",
                        data: this.state.trend.values,
                        borderColor: "#2563eb",
                        backgroundColor: "rgba(37, 99, 235, 0.08)",
                        fill: true,
                        tension: 0.3,
                        pointRadius: 3,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: { y: { beginAtZero: true, max: 100,
                                   ticks: { callback: (v) => v + "%" } } },
                    plugins: { legend: { display: false } },
                },
            });
        }
    }

    // ── Navigation ────────────────────────────────────────────────────────
    openMissingPatches() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Missing Patches",
            res_model: "asset.windows.update",
            views: [[false, "list"], [false, "form"]],
            domain: [["status", "in", ["pending", "allowed"]]],
        });
    }

    openDeployments() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Patch Deployments",
            res_model: "asset.patch.deployment",
            views: [[false, "list"], [false, "form"]],
        });
    }

    openDeployment(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "asset.patch.deployment",
            res_id: id,
            views: [[false, "form"]],
        });
    }

    openPolicies() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Update Policies",
            res_model: "asset.update.policy",
            views: [[false, "list"], [false, "form"]],
        });
    }


    // ── Drill-through ─────────────────────────────────────────────────────
    _patchModelFor(platform) {
        return {
            windows: "asset.windows.update",
            linux: "asset.linux.update",
            macos: "asset.macos.update",
        }[platform] || "asset.windows.update";
    }

    openByStatus(statuses, label) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: label,
            res_model: "asset.windows.update",
            views: [[false, "list"], [false, "form"]],
            domain: [["status", "in", statuses]],
        });
    }

    openApproved() {
        this.openByStatus(["installing"], "Approved / Queued Patches");
    }

    openDeployed() {
        this.openByStatus(["installed"], "Installed Patches");
    }

    openFailed() {
        this.openByStatus(["failed"], "Failed Patches");
    }

    openCompliance() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Compliance History",
            res_model: "asset.patch.compliance.snapshot",
            views: [[false, "list"]],
        });
    }

    openBySeverity(label) {
        // Dashboard buckets map onto the platform models' four severities
        const map = {
            Critical: ["security", "critical"],
            High: ["important"],
            Medium: ["important"],
            Low: ["optional"],
        };
        const severities = map[label] || ["optional"];
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${label} — Missing Patches`,
            res_model: "asset.windows.update",
            views: [[false, "list"], [false, "form"]],
            domain: [
                ["status", "in", ["pending", "allowed"]],
                ["severity", "in", severities],
            ],
        });
    }

    openByPlatform(platform, label) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${label} — Missing Patches`,
            res_model: this._patchModelFor(platform),
            views: [[false, "list"], [false, "form"]],
            domain: [["status", "in", ["pending", "allowed"]]],
        });
    }

    openDeploymentsByState(states, label) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: label,
            res_model: "asset.patch.deployment",
            views: [[false, "list"], [false, "form"]],
            domain: [["state", "in", states]],
        });
    }

    openPatchAcrossAssets(patchId, platform) {
        const model = this._patchModelFor(platform);
        const field = platform === "windows" ? "kb_number" : "package_name";
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${patchId} — affected machines`,
            res_model: model,
            views: [[false, "list"], [false, "form"]],
            domain: [
                [field, "=", patchId],
                ["status", "in", ["pending", "allowed"]],
            ],
        });
    }

    openPolicy(policyId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "asset.update.policy",
            res_id: policyId,
            views: [[false, "form"]],
        });
    }

    severityBadge(sev) {
        const map = {
            security: "pv-badge-critical", critical: "pv-badge-critical",
            important: "pv-badge-high", optional: "pv-badge-low",
        };
        return map[sev] || "pv-badge-medium";
    }

    stateBadge(st) {
        const map = {
            successful: "pv-badge-success", success: "pv-badge-success",
            failed: "pv-badge-critical", partial: "pv-badge-high",
            in_progress: "pv-badge-info", pending: "pv-badge-medium",
            draft: "pv-badge-muted", cancelled: "pv-badge-muted",
        };
        return map[st] || "pv-badge-muted";
    }
}

PatchDashboard.template = "asset_patch_vuln.PatchDashboard";
registry.category("actions").add("patch_management_dashboard", PatchDashboard);

export default PatchDashboard;
