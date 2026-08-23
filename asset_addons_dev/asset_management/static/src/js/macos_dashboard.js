/** @odoo-module **/

import { Component, useState, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/* =========================
   CHART.JS CENTER TEXT PLUGIN
   ========================= */
const donutCenterTextPlugin = {
    id: "donutCenterText",
    afterDraw(chart) {
        const { ctx, chartArea } = chart;
        if (!chartArea) return;

        const total = chart.config.options.plugins?.centerText?.total || 0;
        const label = chart.config.options.plugins?.centerText?.label || "";

        const centerX = (chartArea.left + chartArea.right) / 2;
        const centerY = (chartArea.top + chartArea.bottom) / 2;

        ctx.save();

        ctx.font = "700 34px Inter, Arial, sans-serif";
        ctx.fillStyle = "#212529";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(total.toString(), centerX, centerY - 8);

        ctx.font = "500 13px Inter, Arial, sans-serif";
        ctx.fillStyle = "#6c757d";
        ctx.fillText(label, centerX, centerY + 18);

        ctx.restore();
    },
};

class MacOSDashboard extends Component {

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            total: 0,
            assigned: 0,
            maintenance: 0,
            scrapped: 0,
            draft: 0,
            agent_stats: {
                active: 0,
                offline: 0,
                never: 0,
            },
            change_alerts: 0,
            recent_syncs: 0,
            change_alert_list: [],
            recent_logs: [],
            loading: true,
            last_refresh: null,
            auto_refresh: true,
            // macOS-specific KPIs
            critical_alerts: 0,
            warning_alerts: 0,
            info_alerts: 0,
            no_warranty: 0,
            overdue_maintenance: 0,
            idle_30_days: 0,
            not_synced_7_days: 0,
            risk_compliance_expanded: false,
        });

        this.charts = {};
        this.chartsRendered = false;
        this.refreshInterval = null;
        this.chartJsLoaded = false;

        onWillStart(async () => {
            await this.waitForChartJs();
            await this.loadData();
        });

        onMounted(() => {
            setTimeout(() => {
                if (!this.chartsRendered && this.chartJsLoaded) {
                    this.renderCharts();
                    this.chartsRendered = true;
                }
                this.startAutoRefresh();
            }, 300);
        });

        onWillUnmount(() => {
            this.destroyCharts();
            this.stopAutoRefresh();
        });
    }

    async waitForChartJs() {
        const maxAttempts = 20;
        let attempts = 0;

        while (!window.Chart && attempts < maxAttempts) {
            await new Promise(resolve => setTimeout(resolve, 100));
            attempts++;
        }

        if (window.Chart) {
            this.chartJsLoaded = true;
            console.log("✅ Chart.js loaded for macOS dashboard");

            if (!window.Chart.registry.plugins.get('donutCenterText')) {
                window.Chart.register(donutCenterTextPlugin);
            }
        } else {
            console.error("❌ Chart.js failed to load");
            this.notification.add("Chart.js library failed to load.", {
                type: "warning",
            });
        }
    }

    async loadData() {
        this.state.loading = true;

        try {
            // Load macOS-specific data with context
            const kpis = await this.orm.call("asset.dashboard", "get_kpis", [], {
                context: { os_platform: 'macos' }
            });
            Object.assign(this.state, kpis);

            const alerts = await this.orm.call("asset.dashboard", "get_change_alerts", [], {
                context: { os_platform: 'macos' }
            });
            this.state.change_alert_list = alerts || [];

            // Calculate alert severities
            this.state.critical_alerts = alerts.filter(a => a.severity === 'critical').length;
            this.state.warning_alerts = alerts.filter(a => a.severity === 'warning').length;
            this.state.info_alerts = alerts.filter(a => a.severity === 'info').length;

            const logs = await this.orm.call("asset.dashboard", "get_recent_logs", [20], {
                context: { os_platform: 'macos' }
            });
            this.state.recent_logs = logs || [];

            this.state.last_refresh = new Date().toLocaleTimeString([], {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: true
            });
            console.log("✅ macOS Dashboard data loaded");

        } catch (error) {
            console.error("❌ Failed to load macOS dashboard data:", error);
            this.notification.add("Failed to load dashboard data.", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    async refreshData() {
        await this.loadData();
        if (this.chartsRendered) {
            this.updateCharts();
        }
    }

    startAutoRefresh() {
        if (this.state.auto_refresh) {
            this.refreshInterval = setInterval(() => {
                this.refreshData();
            }, 30000); // 30 seconds
        }
    }

    stopAutoRefresh() {
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
            this.refreshInterval = null;
        }
    }

    toggleAutoRefresh() {
        this.state.auto_refresh = !this.state.auto_refresh;
        if (this.state.auto_refresh) {
            this.startAutoRefresh();
            this.notification.add("Auto-refresh enabled", { type: "success" });
        } else {
            this.stopAutoRefresh();
            this.notification.add("Auto-refresh disabled", { type: "info" });
        }
    }

    destroyCharts() {
        Object.values(this.charts).forEach(chart => {
            if (chart) {
                try {
                    chart.destroy();
                } catch (e) {
                    console.warn("Chart destroy error:", e);
                }
            }
        });
        this.charts = {};
        this.chartsRendered = false;
    }

    // =====================================================
    // macOS SPECIFIC ACTIONS
    // =====================================================

    openAssetsByState(state) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${state.charAt(0).toUpperCase() + state.slice(1)} macOS Assets`,
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["state", "=", state], ["os_platform", "=", "macos"]],
        });
    }

    openAllAssets() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "All macOS Assets",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["os_platform", "=", "macos"]],
        });
    }

    openChangeAlerts() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "macOS Assets with Changes",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["has_changes", "=", true], ["os_platform", "=", "macos"]],
        });
    }

    openAlertsBySeverity(severity) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${severity.charAt(0).toUpperCase() + severity.slice(1)} macOS Alerts`,
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["has_changes", "=", true], ["alert_severity", "=", severity], ["os_platform", "=", "macos"]],
        });
    }

    openAgentStatus(status) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${status.charAt(0).toUpperCase() + status.slice(1)} macOS Agents`,
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["agent_status", "=", status], ["os_platform", "=", "macos"]],
        });
    }

    openAgentLogs() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "macOS Agent Sync Logs",
            res_model: "asset.agent.log",
            view_mode: "tree,form",
            views: [[false, "list"], [false, "form"]],
            domain: [["asset_id.os_platform", "=", "macos"]],
        });
    }

    openAsset(assetId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Asset",
            res_model: "asset.asset",
            res_id: assetId,
            view_mode: "form",
            views: [[false, "form"]],
        });
    }

    toggleRiskCompliance() {
        this.state.risk_compliance_expanded = !this.state.risk_compliance_expanded;
    }

    approveAllChanges() {
        this.notification.add("Approve All Changes is not implemented for macOS dashboard.", { type: "info" });
    }

    createMaintenanceSchedule() {
        this.notification.add("Schedule Maintenance is not implemented for macOS dashboard.", { type: "info" });
    }

    downloadPDF() {
        window.print();
    }

    openNoWarrantyAssets() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "macOS No Warranty",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            domain: [["os_platform", "=", "macos"]],
        });
    }

    openOverdueMaintenanceAssets() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "macOS Overdue Maintenance",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            domain: [["os_platform", "=", "macos"]],
        });
    }

    openIdle30DaysAssets() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "macOS Idle 30+ Days",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            domain: [["os_platform", "=", "macos"]],
        });
    }

    openNotSynced7DaysAssets() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "macOS Not Synced 7+ Days",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            domain: [["os_platform", "=", "macos"]],
        });
    }

    resolveAssetImage(asset) {
        if (asset.has_image) {
            const model = 'asset.asset';
            const id = asset.id || asset.asset_id;
            return `/web/image/${model}/${id}/image_1920`;
        }
        return '/asset_management/static/src/img/asset_laptop_default.png';
    }

    updateCharts() {
        try {
            if (this.charts.donut) {
                const assetData = [
                    this.state.assigned || 0,
                    this.state.maintenance || 0,
                    this.state.scrapped || 0,
                    this.state.draft || 0,
                ];
                const assetSum = assetData.reduce((a, b) => a + b, 0);
                this.charts.donut.data.datasets[0].data = assetData;
                this.charts.donut.data.labels = ["Assigned", "Maintenance", "Scrapped", "Draft"];
                this.charts.donut.data.datasets[0].backgroundColor = ["#0d6efd", "#fd7e14", "#dc3545", "#6c757d"];
                this.charts.donut.options.plugins.centerText.total = this.state.total || 0;
                this.charts.donut.update('none');
            }

            if (this.charts.agentDonut) {
                const agentData = [
                    this.state.agent_stats.active || 0,
                    this.state.agent_stats.offline || 0,
                    this.state.agent_stats.never || 0,
                ];
                const agentSum = agentData.reduce((a, b) => a + b, 0);
                this.charts.agentDonut.data.datasets[0].data = agentData;
                this.charts.agentDonut.data.labels = ["Online", "Offline", "Never Synced"];
                this.charts.agentDonut.data.datasets[0].backgroundColor = ["#198754", "#ffc107", "#6c757d"];
                this.charts.agentDonut.options.plugins.centerText.total = agentSum || 0;
                this.charts.agentDonut.update('none');
            }
        } catch (error) {
            console.error("❌ Error updating charts:", error);
        }
    }

    renderCharts() {
        if (!window.Chart) {
            console.error("❌ Chart.js not available");
            return;
        }

        console.log("🎨 Rendering macOS charts...");

        const safeDestroy = (canvasId) => {
            const existing = window.Chart.getChart(canvasId);
            if (existing) existing.destroy();
        };

        const donut = document.getElementById("macos_asset_status_donut");
        if (donut) {
            try {
                safeDestroy("macos_asset_status_donut");
                const ctx = donut.getContext('2d');
                const total = this.state.total || 0;

                /* Same as Windows: always show Assigned, Maintenance, Scrapped, Draft; 0 when no data */
                const assetData = [
                    this.state.assigned || 0,
                    this.state.maintenance || 0,
                    this.state.scrapped || 0,
                    this.state.draft || 0,
                ];
                const assetSum = assetData.reduce((a, b) => a + b, 0);
                const assetDataFinal = assetData;
                const assetLabelsFinal = ["Assigned", "Maintenance", "Scrapped", "Draft"];
                const assetColorsFinal = ["#0d6efd", "#fd7e14", "#dc3545", "#6c757d"];

                this.charts.donut = new Chart(ctx, {
                    type: "doughnut",
                    data: {
                        labels: assetLabelsFinal,
                        datasets: [{
                            data: assetDataFinal,
                            backgroundColor: assetColorsFinal,
                            borderWidth: 3,
                            borderColor: "#ffffff",
                            hoverBorderWidth: 4,
                        }],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        cutout: "72%",
                        layout: {
                            padding: 0,
                        },
                        plugins: {
                            legend: {
                                position: "right",
                                labels: {
                                    boxWidth: 14,
                                    boxHeight: 14,
                                    padding: 14,
                                    font: {
                                        size: 12,
                                        family: "'Inter', sans-serif",
                                    },
                                },
                            },
                            centerText: {
                                total: total,
                                label: "Assets",
                            },
                            tooltip: {
                                callbacks: {
                                    label: function (context) {
                                        const label = context.label || '';
                                        const value = context.parsed || 0;
                                        const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                        const percentage = total > 0 ? ((value / total) * 100).toFixed(1) : 0;
                                        return `${label}: ${value} (${percentage}%)`;
                                    },
                                },
                            },
                        },
                    },
                });
                console.log("✅ macOS asset status donut rendered");
            } catch (error) {
                console.error("❌ Error rendering asset status donut:", error);
            }
        }

        const agentDonut = document.getElementById("macos_agent_status_donut");
        if (agentDonut) {
            try {
                safeDestroy("macos_agent_status_donut");
                const ctx = agentDonut.getContext('2d');
                /* Same as Windows: always show Online, Offline, Never Synced; 0 when no data */
                const agentData = [
                    this.state.agent_stats.active || 0,
                    this.state.agent_stats.offline || 0,
                    this.state.agent_stats.never || 0,
                ];
                const agentSum = agentData.reduce((a, b) => a + b, 0);
                const agentDataFinal = agentData;
                const agentLabelsFinal = ["Online", "Offline", "Never Synced"];
                const agentColorsFinal = ["#198754", "#ffc107", "#6c757d"];
                const agentTotal = agentSum;

                this.charts.agentDonut = new Chart(ctx, {
                    type: "doughnut",
                    data: {
                        labels: agentLabelsFinal,
                        datasets: [{
                            data: agentDataFinal,
                            backgroundColor: agentColorsFinal,
                            borderWidth: 3,
                            borderColor: "#ffffff",
                            hoverBorderWidth: 4,
                        }],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        cutout: "72%",
                        layout: {
                            padding: 0,
                        },
                        plugins: {
                            legend: {
                                position: "right",
                                labels: {
                                    boxWidth: 14,
                                    boxHeight: 14,
                                    padding: 14,
                                    font: {
                                        size: 12,
                                        family: "'Inter', sans-serif",
                                    },
                                },
                            },
                            centerText: {
                                total: agentTotal,
                                label: "Agents",
                            },
                            tooltip: {
                                callbacks: {
                                    label: function (context) {
                                        const label = context.label || '';
                                        const value = context.parsed || 0;
                                        const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                        const percentage = total > 0 ? ((value / total) * 100).toFixed(1) : 0;
                                        return `${label}: ${value} (${percentage}%)`;
                                    },
                                },
                            },
                        },
                    },
                });
                console.log("✅ macOS agent status donut rendered");
            } catch (error) {
                console.error("❌ Error rendering agent status donut:", error);
            }
        }
    }
}

MacOSDashboard.template = "asset_management.MacOSDashboard";

registry.category("actions").add("macos_dashboard", MacOSDashboard);

export default MacOSDashboard;
