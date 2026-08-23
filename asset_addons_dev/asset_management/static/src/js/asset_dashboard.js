/** @odoo-module **/

import { Component, useState, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatDateTime, deserializeDateTime } from "@web/core/l10n/dates";

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

class AssetDashboard extends Component {

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        // Capture platform from action context (windows, linux, mac)
        this.platform = this.props.action?.context?.os_platform || 'windows';

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
            risk_compliance_expanded: false,
            camera_monitoring_expanded: false,
            // NEW: Alert severity counts
            critical_alerts: 0,
            warning_alerts: 0,
            info_alerts: 0,
            // NEW: Enhanced KPI counts
            no_warranty: 0,
            overdue_maintenance: 0,
            idle_30_days: 0,
            not_synced_7_days: 0,
            camera_stats: {
                total: 0,
                online: 0,
                offline: 0,
                offline_percentage: 0
            },
            recent_cctv_events: [],
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

    formatDateTime(dtStr) {
        if (!dtStr) return "";
        try {
            const dt = deserializeDateTime(dtStr);
            return dt.toFormat("dd/MM/yyyy HH:mm:ss");
        } catch (e) {
            return String(dtStr);
        }
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
            console.log("✅ Chart.js loaded successfully");

            if (!window.Chart.registry.plugins.get('donutCenterText')) {
                window.Chart.register(donutCenterTextPlugin);
            }
        } else {
            console.error("❌ Chart.js failed to load");
            this.notification.add("Chart.js library failed to load. Charts will not be displayed.", {
                type: "warning",
            });
        }
    }

    async loadData() {
        this.state.loading = true;

        try {
            const kpis = await this.orm.call("asset.dashboard", "get_kpis", []);
            Object.assign(this.state, kpis);

            const alerts = await this.orm.call("asset.dashboard", "get_change_alerts", []);
            this.state.change_alert_list = alerts || [];

            // NEW: Calculate alert severities
            this.state.critical_alerts = alerts.filter(a => a.severity === 'critical').length;
            this.state.warning_alerts = alerts.filter(a => a.severity === 'warning').length;
            this.state.info_alerts = alerts.filter(a => a.severity === 'info').length;

            const logs = await this.orm.call("asset.dashboard", "get_recent_logs", [20]);
            this.state.recent_logs = logs || [];

            const cctvEvents = await this.orm.call("asset.dashboard", "get_recent_cctv_events", []);
            this.state.recent_cctv_events = cctvEvents || [];

            if (!Array.isArray(this.state.asset_value_trend)) {
                this.state.asset_value_trend = [];
            }

            this.state.last_refresh = luxon.DateTime.now().toFormat("dd/MM/yyyy HH:mm:ss");
            console.log("✅ Dashboard data loaded");

        } catch (error) {
            console.error("❌ Failed to load dashboard data:", error);
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
            }, 30000);
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
    // 🎯 NEW: CLICKABLE KPI ACTIONS
    // =====================================================

    openAssetsByState(state) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${state.charAt(0).toUpperCase() + state.slice(1)} ${this.getPlatformLabel()} Assets`,
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["state", "=", state], ["os_platform", "=", this.platform]],
        });
    }

    openAllAssets() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `All ${this.getPlatformLabel()} Assets`,
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["os_platform", "=", this.platform]],
        });
    }

    openChangeAlerts() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${this.getPlatformLabel()} Assets with Changes`,
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["has_changes", "=", true], ["os_platform", "=", this.platform]],
        });
    }

    // NEW: Open alerts by severity
    openAlertsBySeverity(severity) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${severity.charAt(0).toUpperCase() + severity.slice(1)} ${this.getPlatformLabel()} Alerts`,
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["has_changes", "=", true], ["alert_severity", "=", severity], ["os_platform", "=", this.platform]],
        });
    }

    openAgentStatus(status) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${status.charAt(0).toUpperCase() + status.slice(1)} ${this.getPlatformLabel()} Agents`,
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["agent_status", "=", status], ["os_platform", "=", this.platform]],
        });
    }

    toggleRiskCompliance() {
        this.state.risk_compliance_expanded = !this.state.risk_compliance_expanded;
    }

    toggleCameraMonitoring() {
        this.state.camera_monitoring_expanded = !this.state.camera_monitoring_expanded;
    }

    openAgentLogs() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${this.getPlatformLabel()} Agent Sync Logs`,
            res_model: "asset.agent.log",
            view_mode: "tree,form",
            views: [[false, "list"], [false, "form"]],
            domain: [["asset_id.os_platform", "=", this.platform]],
        });
    }

    openCameraMonitoring() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "CCTV Camera Monitoring",
            res_model: "asset.camera",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
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

    async downloadPDF() {
        window.print();
    }

    /* =====================================================
       🖼️ IMAGE & ICON RESOLVERS
       ===================================================== */

    /**
     * Identifies the asset type based on available data.
     * @param {Object} asset - Asset data object
     * @returns {string} - Asset type key
     */
    _identifyAssetType(asset) {
        // Priority 1: Explicitly provided asset_type from backend
        if (asset.asset_type) return asset.asset_type;

        // Priority 2: Boolean flag
        if (asset.is_camera) return 'camera';

        // Priority 3: Heuristic based on name (fallback for missing fields)
        const name = (asset.asset_name || '').toLowerCase();
        if (name.includes('cctv') || name.includes('camera') || name.includes('cam')) {
            return 'camera';
        }
        if (name.includes('switch')) return 'switch';
        if (name.includes('router')) return 'router';

        return 'laptop';
    }

    /**
     * Resolves the correct image URL for an asset based on its type and status.
     * @param {Object} asset - Asset data object
     * @returns {string} - Correct image URL
     */
    resolveAssetImage(asset) {
        if (asset.has_image) {
            const model = asset.res_model || 'asset.asset';
            const id = asset.id || asset.asset_id;
            const field = model === 'asset.camera' ? 'camera_image' : 'image_1920';
            return `/web/image/${model}/${id}/${field}`;
        }

        const type = this._identifyAssetType(asset);

        const fallbacks = {
            'camera': '/asset_management/static/src/img/asset_cctv_default.png',
            'laptop': '/asset_management/static/src/img/asset_laptop_default.png',
            'switch': '/asset_management/static/src/img/asset_laptop_default.png', // Future: specific switch icon
            'router': '/asset_management/static/src/img/asset_laptop_default.png', // Future: specific router icon
        };

        return fallbacks[type] || fallbacks['laptop'];
    }

    /**
     * Resolves the correct FontAwesome icon for an asset based on its type.
     * @param {Object} asset - Asset data object
     * @returns {string} - FA icon class
     */
    resolveAssetIcon(asset) {
        const type = this._identifyAssetType(asset);

        const iconMap = {
            'camera': 'fa-video-camera',
            'laptop': 'fa-laptop',
            'switch': 'fa-server',
            'router': 'fa-wifi',
            'default': 'fa-cube',
        };

        return iconMap[type] || iconMap['default'];
    }

    /**
     * Get platform-specific label
     * @returns {string} - Platform label (Windows, Linux, macOS)
     */
    getPlatformLabel() {
        const labelMap = {
            'windows': 'Windows',
            'linux': 'Linux',
            'macos': 'macOS',
        };
        return labelMap[this.platform] || 'Assets';
    }

    /**
     * Get platform-specific dashboard title
     * @returns {string} - Dashboard title
     */
    getPlatformTitle() {
        const titleMap = {
            'windows': 'Windows Agents Dashboard',
            'linux': 'Linux Agents Dashboard',
            'macos': 'macOS Agents Dashboard',
        };
        return titleMap[this.platform] || 'Agents Dashboard';
    }

    /**
     * Get platform-specific icon class
     * @returns {string} - FontAwesome icon class
     */
    getPlatformIcon() {
        const iconMap = {
            'windows': 'fa-windows',
            'linux': 'fa-linux',
            'macos': 'fa-apple',
        };
        return iconMap[this.platform] || 'fa-desktop';
    }

    openNoWarrantyAssets() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Assets Without Warranty",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["warranty_status", "=", "none"]],
        });
    }

    openOverdueMaintenanceAssets() {
        const today = new Date().toISOString().split('T')[0];
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Overdue Maintenance",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["next_maintenance_date", "<", today]],
        });
    }

    openIdle30DaysAssets() {
        const thirtyDaysAgo = new Date();
        thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);
        const dateStr = thirtyDaysAgo.toISOString().split('T')[0] + ' 00:00:00';
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Idle Assets (> 30 Days)",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["last_sync_time", "<", dateStr]],
        });
    }

    openNotSynced7DaysAssets() {
        const sevenDaysAgo = new Date();
        sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7);
        const dateStr = sevenDaysAgo.toISOString().split('T')[0] + ' 00:00:00';
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Not Synced (> 7 Days)",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["last_sync_time", "<", dateStr]],
        });
    }

    // NEW: Quick Actions
    async approveAllChanges() {
        if (!confirm('Approve all pending changes?')) return;

        try {
            const assets = await this.orm.search("asset.asset", [["has_changes", "=", true]]);
            await this.orm.call("asset.asset", "action_bulk_approve_changes", [assets]);
            this.notification.add("All changes approved successfully", { type: "success" });
            await this.refreshData();
        } catch (error) {
            this.notification.add("Failed to approve changes", { type: "danger" });
        }
    }

    async createMaintenanceSchedule() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Schedule Maintenance",
            res_model: "asset.bulk.operations",
            view_mode: "form",
            views: [[false, "form"]],
            target: "new",
            context: {
                default_operation_type: 'maintenance',
            }
        });
    }

    updateCharts() {
        try {
            if (this.charts.donut) {
                this.charts.donut.data.datasets[0].data = [
                    this.state.assigned,
                    this.state.maintenance,
                    this.state.scrapped,
                    this.state.draft,
                ];
                this.charts.donut.options.plugins.centerText.total = this.state.total;
                this.charts.donut.update('none');
            }

            if (this.charts.agentDonut) {
                this.charts.agentDonut.data.datasets[0].data = [
                    this.state.agent_stats.active,
                    this.state.agent_stats.offline,
                    this.state.agent_stats.never,
                ];
                const agentTotal = this.state.agent_stats.active +
                    this.state.agent_stats.offline +
                    this.state.agent_stats.never;
                this.charts.agentDonut.options.plugins.centerText.total = agentTotal;
                this.charts.agentDonut.update('none');
            }

            if (this.charts.cameraDonut) {
                this.charts.cameraDonut.data.datasets[0].data = [
                    this.state.camera_stats.online,
                    this.state.camera_stats.offline,
                    this.state.camera_stats.recording || 0
                ];
                this.charts.cameraDonut.options.plugins.centerText.total = this.state.camera_stats.total;
                this.charts.cameraDonut.update('none');
            }

            if (this.charts.valueTrend && this.state.asset_value_trend.length) {
                this.charts.valueTrend.data.labels = this.state.asset_value_trend.map(v => v.label);
                this.charts.valueTrend.data.datasets[0].data = this.state.asset_value_trend.map(v => v.value);
                this.charts.valueTrend.update('none');
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

        console.log("🎨 Rendering charts...");

        const safeDestroy = (canvasId) => {
            const existing = window.Chart.getChart(canvasId);
            if (existing) existing.destroy();
        };

        const donut = document.getElementById("asset_status_donut");
        if (donut) {
            try {
                safeDestroy("asset_status_donut");
                const ctx = donut.getContext('2d');
                const total = this.state.total || 0;

                this.charts.donut = new Chart(ctx, {
                    type: "doughnut",
                    data: {
                        labels: ["Assigned", "Maintenance", "Scrapped", "Draft"],
                        datasets: [{
                            data: [
                                this.state.assigned,
                                this.state.maintenance,
                                this.state.scrapped,
                                this.state.draft,
                            ],
                            backgroundColor: ["#0d6efd", "#fd7e14", "#dc3545", "#6c757d"],
                            borderWidth: 3,
                            borderColor: "#ffffff",
                            hoverBorderWidth: 4,
                        }],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        cutout: "72%",
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
                                    }
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
                                    }
                                }
                            }
                        },
                    },
                });
                console.log("✅ Asset status donut rendered");
            } catch (error) {
                console.error("❌ Error rendering asset status donut:", error);
            }
        }

        const agentDonut = document.getElementById("agent_status_donut");
        if (agentDonut) {
            try {
                safeDestroy("agent_status_donut");
                const ctx = agentDonut.getContext('2d');
                const agentTotal = this.state.agent_stats.active +
                    this.state.agent_stats.offline +
                    this.state.agent_stats.never;

                this.charts.agentDonut = new Chart(ctx, {
                    type: "doughnut",
                    data: {
                        // ✅ CHANGED: "Active" → "Online"
                        labels: ["Online", "Offline", "Never Synced"],
                        datasets: [{
                            data: [
                                this.state.agent_stats.active,
                                this.state.agent_stats.offline,
                                this.state.agent_stats.never,
                            ],
                            backgroundColor: ["#198754", "#ffc107", "#6c757d"],
                            borderWidth: 3,
                            borderColor: "#ffffff",
                            hoverBorderWidth: 4,
                        }],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        cutout: "72%",
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
                                    }
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
                                    }
                                }
                            }
                        },
                    },
                });
                console.log("✅ Agent status donut rendered");
            } catch (error) {
                console.error("❌ Error rendering agent status donut:", error);
            }
        }

        const cameraDonut = document.getElementById("camera_status_donut");
        if (cameraDonut) {
            try {
                safeDestroy("camera_status_donut");
                const ctx = cameraDonut.getContext('2d');
                const cameraTotal = this.state.camera_stats.total || 0;

                this.charts.cameraDonut = new Chart(ctx, {
                    type: "doughnut",
                    data: {
                        labels: ["Online", "Offline", "Recording"],
                        datasets: [{
                            data: [
                                this.state.camera_stats.online,
                                this.state.camera_stats.offline,
                                this.state.camera_stats.recording || 0,
                            ],
                            backgroundColor: ["#198754", "#dc3545", "#fd7e14"],
                            borderWidth: 3,
                            borderColor: "#ffffff",
                            hoverBorderWidth: 4,
                        }],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        cutout: "72%",
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
                                    }
                                },
                            },
                            centerText: {
                                total: cameraTotal,
                                label: "Cameras",
                            },
                        },
                    },
                });
                console.log("✅ Camera status donut rendered");
            } catch (error) {
                console.error("❌ Error rendering camera status donut:", error);
            }
        }

        const valueEl = document.getElementById("asset_value_over_time");
        if (valueEl) {
            try {
                safeDestroy("asset_value_over_time");
                const ctx = valueEl.getContext('2d');
                const labels = this.state.asset_value_trend.length
                    ? this.state.asset_value_trend.map(v => v.label)
                    : ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

                const values = this.state.asset_value_trend.length
                    ? this.state.asset_value_trend.map(v => v.value)
                    : [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];

                this.charts.valueTrend = new Chart(ctx, {
                    type: "line",
                    data: {
                        labels,
                        datasets: [{
                            label: "Asset Value",
                            data: values,
                            borderColor: "#0d6efd",
                            backgroundColor: "rgba(13,110,253,.15)",
                            tension: 0.4,
                            fill: true,
                            pointRadius: 4,
                            pointHoverRadius: 6,
                            pointBackgroundColor: "#0d6efd",
                            pointBorderColor: "#ffffff",
                            pointBorderWidth: 2,
                        }],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                mode: 'index',
                                intersect: false,
                                callbacks: {
                                    label: function (context) {
                                        return `Value: $${context.parsed.y.toLocaleString()}`;
                                    }
                                }
                            }
                        },
                        scales: {
                            y: {
                                beginAtZero: true,
                                ticks: {
                                    callback: function (value) {
                                        return '$' + value.toLocaleString();
                                    }
                                },
                                grid: {
                                    color: 'rgba(0, 0, 0, 0.05)',
                                }
                            },
                            x: {
                                grid: {
                                    display: false,
                                }
                            }
                        },
                        interaction: {
                            mode: 'nearest',
                            axis: 'x',
                            intersect: false
                        },
                    },
                });
                console.log("✅ Asset value trend rendered");
            } catch (error) {
                console.error("❌ Error rendering asset value trend:", error);
            }
        }

        console.log("✅ All charts rendering completed");
    }
}

AssetDashboard.template = "asset_management.AssetDashboard";
registry.category("actions").add("asset_dashboard", AssetDashboard);
console.log("✅ AssetDashboard component registered");

/* =========================
   UBUNTU AGENT DASHBOARD
   ========================= */

class UbuntuAgentDashboard extends Component {
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
            critical_alerts: 0,
            warning_alerts: 0,
            info_alerts: 0,
            recent_syncs: [],
            change_alerts_list: [],
            loading: true,
            last_refresh: null,
            auto_refresh: true,
            risk_compliance_expanded: false,
        });

        this.charts = {};
        this.chartsRendered = false;
        this.refreshInterval = null;
        this.chartJsLoaded = false;

        onWillStart(async () => {
            await this.waitForChartJs();
            await this.loadDashboardData();
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
            if (!window.Chart.registry.plugins.get('donutCenterText')) {
                window.Chart.register(donutCenterTextPlugin);
            }
        }
    }

    formatDateTime(dtStr) {
        if (!dtStr) return "";
        try {
            const dt = deserializeDateTime(dtStr);
            return dt.toFormat("dd/MM/yyyy HH:mm:ss");
        } catch (e) {
            return String(dtStr);
        }
    }

    async loadDashboardData() {
        this.state.loading = true;
        try {
            const data = await this.orm.call("asset.dashboard", "get_ubuntu_dashboard_data", []);
            Object.assign(this.state, data);
            this.state.last_refresh = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true });
        } catch (error) {
            console.error("❌ Failed to load Ubuntu dashboard data:", error);
            this.notification.add("Failed to load Ubuntu dashboard data.", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    async refreshData() {
        await this.loadDashboardData();
        if (this.chartsRendered) {
            this.updateCharts();
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

    renderCharts() {
        if (!window.Chart) return;

        const safeDestroy = (canvasId) => {
            const existing = window.Chart.getChart(canvasId);
            if (existing) existing.destroy();
        };

        // Asset Status Donut
        const assetDonutEl = document.getElementById("ubuntu_asset_status_donut");
        if (assetDonutEl) {
            try {
                safeDestroy("ubuntu_asset_status_donut");
                const ctx = assetDonutEl.getContext('2d');
                this.charts.assetDonut = new Chart(ctx, {
                    type: "doughnut",
                    data: {
                        labels: ["Assigned", "Maintenance", "Scrapped", "Draft"],
                        datasets: [{
                            data: [
                                this.state.assigned,
                                this.state.maintenance,
                                this.state.scrapped,
                                this.state.draft,
                            ],
                            backgroundColor: ["#E95420", "#ffbf00", "#dc3545", "#333333"],
                            borderWidth: 3,
                            borderColor: "#ffffff",
                            hoverBorderWidth: 4,
                        }],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        cutout: "72%",
                        plugins: {
                            legend: {
                                position: "right",
                                labels: {
                                    boxWidth: 14,
                                    padding: 14,
                                    font: { size: 12, family: "'Inter', sans-serif" }
                                },
                            },
                            centerText: {
                                total: this.state.total || 0,
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
                                    }
                                }
                            }
                        },
                    },
                });
            } catch (error) {
                console.error("❌ Error rendering Ubuntu asset donut:", error);
            }
        }

        // Agent Status Donut
        const agentDonutEl = document.getElementById("ubuntu_agent_status_donut");
        if (agentDonutEl) {
            try {
                safeDestroy("ubuntu_agent_status_donut");
                const ctx = agentDonutEl.getContext('2d');
                const agentTotal = this.state.agent_stats.active +
                    this.state.agent_stats.offline +
                    this.state.agent_stats.never;

                this.charts.agentDonut = new Chart(ctx, {
                    type: "doughnut",
                    data: {
                        labels: ["Online", "Offline", "Never Synced"],
                        datasets: [{
                            data: [
                                this.state.agent_stats.active,
                                this.state.agent_stats.offline,
                                this.state.agent_stats.never,
                            ],
                            backgroundColor: ["#38B44A", "#ffbf00", "#333333"],
                            borderWidth: 3,
                            borderColor: "#ffffff",
                            hoverBorderWidth: 4,
                        }],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        cutout: "72%",
                        plugins: {
                            legend: {
                                position: "right",
                                labels: {
                                    boxWidth: 14,
                                    padding: 14,
                                    font: { size: 12, family: "'Inter', sans-serif" }
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
                                    }
                                }
                            }
                        },
                    },
                });
            } catch (error) {
                console.error("❌ Error rendering Ubuntu agent donut:", error);
            }
        }
    }

    updateCharts() {
        try {
            if (this.charts.assetDonut) {
                this.charts.assetDonut.data.datasets[0].data = [
                    this.state.assigned,
                    this.state.maintenance,
                    this.state.scrapped,
                    this.state.draft,
                ];
                this.charts.assetDonut.options.plugins.centerText.total = this.state.total;
                this.charts.assetDonut.update('none');
            }

            if (this.charts.agentDonut) {
                this.charts.agentDonut.data.datasets[0].data = [
                    this.state.agent_stats.active,
                    this.state.agent_stats.offline,
                    this.state.agent_stats.never,
                ];
                const agentTotal = this.state.agent_stats.active +
                    this.state.agent_stats.offline +
                    this.state.agent_stats.never;
                this.charts.agentDonut.options.plugins.centerText.total = agentTotal;
                this.charts.agentDonut.update('none');
            }
        } catch (error) {
            console.error("❌ Error updating Ubuntu charts:", error);
        }
    }

    startAutoRefresh() {
        if (this.state.auto_refresh) {
            this.refreshInterval = setInterval(() => {
                this.refreshData();
            }, 30000);
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
        } else {
            this.stopAutoRefresh();
        }
    }

    navigateToAssets(state = null) {
        const domain = [["os_platform", "=", "linux"]];
        if (state) domain.push(["state", "=", state]);
        this.action.doAction({
            type: "ir.actions.act_window",
            name: state ? `${state.charAt(0).toUpperCase() + state.slice(1)} Linux Assets` : "Linux Assets",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: domain,
            context: { default_os_platform: 'linux' }
        });
    }

    navigateToAlerts(severity = null) {
        const domain = [["os_platform", "=", "linux"], ["has_changes", "=", true]];
        if (severity) domain.push(["alert_severity", "=", severity]);
        this.action.doAction({
            type: "ir.actions.act_window",
            name: severity ? `${severity.charAt(0).toUpperCase() + severity.slice(1)} Linux Alerts` : "Linux Alerts",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: domain,
        });
    }

    openAgentStatus(status) {
        const domain = [["os_platform", "=", "linux"]];
        if (status === 'online') domain.push(["agent_status", "=", "online"]);
        else if (status === 'offline') domain.push(["agent_status", "=", "offline"]);
        else if (status === 'never') domain.push(["last_sync_time", "=", false]);

        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${status.charAt(0).toUpperCase() + status.slice(1)} Linux Agents`,
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: domain,
        });
    }

    openAsset(assetId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "asset.asset",
            res_id: assetId,
            view_mode: "form",
            views: [[false, "form"]],
            target: "current",
        });
    }

    openAgentLogs() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Linux Agent Sync Logs",
            res_model: "asset.agent.log",
            view_mode: "tree,form",
            views: [[false, "list"], [false, "form"]],
            domain: [["asset_id.os_platform", "=", "linux"]],
        });
    }

    toggleRiskCompliance() {
        this.state.risk_compliance_expanded = !this.state.risk_compliance_expanded;
    }

    _identifyAssetType(asset) {
        if (asset.asset_type) return asset.asset_type;
        if (asset.is_camera) return 'camera';
        const name = (asset.asset_name || '').toLowerCase();
        if (name.includes('cctv') || name.includes('camera') || name.includes('cam')) return 'camera';
        if (name.includes('switch')) return 'switch';
        if (name.includes('router')) return 'router';
        return 'laptop';
    }

    resolveAssetImage(asset) {
        if (asset.has_image) {
            const model = asset.res_model || 'asset.asset';
            const id = asset.id || asset.asset_id;
            const field = model === 'asset.camera' ? 'camera_image' : 'image_1920';
            return `/web/image/${model}/${id}/${field}`;
        }
        const type = this._identifyAssetType(asset);
        const fallbacks = {
            'camera': '/asset_management/static/src/img/asset_cctv_default.png',
            'laptop': '/asset_management/static/src/img/asset_laptop_default.png',
            'switch': '/asset_management/static/src/img/asset_laptop_default.png',
            'router': '/asset_management/static/src/img/asset_laptop_default.png',
        };
        return fallbacks[type] || fallbacks['laptop'];
    }

    async downloadPDF() {
        this.state.loading = true;
        try {
            const reportId = await this.orm.create("asset.reports", [{
                report_type: 'dashboard',
            }]);

            await this.action.doAction({
                type: 'ir.actions.report',
                report_type: 'qweb-pdf',
                report_name: 'asset_management.report_asset_template',
                report_file: 'asset_management.report_asset_template',
                data: {
                    model: 'asset.reports',
                    ids: [reportId],
                },
                context: {
                    active_ids: [reportId],
                }
            });
        } catch (error) {
            console.error("❌ Failed to generate PDF:", error);
            this.notification.add("Failed to generate PDF report.", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }
}

UbuntuAgentDashboard.template = "asset_management.UbuntuAgentDashboard";
registry.category("actions").add("ubuntu_agent_dashboard", UbuntuAgentDashboard);
console.log("✅ UbuntuAgentDashboard component registered");