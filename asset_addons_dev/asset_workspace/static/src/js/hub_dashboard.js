/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/**
 * Unified dashboard hub.
 *
 * Two filters — platform and view. Picking a combination opens the existing
 * dashboard for it. The default state is a consolidated fleet summary, which
 * is the only genuinely new content here; everything else routes to
 * dashboards that already exist.
 */
class AssetHubDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            loading: true,
            registry: [],
            platforms: [],
            health: {},
            attention: [],
            selectedPlatform: null,   // null = consolidated overview
            lastRefresh: null,
        });

        onWillStart(async () => { await this.load(); });
    }

    async load() {
        this.state.loading = true;
        try {
            const data = await this.orm.call(
                "asset.hub.dashboard", "get_hub_data", []
            );
            Object.assign(this.state, data);
            this.state.lastRefresh = new Date().toLocaleTimeString();
        } catch (err) {
            this.notification.add("Could not load dashboard data.",
                                  { type: "danger" });
            console.error(err);
        } finally {
            this.state.loading = false;
        }
    }

    async onRefresh() {
        await this.load();
        this.notification.add("Refreshed.", { type: "success" });
    }

    // ── Filters ───────────────────────────────────────────────────────────
    selectPlatform(key) {
        // Clicking the active platform again returns to the overview
        this.state.selectedPlatform =
            this.state.selectedPlatform === key ? null : key;
    }

    get activePlatform() {
        if (!this.state.selectedPlatform) { return null; }
        return this.state.registry.find(
            (p) => p.key === this.state.selectedPlatform) || null;
    }

    get activePlatformSummary() {
        if (!this.state.selectedPlatform) { return null; }
        return this.state.platforms.find(
            (p) => p.key === this.state.selectedPlatform) || null;
    }

    // ── Routing ───────────────────────────────────────────────────────────
    async openView(platformKey, viewKey) {
        try {
            const res = await this.orm.call(
                "asset.hub.dashboard", "open_dashboard",
                [platformKey, viewKey]
            );
            if (res.error) {
                this.notification.add(res.error, { type: "warning" });
                return;
            }
            this.action.doAction(res.action_id);
        } catch (err) {
            this.notification.add("Could not open that dashboard.",
                                  { type: "danger" });
            console.error(err);
        }
    }

    openAsset(assetId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "asset.asset",
            res_id: assetId,
            views: [[false, "form"]],
        });
    }

    openModel(model, name, domain) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: model,
            views: [[false, "list"], [false, "form"]],
            domain: domain || [],
        });
    }

    openPatchesPending() {
        this.openModel("asset.windows.update", "Pending Patches",
                       [["status", "in", ["pending", "allowed"]]]);
    }

    openPatchesFailed() {
        this.openModel("asset.windows.update", "Failed Patches",
                       [["status", "=", "failed"]]);
    }

    openAppUpdates() {
        this.openModel("asset.app.update", "Application Updates",
                       [["status", "in", ["available", "failed"]]]);
    }

    openVulns(severity) {
        const domain = [["state", "=", "open"]];
        if (severity) { domain.push(["severity", "=", severity]); }
        this.openModel("asset.vulnerability", "Open Vulnerabilities", domain);
    }

    openReviewQueue() {
        this.openModel("asset.vulnerability", "Review Queue",
                       [["state", "=", "needs_review"]]);
    }

    openUnmanagedEndpoints() {
        this.openModel("asset.network.device", "Unmanaged Endpoints",
                       [["is_unmanaged_endpoint", "=", true]]);
    }

    openActiveAlerts() {
        this.openModel("asset.telemetry.alert.log", "Active Alerts",
                       [["state", "=", "active"]]);
    }

    openSecurityConsole() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Asset Security Console",
            res_model: "asset.asset",
            views: [[false, "list"], [false, "form"]],
            domain: [["platform", "in", ["windows", "linux", "macos"]]],
        });
    }

    // ── Presentation helpers ──────────────────────────────────────────────
    statusClass(status) {
        return {
            at_risk: "hub-badge-danger",
            attention: "hub-badge-warning",
            good: "hub-badge-ok",
            unknown: "hub-badge-muted",
        }[status] || "hub-badge-muted";
    }

    statusLabel(status) {
        return {
            at_risk: "At Risk",
            attention: "Needs Attention",
            good: "Good",
            unknown: "Not Assessed",
        }[status] || status;
    }

    get totalAssets() {
        return this.state.platforms.reduce((sum, p) => sum + p.total, 0);
    }

    get totalOnline() {
        return this.state.platforms.reduce((sum, p) => sum + p.online, 0);
    }

    get totalOffline() {
        return this.state.platforms.reduce((sum, p) => sum + p.offline, 0);
    }

    onlinePct(platform) {
        if (!platform.total) { return 0; }
        return Math.round((100 * platform.online) / platform.total);
    }
}

AssetHubDashboard.template = "asset_workspace.HubDashboard";
registry.category("actions").add("asset_hub_dashboard", AssetHubDashboard);

export default AssetHubDashboard;