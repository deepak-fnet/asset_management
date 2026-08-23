/** @odoo-module **/

import { Component, useState, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatDateTime, deserializeDateTime } from "@web/core/l10n/dates";
import { ListRenderer } from "@web/views/list/list_renderer";
import { patch } from "@web/core/utils/patch";

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

/* =========================
   ANTIVIRUS DASHBOARD COMPONENT
   ========================= */
class AntivirusDashboard extends Component {

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            total_devices: 0,
            windows_count: 0,
            linux_count: 0,
            macos_count: 0,
            protected_count: 0,
            unprotected_count: 0,
            threats_count: 0,
            threats_quarantined: 0,
            total_license: 0,
            balance_license: 0,
            expiring_soon: 0,
            threat_list: [],
            recent_scans: [],
            loading: true,
            last_refresh: null,
            auto_refresh: true,
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
            const kpis = await this.orm.call("antivirus.dashboard", "get_kpis", []);
            Object.assign(this.state, kpis);

            const threats = await this.orm.call("antivirus.dashboard", "get_threats", []);
            this.state.threat_list = threats || [];

            const scans = await this.orm.call("antivirus.dashboard", "get_recent_scans", []);
            this.state.recent_scans = scans || [];

            this.state.last_refresh = luxon.DateTime.now().toFormat("dd/MM/yyyy HH:mm:ss");
            console.log("✅ Antivirus dashboard data loaded");

        } catch (error) {
            console.error("❌ Failed to load antivirus dashboard data:", error);
            this.notification.add("Failed to load antivirus dashboard data.", { type: "danger" });
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
    // 🎯 CLICKABLE KPI ACTIONS
    // =====================================================

    openAllDevices() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "All Devices",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
        });
    }

    openDevicesByPlatform(platform) {
        const platformLabels = {
            'windows': 'Windows',
            'linux': 'Linux',
            'macos': 'macOS',
        };
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${platformLabels[platform] || platform} Devices`,
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["os_platform", "=", platform]],
        });
    }

    openProtectedDevices() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Protected Devices",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["antivirus_status", "=", "protected"]],
        });
    }

    openUnprotectedDevices() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Unprotected Devices",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["antivirus_status", "in", ["unprotected", "expired"]]],
        });
    }

    openThreats() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Detected Threats",
            res_model: "antivirus.threat",
            view_mode: "tree,form",
            views: [[false, "list"], [false, "form"]],
        });
    }

    openLicenseDetails() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "License Details",
            res_model: "antivirus.license",
            view_mode: "form",
            views: [[false, "form"]],
        });
    }

    openBalanceLicense() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Available Licenses",
            res_model: "antivirus.license",
            view_mode: "form",
            views: [[false, "form"]],
        });
    }

    openExpiringLicenses() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Expiring Licenses",
            res_model: "antivirus.license",
            view_mode: "tree,form",
            views: [[false, "list"], [false, "form"]],
            domain: [["expiry_date", "<=", (new Date(Date.now() + 30 * 24 * 60 * 60 * 1000)).toISOString().split('T')[0]]],
        });
    }

    openDevice(deviceId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Device",
            res_model: "asset.asset",
            res_id: deviceId,
            view_mode: "form",
            views: [[false, "form"]],
        });
    }

    openScanLogs() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Scan Logs",
            res_model: "antivirus.scan.log",
            view_mode: "tree,form",
            views: [[false, "list"], [false, "form"]],
        });
    }

    async downloadPDF() {
        window.print();
    }

    // =====================================================
    // 🖼️ IMAGE & ICON RESOLVERS
    // =====================================================

    resolveDeviceImage(device) {
        if (device.has_image) {
            const model = device.res_model || 'asset.asset';
            const id = device.id || device.device_id;
            return `/web/image/${model}/${id}/image_1920`;
        }
        return '/asset_management/static/src/img/asset_laptop_default.png';
    }

    resolveThreatIcon(threat) {
        const iconMap = {
            'virus': 'fa-bug',
            'malware': 'fa-exclamation-circle',
            'ransomware': 'fa-lock',
            'spyware': 'fa-eye',
            'trojan': 'fa-shield',
            'default': 'fa-bug',
        };
        const threatType = (threat.threat_type || 'default').toLowerCase();
        return iconMap[threatType] || iconMap['default'];
    }

    // =====================================================
    // ⚡ QUICK ACTIONS
    // =====================================================

    async runFullScan() {
        if (!confirm('Run full system scan on all devices?')) return;

        try {
            await this.orm.call("antivirus.dashboard", "run_full_scan", []);
            this.notification.add("Full scan initiated successfully", { type: "success" });
            await this.refreshData();
        } catch (error) {
            this.notification.add("Failed to initiate scan", { type: "danger" });
        }
    }

    async updateDefinitions() {
        try {
            await this.orm.call("antivirus.dashboard", "update_definitions", []);
            this.notification.add("Virus definitions updated successfully", { type: "success" });
            await this.refreshData();
        } catch (error) {
            this.notification.add("Failed to update definitions", { type: "danger" });
        }
    }

    async quarantineAll() {
        if (!confirm('Quarantine all detected threats?')) return;

        try {
            await this.orm.call("antivirus.dashboard", "quarantine_all", []);
            this.notification.add("All threats quarantined successfully", { type: "success" });
            await this.refreshData();
        } catch (error) {
            this.notification.add("Failed to quarantine threats", { type: "danger" });
        }
    }

    // =====================================================
    // 📊 CHART UPDATES
    // =====================================================

    updateCharts() {
        try {
            if (this.charts.protectionDonut) {
                const prot   = this.state.protected_count || 0;
                const unprot = this.state.unprotected_count || 0;
                const hasData = prot > 0 || unprot > 0;

                this.charts.protectionDonut.data.datasets[0].data =
                    hasData ? [prot, unprot] : [1];
                this.charts.protectionDonut.data.datasets[0].backgroundColor =
                    hasData ? ["#198754", "#dc3545"] : ["#e5e7eb"];
                this.charts.protectionDonut.data.datasets[0].borderWidth =
                    hasData ? 3 : 0;
                this.charts.protectionDonut.options.plugins.legend.display = hasData;
                this.charts.protectionDonut.options.plugins.tooltip.enabled = hasData;
                this.charts.protectionDonut.options.plugins.centerText.total =
                    (this.state.protected_count || 0) + (this.state.unprotected_count || 0);
                this.charts.protectionDonut.update('none');
            }

            if (this.charts.licenseDonut) {
                // Use used_license returned directly by get_kpis – do NOT recompute
                // from total - balance, because available_licenses is already clamped
                // to max(0, total - used) on the server side.
                const totalLicense = this.state.total_license || 0;
                const usedLicense  = this.state.used_license  || 0;
                const availLicense = this.state.balance_license || 0;
                const hasData = totalLicense > 0;

                this.charts.licenseDonut.data.datasets[0].data =
                    hasData ? [usedLicense, availLicense] : [1];
                this.charts.licenseDonut.data.datasets[0].backgroundColor =
                    hasData ? ["#0d6efd", "#6610f2"] : ["#e5e7eb"];
                this.charts.licenseDonut.data.datasets[0].borderWidth =
                    hasData ? 3 : 0;
                this.charts.licenseDonut.options.plugins.legend.display = hasData;
                this.charts.licenseDonut.options.plugins.tooltip.enabled = hasData;
                this.charts.licenseDonut.options.plugins.centerText.total = totalLicense;
                this.charts.licenseDonut.update('none');
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

        this.destroyCharts();

        const safeDestroy = (canvasId) => {
            const existing = window.Chart.getChart(canvasId);
            if (existing) existing.destroy();
        };

        console.log("🎨 Rendering antivirus charts...");

        // ── Protection Status Donut ──────────────────────────────────────
        const protectionDonut = document.getElementById("protection_status_donut");
        if (protectionDonut) {
            try {
                safeDestroy("protection_status_donut");
                const ctx     = protectionDonut.getContext('2d');
                const prot    = this.state.protected_count   || 0;
                const unprot  = this.state.unprotected_count || 0;
                // Use prot+unprot so the center total is always consistent with
                // the hasData check — total_devices can be 0 even when prot/unprot
                // are non-zero (causing colored segments with "0" in the centre).
                const total   = prot + unprot;
                const hasData = total > 0;

                this.charts.protectionDonut = new Chart(ctx, {
                    type: "doughnut",
                    data: {
                        labels: ["Protected", "Unprotected"],
                        datasets: [{
                            data: hasData ? [prot, unprot] : [1],
                            backgroundColor: hasData ? ["#198754", "#dc3545"] : ["#e5e7eb"],
                            borderWidth: hasData ? 3 : 0,
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
                                display: hasData,
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
                                label: "Devices",
                            },
                            tooltip: {
                                enabled: hasData,
                                callbacks: {
                                    label: function (context) {
                                        const label = context.label || '';
                                        const value = context.parsed || 0;
                                        const sum   = context.dataset.data.reduce((a, b) => a + b, 0);
                                        const pct   = sum > 0 ? ((value / sum) * 100).toFixed(1) : 0;
                                        return `${label}: ${value} (${pct}%)`;
                                    }
                                }
                            }
                        },
                    },
                });
                console.log("✅ Protection status donut rendered");
            } catch (error) {
                console.error("❌ Error rendering protection status donut:", error);
            }
        }

        // ── License Utilisation Donut ────────────────────────────────────
        const licenseDonut = document.getElementById("license_donut");
        if (licenseDonut) {
            try {
                safeDestroy("license_donut");
                const ctx          = licenseDonut.getContext('2d');
                const totalLicense = this.state.total_license   || 0;
                // Use used_license directly – server already computes it correctly.
                // Do NOT re-derive it as (total - balance) because available_licenses
                // is clamped at max(0, total - used), which loses precision when used > total.
                const usedLicense  = this.state.used_license    || 0;
                const availLicense = this.state.balance_license || 0;
                const hasData      = totalLicense > 0;

                this.charts.licenseDonut = new Chart(ctx, {
                    type: "doughnut",
                    data: {
                        labels: ["Used", "Available"],
                        datasets: [{
                            data: hasData ? [usedLicense, availLicense] : [1],
                            backgroundColor: hasData ? ["#0d6efd", "#6610f2"] : ["#e5e7eb"],
                            borderWidth: hasData ? 3 : 0,
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
                                display: hasData,
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
                                total: totalLicense,
                                label: "Licenses",
                            },
                            tooltip: {
                                enabled: hasData,
                                callbacks: {
                                    label: function (context) {
                                        const label = context.label || '';
                                        const value = context.parsed || 0;
                                        const sum   = context.dataset.data.reduce((a, b) => a + b, 0);
                                        const pct   = sum > 0 ? ((value / sum) * 100).toFixed(1) : 0;
                                        return `${label}: ${value} (${pct}%)`;
                                    }
                                }
                            }
                        },
                    },
                });
                console.log("✅ License donut rendered");
            } catch (error) {
                console.error("❌ Error rendering license donut:", error);
            }
        }

        this.chartsRendered = true;
    }
}

AntivirusDashboard.template = "antivirus_management.AntivirusDashboard";
registry.category("actions").add("antivirus_dashboard", AntivirusDashboard);

/* =========================
   ANTIVIRUS UNIFIED DASHBOARD COMPONENT
   ========================= */
class AntivirusUnifiedDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        const context = this.props.action?.context || {};
        const defaultPlatform = context.default_os_platform || 'windows';
        const isUnified = !context.default_os_platform;

        this.state = useState({
            config: {
                installer_windows: '',
                installer_linux: '',
                installer_macos: '',
            },
            assets: [],
            platform: defaultPlatform,
            isUnified: isUnified,
            search: '',
            page: 1,
            pageSize: 10,
            selectAll: false,
            selectedAssets: new Set(),
            kpis: {
                total_license: 0,
                used_licenses: 0,
                balance_license: 0,
                threats_count: 0,
                protected_count: 0,
                unprotected_count: 0,
            }
        });

        onWillStart(async () => {
            await Promise.all([
                this.loadConfig(),
                this.loadAssets(),
                this.loadKPIs(),
            ]);
        });
    }

    /* =========================
       GETTERS
       ========================= */
    get filteredAssets() {
        let assets = this.state.assets.filter(a => a.os_platform === this.state.platform);

        if (this.state.search) {
            const searchLower = this.state.search.toLowerCase();
            assets = assets.filter(a =>
                a.asset_name.toLowerCase().includes(searchLower) ||
                a.asset_code.toLowerCase().includes(searchLower) ||
                a.serial_number.toLowerCase().includes(searchLower)
            );
        }

        return assets;
    }

    /* =========================
       DATA LOADING
       ========================= */
    async loadConfig() {
        try {
            const configs = await this.orm.searchRead('antivirus.config', [], ['installer_windows', 'installer_linux', 'installer_macos'], { limit: 1 });
            if (configs.length > 0) {
                const config = configs[0];
                this.state.config.installer_windows = config.installer_windows || '';
                this.state.config.installer_linux = config.installer_linux || '';
                this.state.config.installer_macos = config.installer_macos || '';
            }
        } catch (error) {
            console.error('Error loading config:', error);
        }
    }

    async loadAssets() {
        try {
            const domain = this.state.isUnified ? [] : [['os_platform', '=', this.state.platform]];
            this.state.assets = await this.orm.searchRead('asset.asset', domain, [
                'asset_code',
                'asset_name',
                'serial_number',
                'os_platform',
                'agent_status',
                'antivirus_status'
            ]);
        } catch (error) {
            console.error('Error loading assets:', error);
            this.notification.add("Error loading assets", { type: 'danger' });
        }
    }

    async loadKPIs() {
        try {
            const kpis = await this.orm.call('antivirus.dashboard', 'get_kpis', []);
            this.state.kpis = {
                total_license: kpis.total_license || 0,
                used_licenses: kpis.protected_count || 0, // Using protected devices as used licenses for now
                balance_license: kpis.balance_license || 0,
                threats_count: kpis.threats_count || 0,
                protected_count: kpis.protected_count || 0,
                unprotected_count: kpis.unprotected_count || 0,
            };
        } catch (error) {
            console.error('Error loading KPIs:', error);
        }
    }

    async refreshAssets() {
        this.notification.add("Refreshing assets...", { type: 'info' });
        await Promise.all([
            this.loadAssets(),
            this.loadKPIs(),
        ]);
        this.notification.add("Dashboard refreshed", { type: 'success' });
    }

    /* =========================
       ACTIONS
       ========================= */
    async saveConfig(platform) {
        try {
            const configId = await this.orm.search('antivirus.config', [], { limit: 1 });
            const values = {};

            if (platform === 'windows') {
                values.installer_windows = this.state.config.installer_windows;
            } else if (platform === 'linux') {
                values.installer_linux = this.state.config.installer_linux;
            } else if (platform === 'macos') {
                values.installer_macos = this.state.config.installer_macos;
            }

            if (configId.length > 0) {
                await this.orm.write('antivirus.config', configId, values);
            } else {
                values.name = 'Default Configuration';
                values.antivirus_product = 'kaspersky';
                await this.orm.create('antivirus.config', values);
            }

            this.notification.add(`${platform.charAt(0).toUpperCase() + platform.slice(1)} configuration saved!`, { type: 'success' });
        } catch (error) {
            console.error('Error saving config:', error);
            this.notification.add("Error saving configuration", { type: 'danger' });
        }
    }

    switchPlatform(platform) {
        this.state.platform = platform;
        this.state.page = 1;
    }

    async deployAntivirus(assetId) {
        try {
            await this.orm.call('asset.asset', 'action_deploy_antivirus', [[assetId]]);
            this.notification.add("Antivirus deployment initiated", { type: 'success' });
            await this.loadAssets();
        } catch (error) {
            console.error('Error deploying antivirus:', error);
            this.notification.add("Error deploying antivirus", { type: 'danger' });
        }
    }

    async removeAntivirus(assetId) {
        try {
            await this.orm.call('asset.asset', 'action_remove_antivirus', [[assetId]]);
            this.notification.add("Antivirus removal initiated", { type: 'success' });
            await this.loadAssets();
        } catch (error) {
            console.error('Error removing antivirus:', error);
            this.notification.add("Error removing antivirus", { type: 'danger' });
        }
    }

    previousPage() {
        if (this.state.page > 1) {
            this.state.page--;
        }
    }

    nextPage() {
        if (this.state.page * this.state.pageSize < this.state.assets.length) {
            this.state.page++;
        }
    }

    toggleSelectAll() {
        this.state.selectAll = !this.state.selectAll;
        if (this.state.selectAll) {
            // Select all filtered assets
            this.filteredAssets.forEach(asset => {
                this.state.selectedAssets.add(asset.id);
            });
        } else {
            // Deselect all
            this.state.selectedAssets.clear();
        }
    }

    toggleAssetSelection(assetId) {
        if (this.state.selectedAssets.has(assetId)) {
            this.state.selectedAssets.delete(assetId);
            this.state.selectAll = false;
        } else {
            this.state.selectedAssets.add(assetId);
            // Check if all are selected
            const filteredAssets = this.filteredAssets;
            if (this.state.selectedAssets.size === filteredAssets.length) {
                this.state.selectAll = true;
            }
        }
    }

    async deploySelected() {
        if (this.state.selectedAssets.size === 0) {
            this.notification.add("Please select at least one asset", { type: 'warning' });
            return;
        }

        const selectedIds = Array.from(this.state.selectedAssets);
        let successCount = 0;
        let failCount = 0;

        for (const assetId of selectedIds) {
            try {
                await this.orm.call('asset.asset', 'action_deploy_antivirus', [[assetId]]);
                successCount++;
            } catch (error) {
                console.error('Error deploying to asset:', assetId, error);
                failCount++;
            }
        }

        // Clear selection after deployment
        this.state.selectedAssets.clear();
        this.state.selectAll = false;

        // Refresh assets list
        await this.loadAssets();

        // Show result notification
        let message = `Deployed to ${successCount} asset(s)`;
        if (failCount > 0) {
            message += ` (${failCount} failed)`;
        }
        this.notification.add(message, { type: successCount > 0 ? 'success' : 'danger' });
    }

    async deployAll() {
        const assetsToDeploy = this.filteredAssets.filter(a => a.antivirus_status !== 'protected');

        if (assetsToDeploy.length === 0) {
            this.notification.add("All assets are already protected!", { type: 'info' });
            return;
        }

        let successCount = 0;
        let failCount = 0;

        for (const asset of assetsToDeploy) {
            try {
                await this.orm.call('asset.asset', 'action_deploy_antivirus', [[asset.id]]);
                successCount++;
            } catch (error) {
                console.error('Error deploying to asset:', asset.id, error);
                failCount++;
            }
        }

        // Refresh assets list
        await this.loadAssets();

        // Show result notification
        let message = `Deployed to ${successCount} asset(s)`;
        if (failCount > 0) {
            message += ` (${failCount} failed)`;
        }
        this.notification.add(message, { type: successCount > 0 ? 'success' : 'danger' });
    }
}

AntivirusUnifiedDashboard.template = "antivirus.UnifiedDashboard";
AntivirusUnifiedDashboard.props = {
    action: { type: Object, optional: true },
    actionId: { type: [Number, String], optional: true },
    updateActionState: { type: Function, optional: true },
    className: { type: String, optional: true },
};

registry.category("actions").add("antivirus_unified_dashboard", AntivirusUnifiedDashboard);


/* =========================
   ANTIVIRUS ASSETS TABS - LIST RENDERER PATCH
   ========================= */
console.log("[Antivirus] antivirus_assets_tabs.js loaded");

patch(ListRenderer.prototype, {
    setup() {
        console.log("[Antivirus] ListRenderer setup started");
        super.setup(...arguments);
        this.action = useService("action");

        this.tabs = [
            { id: 'windows', label: 'Windows', icon: 'fa-windows', color: '#0078d4', action: 'asset_management.action_antivirus_assets_windows' },
            { id: 'linux', label: 'Linux', icon: 'fa-linux', color: '#f04e23', action: 'asset_management.action_antivirus_assets_linux' },
            { id: 'macos', label: 'macOS', icon: 'fa-apple', color: '#555555', action: 'asset_management.action_antivirus_assets_macos' }
        ];
    },

    getAvTabData(tabId) {
        return this.tabs.find(t => t.id === tabId);
    },

    isAvView() {
        const context = this.props.list?.context || {};
        const actionXmlId = this.env.config?.actionXmlId || "";
        const result = context.antivirus_assets_view === true ||
            actionXmlId.includes('action_antivirus_assets') ||
            actionXmlId.includes('tabbed');

        // Only log once to avoid clutter but ensure visibility
        if (result) {
            console.log("[Antivirus] View detected! Context:", context);
        }
        return result;
    },

    isAvTabActive(tabId) {
        const context = this.props.list?.context || {};
        const actionXmlId = this.env.config?.actionXmlId || "";

        // Match by context platform first
        if (context.default_os_platform === tabId) return true;

        // Match by action ID
        if (actionXmlId.includes(tabId)) return true;

        // Default to Windows for the main entry point
        if ((context.antivirus_assets_view || actionXmlId.includes('tabbed')) && tabId === 'windows' && !context.default_os_platform) return true;

        return false;
    },

    onCellClicked(record, column, ev) {
        if (this.isAvView()) {
            console.log("[Antivirus] Row click blocked for record:", record.resId);
            return;
        }
        super.onCellClicked(...arguments);
    },

    async onAvTabClick(tabId) {
        console.log("[Antivirus] Tab clicked:", tabId);
        const tab = this.getAvTabData(tabId);
        if (tab && tab.action) {
            await this.action.doAction(tab.action, {
                clear_breadcrumbs: true,
                stackPosition: 'replaceCurrent'
            });
        }
    },

    async onDeployClick() {
        const selectedIds = this.props.list.selection.map(rec => rec.resId);
        if (selectedIds.length === 0) return;

        console.log("[Antivirus] Deploying for IDs:", selectedIds);

        try {
            await this.props.list.model.orm.call(
                "asset.asset",
                "action_deploy_antivirus",
                [selectedIds]
            );

            // Notification
            this.env.services.notification.add(`Antivirus deployment initiated for ${selectedIds.length} assets`, {
                title: "Antivirus Deployment",
                type: "success",
            });

            // Reload the list
            await this.props.list.model.load();
        } catch (error) {
            console.error("[Antivirus] Deployment failed:", error);
        }
    }
});

/* =========================================================
   ANTIVIRUS THREAT TRACKING DASHBOARD COMPONENT
   ========================================================= */
class AntivirusTrackingDashboard extends Component {

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.dialog = useService("dialog");

        this.state = useState({
            // Threat KPIs
            total_threats: 0,
            active_threats: 0,
            quarantined_threats: 0,
            critical_threats: 0,
            high_threats: 0,
            medium_threats: 0,
            low_threats: 0,

            // License KPIs (for Overview tab)
            total_license: 0,
            used_license: 0,
            balance_license: 0,

            // Antivirus Product Stats
            total_devices: 0,
            protected_devices: 0,
            unprotected_devices: 0,
            antivirus_products: [],
            product_breakdown: [],

            // Data
            threats: [],
            filtered_threats: [],
            affected_devices: [],
            affected_devices_count: 0,
            devices_by_product: {},

            // Installer Config (for Overview tab)
            config: {
                installer_windows: '',
                installer_linux: '',
                installer_macos: '',
            },
            installer_platform: 'windows',
            show_platform_selector: false,

            // Filters
            filter_status: 'all',
            filter_severity: 'all',
            filter_product: 'all',
            viewMode: 'timeline',
            activeTab: 'overview', // 'overview' | 'products' | 'devices'

            loading: true,
            last_refresh: null,
            auto_refresh: true,
        });

        // Detect platform from action context
        const context = this.props.action?.context || {};
        const defaultPlatform = context.default_os_platform || 'windows';
        this.state.installer_platform = defaultPlatform;
        // Hide platform selector when a specific platform is set via context
        this.state.show_platform_selector = !context.default_os_platform;

        this.charts = {};
        this.refreshInterval = null;
        this.chartJsLoaded = false;
        this.chartsRendered = false;

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
            const dt = luxon.DateTime.fromISO(dtStr);
            return dt.toFormat("dd/MM/yyyy HH:mm");
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
            console.log("✅ Chart.js loaded successfully for tracking dashboard");
        } else {
            console.error("❌ Chart.js failed to load");
        }
    }

    async loadData() {
        this.state.loading = true;

        try {
            // Load all threats
            const threats = await this.orm.searchRead('antivirus.threat', [], [
                'name', 'threat_name', 'threat_type', 'severity', 'status',
                'asset_id', 'detected_date', 'summary', 'os_platform'
            ], { order: 'detected_date desc' });

            // Load all assets with antivirus info
            const assets = await this.orm.searchRead('asset.asset', [], [
                'asset_code', 'asset_name', 'serial_number', 'os_platform', 'agent_status',
                'antivirus_status', 'antivirus_product', 'antivirus_version',
                'antivirus_installed', 'antivirus_running'
            ]);

            // Load license info
            const licenses = await this.orm.searchRead('antivirus.license', [], [
                'name', 'total_licenses', 'used_licenses', 'available_licenses', 'status'
            ]);

            // Load installer config
            const configs = await this.orm.searchRead('antivirus.config', [], [
                'installer_windows', 'installer_linux', 'installer_macos'
            ]);

            // Process threats
            const processedThreats = [];
            const deviceMap = new Map();

            for (const threat of threats) {
                const deviceName = threat.asset_id?.[1] || 'Unknown Device';
                const deviceCode = threat.asset_id ? `DEV-${threat.asset_id[0]}` : 'N/A';

                processedThreats.push({
                    id: threat.id,
                    threat_name: threat.threat_name || threat.name || 'Unknown Threat',
                    threat_type: threat.threat_type || 'virus',
                    severity: threat.severity || 'medium',
                    status: threat.status || 'active',
                    device_name: deviceName,
                    device_code: deviceCode,
                    device_id: threat.asset_id?.[0],
                    detected_date: threat.detected_date,
                    summary: threat.summary || '',
                    os_platform: threat.os_platform || 'unknown'
                });

                if (threat.asset_id?.[0]) {
                    if (!deviceMap.has(threat.asset_id[0])) {
                        deviceMap.set(threat.asset_id[0], {
                            id: threat.asset_id[0],
                            name: deviceName,
                            code: deviceCode,
                            threat_count: 0
                        });
                    }
                    deviceMap.get(threat.asset_id[0]).threat_count++;
                }
            }

            // Process antivirus products
            const productStats = {};
            let protectedCount = 0;
            let unprotectedCount = 0;

            assets.forEach(asset => {
                const productName = asset.antivirus_product || 'No Antivirus';
                const isProtected = asset.antivirus_status === 'protected' && asset.antivirus_running;

                if (!productStats[productName]) {
                    productStats[productName] = {
                        name: productName,
                        count: 0,
                        protected: 0,
                        unprotected: 0,
                        versions: new Set(),
                        devices: []
                    };
                }

                productStats[productName].count++;
                productStats[productName].devices.push({
                    id: asset.id,
                    name: asset.asset_name,
                    asset_name: asset.asset_name,
                    asset_code: asset.asset_code,
                    serial_number: asset.serial_number,
                    status: asset.antivirus_status,
                    running: asset.antivirus_running,
                    version: asset.antivirus_version
                });

                if (asset.antivirus_version) {
                    productStats[productName].versions.add(asset.antivirus_version);
                }

                if (isProtected) {
                    productStats[productName].protected++;
                    protectedCount++;
                } else {
                    productStats[productName].unprotected++;
                    unprotectedCount++;
                }
            });

            // Convert to array and sort by count
            const productBreakdown = Object.values(productStats)
                .sort((a, b) => b.count - a.count);

            // Calculate threat KPIs
            const kpis = {
                total_threats: processedThreats.length,
                active_threats: processedThreats.filter(t => t.status === 'active').length,
                quarantined_threats: processedThreats.filter(t => t.status === 'quarantined').length,
                critical_threats: processedThreats.filter(t => t.severity === 'critical').length,
                high_threats: processedThreats.filter(t => t.severity === 'high').length,
                medium_threats: processedThreats.filter(t => t.severity === 'medium').length,
                low_threats: processedThreats.filter(t => t.severity === 'low').length,
                total_devices: assets.length,
                protected_devices: protectedCount,
                unprotected_devices: unprotectedCount,
            };

            // Calculate license KPIs
            let totalLicense = 0;
            let usedLicense = 0;
            let balanceLicense = 0;

            if (licenses && licenses.length > 0) {
                licenses.forEach(lic => {
                    totalLicense += (lic.total_licenses || 0);
                    usedLicense += (lic.used_licenses || 0);
                    balanceLicense += (lic.available_licenses || 0);
                });
            }

            // Get installer config
            let installerConfig = {
                installer_windows: '',
                installer_linux: '',
                installer_macos: '',
            };

            if (configs && configs.length > 0) {
                installerConfig = {
                    installer_windows: configs[0].installer_windows || '',
                    installer_linux: configs[0].installer_linux || '',
                    installer_macos: configs[0].installer_macos || '',
                };
            }

            this.state.threats = processedThreats;
            this.state.filtered_threats = processedThreats;
            this.state.antivirus_products = productBreakdown;
            this.state.product_breakdown = productBreakdown;
            this.state.devices_by_product = productStats;
            Object.assign(this.state, kpis);
            
            // Set license KPIs
            this.state.total_license = totalLicense;
            this.state.used_license = usedLicense;
            this.state.balance_license = balanceLicense;
            
            // Set installer config
            this.state.config = installerConfig;

            const affectedDevices = Array.from(deviceMap.values())
                .sort((a, b) => b.threat_count - a.threat_count);
            this.state.affected_devices = affectedDevices;
            this.state.affected_devices_count = affectedDevices.length;

            this.state.last_refresh = luxon.DateTime.now().toFormat("dd/MM/yyyy HH:mm:ss");
            console.log("✅ Antivirus tracking data loaded");
            console.log("📊 KPIs:", {
                total_devices: this.state.total_devices,
                protected_devices: this.state.protected_devices,
                unprotected_devices: this.state.unprotected_devices,
                total_threats: this.state.total_threats
            });

        } catch (error) {
            console.error("❌ Failed to load antivirus tracking data:", error);
            this.notification.add("Failed to load antivirus tracking data.", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    async refreshData(silent = false) {
        await this.loadData();
        if (this.chartsRendered) {
            this.updateCharts();
        }
        if (!silent) {
            this.notification.add("Threat tracking refreshed", { type: "success" });
        }
    }

    startAutoRefresh() {
        if (this.state.auto_refresh) {
            this.refreshInterval = setInterval(() => {
                this.refreshData(true); // Silent refresh for auto-refresh
            }, 30000);
        }
    }

    stopAutoRefresh() {
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
            this.refreshInterval = null;
        }
    }

    setActiveTab(tab) {
        this.state.activeTab = tab;
        setTimeout(() => {
            if (this.chartJsLoaded) {
                this.renderCharts();
            }
        }, 100);
    }

    filterByStatus(status) {
        this.state.filter_status = status;
        this.applyFilters();
    }

    filterBySeverity(severity) {
        this.state.filter_severity = severity;
        this.applyFilters();
    }

    filterByProduct(product) {
        this.state.filter_product = product;
        this.applyFilters();
    }

    applyFilters() {
        let filtered = [...this.state.threats];

        if (this.state.filter_status !== 'all') {
            filtered = filtered.filter(t => t.status === this.state.filter_status);
        }

        if (this.state.filter_severity !== 'all') {
            filtered = filtered.filter(t => t.severity === this.state.filter_severity);
        }

        this.state.filtered_threats = filtered;
        this.updateKPIsFromFilter();
    }

    updateKPIsFromFilter() {
        const filtered = this.state.filtered_threats;

        Object.assign(this.state, {
            total_threats: filtered.length,
            active_threats: filtered.filter(t => t.status === 'active').length,
            quarantined_threats: filtered.filter(t => t.status === 'quarantined').length,
            critical_threats: filtered.filter(t => t.severity === 'critical').length,
            high_threats: filtered.filter(t => t.severity === 'high').length,
            medium_threats: filtered.filter(t => t.severity === 'medium').length,
            low_threats: filtered.filter(t => t.severity === 'low').length,
        });
    }

    setViewMode(mode) {
        this.state.viewMode = mode;
    }

    getThreatIcon(threatType) {
        const iconMap = {
            'virus': 'fa-bug', 'malware': 'fa-exclamation-circle',
            'ransomware': 'fa-lock', 'spyware': 'fa-eye',
            'trojan': 'fa-shield', 'worm': 'fa-random', 'default': 'fa-bug',
        };
        return iconMap[threatType?.toLowerCase()] || iconMap['default'];
    }

    getSeverityBadge(severity) {
        const badgeMap = {
            'critical': 'bg-danger', 'high': 'bg-warning text-dark',
            'medium': 'bg-info', 'low': 'bg-secondary',
        };
        return badgeMap[severity?.toLowerCase()] || 'bg-secondary';
    }

    getStatusClass(status) {
        const classMap = {
            'active': 'status-active', 'quarantined': 'status-quarantined',
            'cleaning': 'status-cleaning', 'removed': 'status-removed',
        };
        return classMap[status?.toLowerCase()] || 'status-active';
    }

    getStatusIcon(status) {
        const iconMap = {
            'active': 'fa-exclamation-circle', 'quarantined': 'fa-lock',
            'cleaning': 'fa-sync', 'removed': 'fa-check-circle',
        };
        return iconMap[status?.toLowerCase()] || 'fa-circle';
    }

    getStatusLabel(status) {
        const labelMap = {
            'active': 'Active', 'quarantined': 'Quarantined',
            'cleaning': 'Cleaning', 'removed': 'Removed',
        };
        return labelMap[status?.toLowerCase()] || status;
    }

    getProtectionStatusBadge(status, running) {
        if (status === 'protected' && running) {
            return { class: 'bg-success', label: 'Protected' };
        } else if (status === 'protected' && !running) {
            return { class: 'bg-warning text-dark', label: 'Not Running' };
        } else if (['unprotected', 'expired'].includes(status)) {
            return { class: 'bg-danger', label: 'Unprotected' };
        } else if (status === 'pending' || status === 'installing') {
            return { class: 'bg-info', label: 'Installing' };
        }
        return { class: 'bg-secondary', label: 'Not Installed' };
    }

    getProductIcon(productName) {
        const name = (productName || '').toLowerCase();
        if (name.includes('kaspersky')) return 'fa-shield-virus text-danger';
        if (name.includes('bitdefender')) return 'fa-shield-alt text-primary';
        if (name.includes('sophos')) return 'fa-shield-cat text-purple';
        if (name.includes('mcafee')) return 'fa-shield-dog text-info';
        if (name.includes('symantec') || name.includes('norton')) return 'fa-shield-halved text-warning';
        if (name.includes('eset')) return 'fa-shield-heart text-success';
        if (name.includes('trend')) return 'fa-shield-lightning text-danger';
        if (name.includes('f-secure')) return 'fa-shield-check text-info';
        if (name.includes('defender') || name.includes('microsoft')) return 'fa-brands fa-windows text-primary';
        return 'fa-shield text-muted';
    }

    calculateSeverityPercent(count) {
        const total = this.state.total_threats || 1;
        return Math.round((count / total) * 100);
    }

    async quarantineThreat(threatId) {
        try {
            await this.orm.write('antivirus.threat', [threatId], { status: 'quarantined' });
            this.notification.add("Threat quarantined successfully", { type: "success" });
            await this.refreshData();
        } catch (error) {
            console.error("Error quarantining threat:", error);
            this.notification.add("Failed to quarantine threat", { type: "danger" });
        }
    }

    async removeThreat(threatId) {
        if (!confirm('Are you sure you want to remove this threat?')) return;
        try {
            await this.orm.write('antivirus.threat', [threatId], { status: 'removed' });
            this.notification.add("Threat removed successfully", { type: "success" });
            await this.refreshData();
        } catch (error) {
            console.error("Error removing threat:", error);
            this.notification.add("Failed to remove threat", { type: "danger" });
        }
    }

    viewThreatDetails(threatId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Threat Details",
            res_model: "antivirus.threat",
            res_id: threatId,
            view_mode: "form",
            views: [[false, "form"]],
        });
    }

    openDevice(deviceId) {
        if (!deviceId) return;
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Device",
            res_model: "asset.asset",
            res_id: deviceId,
            view_mode: "form",
            views: [[false, "form"]],
        });
    }

    viewProductDetails(productName) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${productName} - Devices`,
            res_model: "asset.asset",
            view_mode: "tree,form",
            domain: [['antivirus_product', 'ilike', productName]],
            views: [[false, "list"], [false, "form"]],
        });
    }

    viewAllDevices() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "All Devices",
            res_model: "asset.asset",
            view_mode: "tree,form",
            views: [[false, "list"], [false, "form"]],
        });
    }

    viewUnprotectedDevices() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Unprotected Devices",
            res_model: "asset.asset",
            view_mode: "tree,form",
            domain: [['antivirus_status', 'in', ['unprotected', 'expired', false]]],
            views: [[false, "list"], [false, "form"]],
        });
    }

    openAllDevices() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "All Devices",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
        });
    }

    openProtectedDevices() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Protected Devices",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["antivirus_status", "=", "protected"]],
        });
    }

    openUnprotectedDevices() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Unprotected Devices",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["antivirus_status", "in", ["unprotected", "expired"]]],
        });
    }

    openThreats() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Detected Threats",
            res_model: "antivirus.threat",
            view_mode: "tree,form",
            views: [[false, "list"], [false, "form"]],
        });
    }

    async exportReport() {
        this.notification.add("Export functionality coming soon", { type: "info" });
    }

    // =====================================================
    // OVERVIEW TAB METHODS
    // =====================================================

    setInstallerPlatform(platform) {
        this.state.installer_platform = platform;
    }

    async saveInstallerConfig(platform) {
        try {
            const configModel = 'antivirus.config';
            const fieldName = `installer_${platform}`;
            const value = this.state.config[fieldName];

            // Search for existing config
            const existingConfigs = await this.orm.searchRead(configModel, [], ['id', fieldName]);

            if (existingConfigs && existingConfigs.length > 0) {
                // Update existing
                await this.orm.write(configModel, [existingConfigs[0].id], { [fieldName]: value });
            } else {
                // Create new
                const configData = { [fieldName]: value };
                await this.orm.create(configModel, [configData]);
            }

            this.notification.add(`${platform.charAt(0).toUpperCase() + platform.slice(1)} installer URL saved successfully`, { type: "success" });
        } catch (error) {
            console.error("Error saving installer config:", error);
            this.notification.add("Failed to save installer configuration", { type: "danger" });
        }
    }

    async deployAllAntivirus() {
        if (!confirm('Deploy antivirus to all unprotected devices?')) return;

        try {
            // Get all unprotected assets
            const unprotectedAssets = await this.orm.searchRead('asset.asset', [
                ['antivirus_status', 'in', ['unprotected', 'expired', false]]
            ], ['id', 'asset_name']);

            if (unprotectedAssets.length === 0) {
                this.notification.add("No unprotected devices found", { type: "info" });
                return;
            }

            let successCount = 0;
            let failCount = 0;

            for (const asset of unprotectedAssets) {
                try {
                    await this.orm.call('asset.asset', 'action_deploy_antivirus', [[asset.id]]);
                    successCount++;
                } catch (error) {
                    console.error('Error deploying to asset:', asset.id, error);
                    failCount++;
                }
            }

            await this.refreshData();

            let message = `Deployed to ${successCount} device(s)`;
            if (failCount > 0) {
                message += ` (${failCount} failed)`;
            }
            this.notification.add(message, { type: successCount > 0 ? 'success' : 'danger' });
        } catch (error) {
            console.error("Error deploying antivirus:", error);
            this.notification.add("Failed to deploy antivirus", { type: "danger" });
        }
    }

    destroyCharts() {
        Object.values(this.charts).forEach(chart => {
            if (chart) {
                try { chart.destroy(); } catch (e) { console.warn("Chart destroy error:", e); }
            }
        });
        this.charts = {};
        this.chartsRendered = false;
    }

    updateCharts() {
        try {

            if (this.charts.productDonut) {
                const productCounts = {};
                this.state.antivirus_products.forEach(p => {
                    productCounts[p.name] = p.count;
                });
                this.charts.productDonut.data.labels = Object.keys(productCounts);
                this.charts.productDonut.data.datasets[0].data = Object.values(productCounts);
                this.charts.productDonut.update('none');
            }
        } catch (error) {
            console.error("Error updating charts:", error);
        }
    }

    renderCharts() {
        if (!window.Chart) {
            console.error("Chart.js not available");
            return;
        }

        this.destroyCharts();

        const safeDestroy = (canvasId) => {
            const existing = window.Chart.getChart(canvasId);
            if (existing) existing.destroy();
        };

        console.log("🎨 Rendering threat tracking charts...");


        // Product Distribution Chart
        const productCanvas = document.getElementById("product_distribution_chart");
        if (productCanvas && this.state.activeTab === 'products') {
            try {
                safeDestroy("product_distribution_chart");
                const ctx = productCanvas.getContext('2d');

                const productCounts = {};
                this.state.antivirus_products.forEach(p => {
                    productCounts[p.name] = p.count;
                });

                const labels = Object.keys(productCounts);
                const data = Object.values(productCounts);

                const colors = [
                    '#667eea', '#764ba2', '#f093fb', '#f5576c',
                    '#4facfe', '#00f2fe', '#43e97b', '#38f9d7',
                    '#fa709a', '#fee140', '#a8edea', '#fed6e3'
                ];

                this.charts.productDonut = new Chart(ctx, {
                    type: "doughnut",
                    data: {
                        labels: labels,
                        datasets: [{
                            data: data,
                            backgroundColor: colors.slice(0, labels.length),
                            borderWidth: 2,
                            borderColor: "#ffffff",
                        }],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        cutout: "60%",
                        plugins: {
                            // This chart has no center text — disable the global plugin
                            // so it does not paint a spurious "0" in the cutout hole.
                            donutCenterText: false,
                            legend: {
                                position: "bottom",
                                labels: {
                                    boxWidth: 12,
                                    boxHeight: 12,
                                    padding: 10,
                                    font: { size: 11 },
                                },
                            },
                            tooltip: {
                                callbacks: {
                                    label: function(context) {
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
                console.log("✅ Product distribution chart rendered");
            } catch (error) {
                console.error("Error rendering product distribution chart:", error);
            }
        }
    }
}

AntivirusTrackingDashboard.template = "antivirus_management.AntivirusTrackingDashboard";
registry.category("actions").add("antivirus_tracking_dashboard", AntivirusTrackingDashboard);
