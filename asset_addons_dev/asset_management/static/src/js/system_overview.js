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

class SystemOverview extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            total_assets: 0,
            windows_count: 0,
            windows_online: 0,
            windows_offline: 0,
            linux_count: 0,
            linux_online: 0,
            linux_offline: 0,
            mac_count: 0,
            mac_online: 0,
            mac_offline: 0,
            camera_count: 0,
            camera_online: 0,
            camera_offline: 0,
            network_count: 0,
            network_online: 0,
            network_offline: 0,
            online_agents: 0,
            offline_agents: 0,
            critical_alerts_count: 0,
            warning_alerts_count: 0,
            info_alerts_count: 0,
            total_alerts_count: 0,
            windows_alerts_count: 0,
            linux_alerts_count: 0,
            mac_alerts_count: 0,
            cctv_alerts_count: 0,
            network_alerts_count: 0,
            health_status: 'healthy',
            recent_activity: [],
            all_alerts: [],
            loading: true,
            last_refresh: null,
            auto_refresh: true,
        });

        this.charts = {};
        this.chartsRendered = false;
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
            if (!window.Chart.registry.plugins.get('donutCenterText')) {
                window.Chart.register(donutCenterTextPlugin);
            }
        }
    }

    async loadData() {
        this.state.loading = true;
        try {
            const data = await this.orm.call("asset.dashboard", "get_system_overview_data", []);
            Object.assign(this.state, data);
            this.state.last_refresh = luxon.DateTime.now().toFormat("dd/MM/yyyy HH:mm:ss");

            if (this.chartsRendered) {
                this.updateCharts();
            }
        } catch (error) {
            console.error("Failed to load system overview data:", error);
        } finally {
            this.state.loading = false;
        }
    }

    async refreshData() {
        await this.loadData();
    }

    renderCharts() {
        if (!window.Chart) return;

        // Helper: destroy any existing Chart.js instance on a canvas before reusing it
        const safeDestroy = (canvasId) => {
            const existing = window.Chart.getChart(canvasId);
            if (existing) existing.destroy();
        };

        // 1. Asset Distribution Donut - Colors match "Total" cards
        const distEl = document.getElementById("asset_distribution_donut");
        if (distEl) {
            safeDestroy("asset_distribution_donut");
            const ctx = distEl.getContext('2d');
            this.charts.distribution = new Chart(ctx, {
                type: "doughnut",
                data: {
                    labels: ["Windows", "Linux", "macOS", "CCTV", "Network"],
                    datasets: [{
                        data: [
                            this.state.windows_count || 0,
                            this.state.linux_count || 0,
                            this.state.mac_count || 0,
                            this.state.camera_count || 0,
                            this.state.network_count || 0,
                        ],
                        // Colors match Total Windows, Total Linux, Total macOS, Total CCTV, Total Network
                        backgroundColor: ["#0057D9", "#E95420", "#E91E63", "#009688", "#8E24AA"],
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
                                boxWidth: 15,
                                padding: 8,
                                font: { size: 14 }
                            }
                        },
                        centerText: {
                            total: this.state.total_assets || 0,
                            label: "Total Assets",
                        }
                    },
                },
            });
        }

        // 2. Global Agent Status Donut - Colors match Online/Offline cards
        const statusEl = document.getElementById("agent_status_global_donut");
        if (statusEl) {
            safeDestroy("agent_status_global_donut");
            const ctx = statusEl.getContext('2d');
            this.charts.status = new Chart(ctx, {
                type: "doughnut",
                data: {
                    labels: [
                        "Windows Online", "Windows Offline",
                        "Linux Online", "Linux Offline",
                        "macOS Online", "macOS Offline",
                        "CCTV Online", "CCTV Offline",
                        "Network Online", "Network Offline"
                    ],
                    datasets: [{
                        data: [
                            this.state.windows_online || 0,
                            this.state.windows_offline || 0,
                            this.state.linux_online || 0,
                            this.state.linux_offline || 0,
                            this.state.mac_online || 0,
                            this.state.mac_offline || 0,
                            this.state.camera_online || 0,
                            this.state.camera_offline || 0,
                            this.state.network_online || 0,
                            this.state.network_offline || 0,
                        ],
                        // Colors match card colors: Online/Offline for each category
                        backgroundColor: [
                            "#00B4FF", "#F4B400", // Windows Online (Azure Cyan), Windows Offline (System Yellow)
                            "#2ECC71", "#dc3545", // Linux Online (Open-source Green), Linux Offline (Red)
                            "#C2185B", "#F06292", // macOS Online (Rose Online), macOS Offline (Rose Offline)
                            "#7B3FE4", "#E95420", // CCTV Online (Purple), CCTV Offline (Orange)
                            "#00BFA5", "#95A753"  // Network Online (Teal Green), Network Offline (Olive Green)
                        ],
                        borderWidth: 2,
                        borderColor: "#ffffff",
                        hoverBorderWidth: 3,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: "70%",
                    plugins: {
                        legend: {
                            position: "right",
                            labels: {
                                boxWidth: 15,
                                padding: 8,
                                font: { size: 13 }
                            }
                        },
                        centerText: {
                            total: this.state.total_assets || 0,
                            label: "Total Assets",
                        }
                    },
                },
            });
        }
    }

    updateCharts() {
        if (this.charts.distribution) {
            this.charts.distribution.data.datasets[0].data = [
                this.state.windows_count || 0,
                this.state.linux_count || 0,
                this.state.mac_count || 0,
                this.state.camera_count || 0,
                this.state.network_count || 0,
            ];
            this.charts.distribution.options.plugins.centerText.total = this.state.total_assets || 0;
            this.charts.distribution.update('none');
        }

        if (this.charts.status) {
            this.charts.status.data.datasets[0].data = [
                this.state.windows_online || 0,
                this.state.windows_offline || 0,
                this.state.linux_online || 0,
                this.state.linux_offline || 0,
                this.state.mac_online || 0,
                this.state.mac_offline || 0,
                this.state.camera_online || 0,
                this.state.camera_offline || 0,
                this.state.network_online || 0,
                this.state.network_offline || 0,
            ];
            this.charts.status.options.plugins.centerText.total = this.state.total_assets || 0;
            this.charts.status.update('none');
        }
    }

    destroyCharts() {
        Object.values(this.charts).forEach(chart => {
            if (chart) chart.destroy();
        });
        this.charts = {};
        this.chartsRendered = false;
    }

    startAutoRefresh() {
        if (this.state.auto_refresh) {
            this.refreshInterval = setInterval(() => {
                this.refreshData();
            }, 60000);
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

    // Quick Actions
    async approveAllChanges() {
        if (!confirm('Approve all pending changes across all assets?')) return;

        try {
            const assets = await this.orm.search("asset.asset", [["has_changes", "=", true]]);
            if (assets.length === 0) {
                this.notification.add("No pending changes found", { type: "info" });
                return;
            }
            await this.orm.call("asset.asset", "action_bulk_approve_changes", [assets]);
            this.notification.add(`Successfully approved changes for ${assets.length} assets`, { type: "success" });
            await this.refreshData();
        } catch (error) {
            console.error("Failed to approve all changes:", error);
            this.notification.add("Failed to approve changes", { type: "danger" });
        }
    }

    createMaintenanceSchedule() {
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

    openAgentLogs() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Agent Sync Logs",
            res_model: "asset.agent.log",
            view_mode: "tree,form",
            views: [[false, "list"], [false, "form"]],
        });
    }

    async downloadPDF() {
        window.print();
    }

    // Navigation methods
    async openAllAssets() {
        // Open the All Assets Selection Wizard in FULL SCREEN mode
        // This is needed because CCTV and Network are stored in separate models
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'View All Assets',
            res_model: 'asset.all.assets.wizard',
            view_mode: 'form',
            views: [[false, 'form']],
            target: 'current',  // Full screen instead of 'new' (popup)
            context: {
                default_windows_count: this.state.windows_count,
                default_linux_count: this.state.linux_count,
                default_mac_count: this.state.mac_count,
                default_cctv_count: this.state.camera_count,
                default_network_count: this.state.network_count,
                default_total_count: this.state.total_assets,
            }
        });
    }

    openWindowsAssets() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Windows Assets',
            res_model: 'asset.asset',
            domain: [['os_platform', '=', 'windows']],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    openLinuxAssets() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Linux Assets',
            res_model: 'asset.asset',
            domain: [['os_platform', '=', 'linux']],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    openCCTVDevices() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'CCTV Devices',
            res_model: 'asset.camera',
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    openNetworkDevices() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Network Devices',
            res_model: 'asset.network.device',
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    openOnlineAgents() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'View Online Assets',
            res_model: 'asset.online.assets.wizard',
            view_mode: 'form',
            views: [[false, 'form']],
            target: 'current',
            context: {
                default_windows_online: this.state.windows_online,
                default_linux_online: this.state.linux_online,
                default_mac_online: this.state.mac_online,
                default_cctv_online: this.state.camera_online,
                default_network_online: this.state.network_online,
                default_total_online: this.state.online_agents,
            }
        });
    }

    openOfflineAgents() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'View Offline Assets',
            res_model: 'asset.offline.assets.wizard',
            view_mode: 'form',
            views: [[false, 'form']],
            target: 'current',
            context: {
                default_windows_offline: this.state.windows_offline,
                default_linux_offline: this.state.linux_offline,
                default_mac_offline: this.state.mac_offline,
                default_cctv_offline: this.state.camera_offline,
                default_network_offline: this.state.network_offline,
                default_total_offline: this.state.offline_agents,
            }
        });
    }

    async openWindowsAssetsOnline() {
        // Get heartbeat timeout and calculate cutoff time
        const heartbeatTimeout = await this.orm.call('ir.config_parameter', 'get_param', ['asset_management.agent_heartbeat_timeout'], { default: '180' });
        const timeout = parseInt(heartbeatTimeout, 10) || 180;
        const cutoffTime = new Date();
        cutoffTime.setSeconds(cutoffTime.getSeconds() - timeout);

        // Format cutoff time as Odoo datetime string
        const cutoffTimeStr = cutoffTime.toISOString().slice(0, 19).replace('T', ' ');

        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Windows Assets (Online)',
            res_model: 'asset.asset',
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            domain: [
                ['os_platform', '=', 'windows'],
                ['last_sync_time', '!=', false],
                ['last_sync_time', '>=', cutoffTimeStr]
            ],
            target: 'current',
        });
    }

    async openLinuxAssetsOnline() {
        const heartbeatTimeout = await this.orm.call('ir.config_parameter', 'get_param', ['asset_management.agent_heartbeat_timeout'], { default: '180' });
        const timeout = parseInt(heartbeatTimeout, 10) || 180;
        const cutoffTime = new Date();
        cutoffTime.setSeconds(cutoffTime.getSeconds() - timeout);
        const cutoffTimeStr = cutoffTime.toISOString().slice(0, 19).replace('T', ' ');

        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Linux Assets (Online)',
            res_model: 'asset.asset',
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            domain: [
                ['os_platform', '=', 'linux'],
                ['last_sync_time', '!=', false],
                ['last_sync_time', '>=', cutoffTimeStr]
            ],
            target: 'current',
        });
    }

    async openMacOSAssetsOnline() {
        const heartbeatTimeout = await this.orm.call('ir.config_parameter', 'get_param', ['asset_management.agent_heartbeat_timeout'], { default: '180' });
        const timeout = parseInt(heartbeatTimeout, 10) || 180;
        const cutoffTime = new Date();
        cutoffTime.setSeconds(cutoffTime.getSeconds() - timeout);
        const cutoffTimeStr = cutoffTime.toISOString().slice(0, 19).replace('T', ' ');

        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'macOS Assets (Online)',
            res_model: 'asset.asset',
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            domain: [
                ['os_platform', '=', 'macos'],
                ['last_sync_time', '!=', false],
                ['last_sync_time', '>=', cutoffTimeStr]
            ],
            target: 'current',
        });
    }

    openWindowsAssetsOffline() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Windows Assets (Offline)',
            res_model: 'asset.asset',
            domain: [['os_platform', '=', 'windows'], ['agent_status', '=', 'offline']],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    openLinuxAssetsOffline() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Linux Assets (Offline)',
            res_model: 'asset.asset',
            domain: [['os_platform', '=', 'linux'], ['agent_status', '=', 'offline']],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    openMacOSAssets() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'macOS Assets',
            res_model: 'asset.asset',
            domain: [['os_platform', '=', 'macos']],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    openMacOSAssetsOffline() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'macOS Assets (Offline)',
            res_model: 'asset.asset',
            domain: [['os_platform', '=', 'macos'], ['agent_status', '=', 'offline']],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    openMacOSAlerts() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'macOS Alerts',
            res_model: 'asset.asset',
            domain: [['has_changes', '=', true], ['os_platform', '=', 'macos']],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    openCCTVDevicesOnline() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'CCTV Devices (Online)',
            res_model: 'asset.camera',
            domain: [['status', '=', 'online']],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    openCCTVDevicesOffline() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'CCTV Devices (Offline)',
            res_model: 'asset.camera',
            domain: [['status', '!=', 'online']],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    openNetworkDevicesOnline() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Network Devices (Online)',
            res_model: 'asset.network.device',
            domain: [['connection_status', '=', 'online']],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    openNetworkDevicesOffline() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Network Devices (Offline)',
            res_model: 'asset.network.device',
            domain: [['connection_status', '!=', 'online']],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    openCriticalAlerts() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Critical Alerts',
            res_model: 'asset.asset',
            domain: [['has_changes', '=', true], ['alert_severity', '=', 'critical']],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    openAllAlerts() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'View All Alerts',
            res_model: 'asset.alerts.wizard',
            view_mode: 'form',
            views: [[false, 'form']],
            target: 'current',
            context: {
                default_windows_alerts: this.state.windows_alerts_count,
                default_linux_alerts: this.state.linux_alerts_count,
                default_mac_alerts: this.state.mac_alerts_count,
                default_cctv_alerts: this.state.cctv_alerts_count,
                default_network_alerts: this.state.network_alerts_count,
                default_total_alerts: this.state.total_alerts_count,
                default_critical_alerts: this.state.critical_alerts_count,
                default_warning_alerts: this.state.warning_alerts_count,
                default_info_alerts: this.state.info_alerts_count,
            }
        });
    }

    openWindowsAlerts() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Windows Alerts',
            res_model: 'asset.asset',
            domain: [['has_changes', '=', true], ['os_platform', '=', 'windows']],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    openLinuxAlerts() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Linux Alerts',
            res_model: 'asset.asset',
            domain: [['has_changes', '=', true], ['os_platform', '=', 'linux']],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    openCCTVAlerts() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'CCTV Alerts',
            res_model: 'asset.camera',
            domain: [['has_changes', '=', true]],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    openNetworkAlerts() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Network Alerts',
            res_model: 'asset.network.device',
            domain: [['has_changes', '=', true]],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    openAsset(assetId) {
        this.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'asset.asset',
            res_id: assetId,
            view_mode: 'form',
            views: [[false, 'form']],
            target: 'current',
        });
    }

    resolveAssetImage(asset) {
        if (asset.has_image) {
            const model = asset.res_model || 'asset.asset';
            const id = asset.id || asset.asset_id;
            const field = model === 'asset.camera' ? 'camera_image' : 'image_1920';
            return `/web/image/${model}/${id}/${field}`;
        }

        // Fallback to default laptop image
        return '/asset_management/static/src/img/asset_laptop_default.png';
    }
}

SystemOverview.template = "asset_management.SystemOverview";

registry.category("actions").add("asset_management.system_overview", SystemOverview);
