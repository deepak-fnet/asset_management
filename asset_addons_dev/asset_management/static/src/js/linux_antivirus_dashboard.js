/** @odoo-module **/

import { Component, useState, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";

/* =========================
   LINUX ANTIVIRUS DASHBOARD COMPONENT
   Same logic as WindowsAntivirusDashboard — canvas IDs differ.
   ========================= */
class LinuxAntivirusDashboard extends Component {

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            // KPIs
            total_devices: 0,
            windows_count: 0,
            linux_count: 0,
            macos_count: 0,
            protected_devices: 0,
            unprotected_devices: 0,
            total_threats: 0,
            active_threats: 0,
            quarantined_threats: 0,
            critical_threats: 0,

            // Antivirus Product Counts
            kaspersky_count: 0,
            windows_defender_count: 0,
            mcafee_count: 0,
            others_count: 0,

            // Lists
            threat_list: [],
            device_list: [],
            recent_scans: [],

            // Config
            config: {
                installer_windows: '',
                installer_linux: '',
                installer_macos: '',
            },

            // UI State
            activeTab: 'overview',
            installer_platform: 'linux',
            show_platform_selector: false,
            loading: true,
            last_refresh: null,
            // Deploy Center
            deploy_loading: false,
            deploy_queue_count: 0,
            unprotected_devices_list: [],

            // KSC API State
            ksc_config: {
                server_url: '',
                username: '',
                password: '',
                package_name: '',
                verify_ssl: true,
            },
            ksc_status: 'unknown', // unknown|testing|connected|failed
            ksc_error_msg: null,

        });

        this.charts = {};
        this.chartJsLoaded = false;

        onWillStart(async () => {
            await this.waitForChartJs();
            await this.loadData();
        });

        onMounted(() => {
            this.renderChartsWhenReady();
        });

        onWillUnmount(() => {
            this.destroyCharts();
        });
    }

    /**
     * Polls for chart canvases then renders. Deferred resizes handle
     * Odoo layout animations firing after onMounted.
     */
    async renderChartsWhenReady() {
        if (!this.chartJsLoaded) return;
        const maxRetries = 30;
        for (let i = 0; i < maxRetries; i++) {
            const avCanvas   = document.getElementById('linux_av_distribution_chart');
            const protCanvas = document.getElementById('linux_av_protection_chart');
            if (avCanvas && protCanvas) {
                this.renderCharts();
                for (const delay of [150, 400]) {
                    setTimeout(() => {
                        Object.values(this.charts).forEach(chart => {
                            if (chart) { try { chart.resize(); } catch (e) {} }
                        });
                    }, delay);
                }
                return;
            }
            await new Promise(r => setTimeout(r, 100));
        }
        console.warn('⚠️ Linux chart canvases not found after 3s – skipping render');
    }

    /* =========================
       DATA LOADING
       ========================= */
    async loadData() {
        this.state.loading = true;

        try {
            const kpis = await this.orm.call("antivirus.dashboard", "get_kpis_by_platform", ['linux']);
            Object.assign(this.state, kpis);

            // Map protection counts (backend returns protected_count / unprotected_count)
            this.state.protected_devices = kpis.protected_count || 0;
            this.state.unprotected_devices = kpis.unprotected_count || 0;

            this.state.total_threats = kpis.threats_count || 0;
            this.state.quarantined_threats = kpis.threats_quarantined || 0;
            this.state.active_threats = (kpis.threats_count || 0) - (kpis.threats_quarantined || 0);

            this.state.kaspersky_count = kpis.kaspersky_count || 0;
            this.state.windows_defender_count = kpis.windows_defender_count || 0;
            this.state.mcafee_count = kpis.mcafee_count || 0;
            this.state.others_count = kpis.others_count || 0;

            const threats = await this.orm.call("antivirus.dashboard", "get_threats_by_platform", ['linux', 10]);
            this.state.threat_list = threats || [];
            this.state.critical_threats = (threats || []).filter(t => t.severity === 'critical').length;

            await this.loadDevices();
            await this.loadConfig();

            this.state.last_refresh = luxon.DateTime.now().toFormat("dd/MM/yyyy HH:mm:ss");
            console.log("✅ Linux antivirus dashboard data loaded");

            if (this.chartJsLoaded) {
                await new Promise(r => setTimeout(r, 50));
                this.renderCharts();
                setTimeout(() => {
                    Object.values(this.charts).forEach(chart => {
                        if (chart) { try { chart.resize(); } catch (e) {} }
                    });
                }, 150);
            }

        } catch (error) {
            console.error("❌ Failed to load Linux antivirus dashboard data:", error);
            this.notification.add("Failed to load antivirus dashboard data.", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    async loadDevices() {
        try {
            const assets = await this.orm.searchRead("asset.asset",
                [["os_platform", "=", "linux"]], [
                'id', 'asset_name', 'asset_code', 'os_platform', 'agent_status', 'antivirus_status', 'antivirus_product', 'last_sync_time'
            ], { limit: 100 });

            this.state.device_list = (assets || []).map(asset => ({
                id: asset.id,
                name: asset.asset_name || 'Unknown',
                code: asset.asset_code || `DEV-${asset.id}`,
                platform: asset.os_platform || 'unknown',
                status: asset.agent_status || 'offline',
                protection: asset.antivirus_status || 'unprotected',
                antivirus: asset.antivirus_product || 'N/A',
                last_scan: asset.last_sync_time || 'Never',
            }));
        } catch (error) {
            console.error("Failed to load devices:", error);
        }
    }

    async loadConfig() {
        try {
            const configs = await this.orm.searchRead("antivirus.config", [], [
                'installer_windows', 'installer_linux', 'installer_macos'
            ], { limit: 1 });

            if (configs && configs.length > 0) {
                this.state.config = {
                    installer_windows: configs[0].installer_windows || '',
                    installer_linux: configs[0].installer_linux || '',
                    installer_macos: configs[0].installer_macos || '',
                };
            }
        } catch (error) {
            console.error("Failed to load config:", error);
        }
    }

    async refreshData() {
        await this.loadData();
        this.notification.add("Dashboard refreshed", { type: "success" });
    }

    /* =========================
       CHART.JS
       ========================= */
    async waitForChartJs() {
        const maxAttempts = 20;
        let attempts = 0;
        while (!window.Chart && attempts < maxAttempts) {
            await new Promise(resolve => setTimeout(resolve, 100));
            attempts++;
        }
        if (window.Chart) {
            this.chartJsLoaded = true;
        }
    }

    destroyCharts() {
        Object.values(this.charts).forEach(chart => {
            if (chart) { try { chart.destroy(); } catch (e) {} }
        });
        this.charts = {};
    }

    renderCharts() {
        if (!window.Chart || !this.chartJsLoaded) return;
        this.destroyCharts();

        const makeCenterPlugin = (id, subLabel, realValues) => ({
            id,
            afterDraw(chart) {
                const { ctx, chartArea } = chart;
                if (!chartArea) return;
                const total = realValues.reduce((a, b) => a + b, 0);
                const cx = (chartArea.left + chartArea.right) / 2;
                const cy = (chartArea.top + chartArea.bottom) / 2;
                ctx.save();
                ctx.font = 'bold 30px Inter, Arial, sans-serif';
                ctx.fillStyle = '#1e293b';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(total.toString(), cx, cy - 10);
                ctx.font = '500 12px Inter, Arial, sans-serif';
                ctx.fillStyle = '#6b7280';
                ctx.fillText(subLabel, cx, cy + 14);
                ctx.restore();
            }
        });

        const makeTooltip = (realValues) => ({
            callbacks: {
                label: ctx => {
                    const total = realValues.reduce((a, b) => a + b, 0);
                    const pct   = total > 0 ? ((ctx.parsed / total) * 100).toFixed(1) : '0.0';
                    return ` ${ctx.label}: ${ctx.parsed} (${pct}%)`;
                }
            }
        });

        // ── Antivirus Distribution Donut ──────────────────────────────
        const avCanvas = document.getElementById('linux_av_distribution_chart');
        if (avCanvas) {
            const kaspersky  = this.state.kaspersky_count         || 0;
            const defender   = this.state.windows_defender_count  || 0;
            const mcafee     = this.state.mcafee_count            || 0;
            const others     = this.state.others_count            || 0;
            const realValues = [kaspersky, defender, mcafee, others];
            const hasData    = realValues.some(v => v > 0);

            this.charts.avDist = new window.Chart(avCanvas.getContext('2d'), {
                type: 'doughnut',
                data: {
                    labels: ['Kaspersky', 'ClamAV / Defender', 'McAfee', 'Others'],
                    datasets: [{
                        data: hasData ? realValues : [1],
                        backgroundColor: hasData
                            ? ['#22c55e', '#f97316', '#ef4444', '#94a3b8']
                            : ['#e5e7eb'],
                        borderWidth: hasData ? 3 : 0,
                        borderColor: '#ffffff',
                        hoverOffset: 6,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '72%',
                    plugins: {
                        legend: {
                            display: hasData,
                            position: 'right',
                            labels: { boxWidth: 12, padding: 14, font: { size: 12 }, color: '#374151' }
                        },
                        tooltip: { enabled: hasData, ...makeTooltip(realValues) },
                    },
                },
                plugins: [makeCenterPlugin('centerAV', 'Total Assets', realValues)],
            });
        }

        // ── Protection Status Donut ───────────────────────────────────
        const protCanvas = document.getElementById('linux_av_protection_chart');
        if (protCanvas) {
            const prot       = this.state.protected_count   || this.state.protected_devices   || 0;
            const unprot     = this.state.unprotected_count || this.state.unprotected_devices || 0;
            const realValues = [prot, unprot];
            const hasData    = realValues.some(v => v > 0);

            this.charts.avProt = new window.Chart(protCanvas.getContext('2d'), {
                type: 'doughnut',
                data: {
                    labels: ['Protected', 'Unprotected'],
                    datasets: [{
                        data: hasData ? realValues : [1],
                        backgroundColor: hasData ? ['#10b981', '#ef4444'] : ['#e5e7eb'],
                        borderWidth: hasData ? 3 : 0,
                        borderColor: '#ffffff',
                        hoverOffset: 6,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '72%',
                    plugins: {
                        legend: {
                            display: hasData,
                            position: 'right',
                            labels: { boxWidth: 12, padding: 14, font: { size: 12 }, color: '#374151' }
                        },
                        tooltip: { enabled: hasData, ...makeTooltip(realValues) },
                    },
                },
                plugins: [makeCenterPlugin('centerProt', 'Total Assets', realValues)],
            });
        }
    }

    /* =========================
       TAB NAVIGATION
       ========================= */
    setActiveTab(tab) {
        this.state.activeTab = tab;

        if (tab === 'devices' || tab === 'all_devices') {
            this.loadDevices();
        } else if (tab === 'deploy_center') {
            this.loadDeployCenter();
        } else if (tab === 'overview') {
            setTimeout(() => {
                Object.values(this.charts).forEach(chart => {
                    if (chart) {
                        chart.resize();
                        chart.update('none');
                    }
                });
                if (Object.keys(this.charts).length === 0) {
                    this.renderChartsWhenReady();
                }
            }, 50);
        }
    }

    async loadThreats() {
        try {
            const threats = await this.orm.call("antivirus.dashboard", "get_threats", [50]);
            this.state.threat_list = threats || [];
            this.state.critical_threats = (threats || []).filter(t => t.severity === 'critical').length;
            this.state.active_threats = (threats || []).filter(t => t.status === 'active').length;
            this.state.quarantined_threats = (threats || []).filter(t => t.status === 'quarantined').length;
            this.state.total_threats = threats ? threats.length : 0;
        } catch (error) {
            console.error("Failed to load threats:", error);
        }
    }

    /* =========================
       INSTALLER CONFIGURATION
       ========================= */
    setInstallerPlatform(platform) {
        this.state.installer_platform = platform;
    }

    async saveInstallerConfig(platform) {
        try {
            const configs = await this.orm.searchRead("antivirus.config", [], [], { limit: 1 });
            let configId = configs && configs.length > 0 ? configs[0].id : null;

            const fieldMap = {
                'windows': 'installer_windows',
                'linux': 'installer_linux',
                'macos': 'installer_macos',
            };

            const fieldName = fieldMap[platform];
            const value = this.state.config[fieldName];

            if (!value) {
                this.notification.add(`Please enter a ${platform} installer URL`, { type: "warning" });
                return;
            }

            if (configId) {
                await this.orm.write("antivirus.config", [configId], { [fieldName]: value });
            } else {
                await this.orm.create("antivirus.config", [{
                    name: 'Default Configuration',
                    antivirus_product: 'kaspersky',
                    [fieldName]: value,
                    is_default: true,
                }]);
            }

            this.notification.add(`${platform.charAt(0).toUpperCase() + platform.slice(1)} installer saved successfully!`, { type: "success" });
            await this.loadConfig();

        } catch (error) {
            console.error("Failed to save installer config:", error);
            this.notification.add("Failed to save installer configuration", { type: "danger" });
        }
    }

    /* =========================
       FILTERING
       ========================= */
    filterByStatus(status) {
        if (status === 'all') {
            this.loadThreats();
        } else {
            this.state.threat_list = (this.state.threat_list || []).filter(t => t.status === status);
        }
        this.notification.add(`Filtered by ${status} threats`, { type: "info" });
    }

    filterBySeverity(severity) {
        if (severity === 'all') {
            this.loadThreats();
        } else {
            this.state.threat_list = (this.state.threat_list || []).filter(t => t.severity === severity);
        }
        this.notification.add(`Filtered by ${severity} severity`, { type: "info" });
    }

    /* =========================
       DEPLOY CENTER — KSC API
       ========================= */

    /** Load unprotected Linux devices and KSC config from the server. */
    async loadDeployCenter() {
        try {
            const assets = await this.orm.searchRead("asset.asset",
                [["os_platform", "=", "linux"], ["antivirus_status", "in", ["unprotected", "expired"]]],
                ['id','asset_name','asset_code','agent_status','antivirus_status','antivirus_product','last_sync_time'],
                { limit: 200 }
            );
            this.state.unprotected_devices_list = (assets || []).map(a => ({
                id: a.id,
                name: a.asset_name || 'Unknown',
                code: a.asset_code || `DEV-${a.id}`,
                status: a.agent_status || 'offline',
                protection: a.antivirus_status || 'unprotected',
                antivirus: a.antivirus_product || 'N/A',
                last_scan: a.last_sync_time || 'Never',
                deploy_status: null,   // null|pending|running|success|failed
                deploy_progress: 0,
                ksc_task_id: null,
            }));
            this.state.deploy_queue_count = this.state.unprotected_devices_list.length;

            // Load KSC credentials from config
            await this._loadKSCConfig();
        } catch (e) {
            console.error('loadDeployCenter error:', e);
        }
    }

    /** Fetch KSC credentials saved on the server and pre-fill state.ksc_config. */
    async _loadKSCConfig() {
        try {
            const configs = await this.orm.searchRead("antivirus.ksc.config", [], [
                'ksc_url','username','package_name','verify_ssl'
            ], { limit: 1 });
            if (configs && configs.length > 0) {
                const c = configs[0];
                this.state.ksc_config = {
                    server_url:   c.ksc_url || '',
                    username:     c.username || '',
                    password:     '',  // never returned
                    package_name: c.package_name || '',
                    verify_ssl:   c.verify_ssl || false,
                };
            }
        } catch (e) {
            console.error('_loadKSCConfig error:', e);
        }
    }

    /** Test KSC connection — calls /api/antivirus/ksc/test */
    async testKSCConnection() {
        this.state.ksc_status = 'testing';
        this.state.ksc_error_msg = null;
        // Auto-save current form values first so the controller uses them
        await this.saveKSCConfig(true);
        try {
            // Odoo 19: use the framework rpc() helper instead of a hand-rolled
            // JSON-RPC envelope. It builds the envelope, forwards the session
            // context, and returns the unwrapped `result` payload.
            const data = await rpc('/api/antivirus/ksc/test', {});
            if (data.ok) {
                this.state.ksc_status = 'connected';
                this.notification.add(`✅ ${data.message || 'Connected to KSC'}`, { type: 'success' });
            } else {
                this.state.ksc_status = 'failed';
                this.state.ksc_error_msg = data.message || 'Connection failed';
                this.notification.add(`❌ ${data.message || 'KSC Connection failed'}`, { type: 'danger' });
            }
        } catch (e) {
            this.state.ksc_status = 'failed';
            this.state.ksc_error_msg = 'Network error: ' + e.message;
            this.notification.add('Cannot reach KSC server: ' + e.message, { type: 'danger' });
        }
    }

    /** Save KSC credentials to the server config. */
    async saveKSCConfig(silent = false) {
        try {
            const { server_url, username, password, package_name, verify_ssl } = this.state.ksc_config;
            const data = await rpc('/api/antivirus/ksc/config', {
                ksc_server_url:   server_url,
                ksc_username:     username,
                ksc_password:     password,
                ksc_package_name: package_name,
                ksc_verify_ssl:   verify_ssl,
            });

            if (!silent) {
                if (data.ok) {
                    this.notification.add('KSC configuration saved!', { type: 'success' });
                } else {
                    this.notification.add(data.message || 'Save failed', { type: 'warning' });
                }
            }
        } catch (e) {
            if (!silent) {
                this.notification.add('Failed to save KSC config: ' + e.message, { type: 'danger' });
            }
        }
    }

    /**
     * Deploy via KSC to a single device.
     * Calls /api/antivirus/ksc/deploy, then polls status every 3s.
     */
    async deployToDevice(deviceId, deviceName) {
        if (!this.state.ksc_config.server_url) {
            this.notification.add('KSC Server URL is not configured. Please fill in the KSC Connection section.', { type: 'warning' });
            return;
        }
        const ok = confirm(`Deploy Kaspersky to "${deviceName}" via KSC?`);
        if (!ok) return;

        // Set device status to pending
        this._setDeviceDeployStatus(deviceId, 'pending', null, null);
        this.state.deploy_loading = true;

        try {
            const data = await rpc('/api/antivirus/ksc/deploy', {
                device_ids: [deviceId],
            });
            if (!data.ok) {
                this._setDeviceDeployStatus(deviceId, 'failed', null, data.message);
                this.notification.add(`Deploy failed: ${data.message}`, { type: 'danger' });
                return;
            }

            const deviceResult = (data.results || [])[0];
            if (!deviceResult || deviceResult.status === 'failed') {
                const err = deviceResult?.error || 'Unknown error';
                this._setDeviceDeployStatus(deviceId, 'failed', null, err);
                this.notification.add(`Deploy failed for ${deviceName}: ${err}`, { type: 'danger' });
                return;
            }

            const taskId = deviceResult.task_id;
            this._setDeviceDeployStatus(deviceId, 'running', taskId, null);
            this.notification.add(`Kaspersky deployment started for ${deviceName}`, { type: 'info' });

            // Poll status
            this._pollTaskStatus(deviceId, taskId);

        } catch (e) {
            console.error('deployToDevice error:', e);
            this._setDeviceDeployStatus(deviceId, 'failed', null, e.message);
            this.notification.add('Deploy error: ' + e.message, { type: 'danger' });
        } finally {
            this.state.deploy_loading = false;
        }
    }

    /**
     * Deploy via KSC to ALL unprotected Linux devices at once.
     */
    async deployAllUnprotected() {
        if (!this.state.ksc_config.server_url) {
            this.notification.add('KSC Server URL is not configured.', { type: 'warning' });
            return;
        }
        const list = this.state.unprotected_devices_list || [];
        if (list.length === 0) {
            this.notification.add('No unprotected Linux devices found.', { type: 'info' });
            return;
        }
        const ok = confirm(`Deploy Kaspersky via KSC to all ${list.length} unprotected Linux device(s)?`);
        if (!ok) return;

        // Mark all as pending
        list.forEach(d => this._setDeviceDeployStatus(d.id, 'pending', null, null));
        this.state.deploy_loading = true;
        const ids = list.map(d => d.id);

        try {
            const data = await rpc('/api/antivirus/ksc/deploy', {
                device_ids: ids,
            });
            if (!data.ok) {
                list.forEach(d => this._setDeviceDeployStatus(d.id, 'failed', null, data.message));
                this.notification.add(`Bulk deploy failed: ${data.message}`, { type: 'danger' });
                return;
            }

            let started = 0;
            for (const r of (data.results || [])) {
                if (r.status === 'pending' && r.task_id) {
                    this._setDeviceDeployStatus(r.device_id, 'running', r.task_id, null);
                    this._pollTaskStatus(r.device_id, r.task_id);
                    started++;
                } else {
                    this._setDeviceDeployStatus(r.device_id, 'failed', null, r.error);
                }
            }
            this.notification.add(`KSC deployment started for ${started} device(s)`, { type: 'success' });

        } catch (e) {
            console.error('deployAllUnprotected error:', e);
            list.forEach(d => this._setDeviceDeployStatus(d.id, 'failed', null, e.message));
            this.notification.add('Bulk deploy error: ' + e.message, { type: 'danger' });
        } finally {
            this.state.deploy_loading = false;
        }
    }

    /**
     * Poll /api/antivirus/ksc/status/<taskId> every 3s until terminal state.
     */
    _pollTaskStatus(deviceId, taskId) {
        const INTERVAL_MS = 3000;
        const MAX_POLLS = 40; // 2 minutes max
        let polls = 0;

        const poll = async () => {
            polls++;
            if (polls > MAX_POLLS) {
                this._setDeviceDeployStatus(deviceId, 'failed', taskId, 'Timed out waiting for KSC task');
                return;
            }
            try {
                const data = await rpc(`/api/antivirus/ksc/status/${taskId}`, {});
                const status = data.status || 'unknown';
                const progress = data.progress || 0;

                if (status === 'success') {
                    this._setDeviceDeployStatus(deviceId, 'success', taskId, null);
                    this.state.unprotected_devices_list.find(d => d.id === deviceId).deploy_progress = 100;
                    // Update Odoo record to mark protected
                    this.orm.write('asset.asset', [deviceId], {
                        antivirus_status: 'protected',
                        antivirus_product: 'Kaspersky Endpoint Security',
                    }).catch(() => {});
                    this.notification.add('✅ Kaspersky successfully deployed!', { type: 'success' });
                    await this.loadData();
                } else if (['failed', 'cancelled'].includes(status)) {
                    this._setDeviceDeployStatus(deviceId, 'failed', taskId, data.message || status);
                    this.notification.add(`⚠️ KSC deployment ${status}`, { type: 'warning' });
                } else {
                    // Still running or pending — poll again
                    this._setDeviceDeployStatus(deviceId, 'running', taskId, null);
                    const dev = this.state.unprotected_devices_list.find(d => d.id === deviceId);
                    if (dev) {
                        dev.deploy_progress = progress;
                    }
                    setTimeout(poll, INTERVAL_MS);
                }
            } catch (e) {
                console.warn('Poll task status error:', e);
                setTimeout(poll, INTERVAL_MS);
            }
        };

        setTimeout(poll, INTERVAL_MS);
    }

    /** Update a device row's deploy_status in the reactive state list. */
    _setDeviceDeployStatus(deviceId, status, taskId, error) {
        const device = this.state.unprotected_devices_list.find(d => d.id === deviceId);
        if (device) {
            device.deploy_status = status;
            device.ksc_task_id = taskId;
            device.deploy_error = error;
            if (status === 'success') device.deploy_progress = 100;
        }
    }

    /* =========================
       DEVICE ACTIONS
       ========================= */
    async viewDevice(deviceId) {
        this.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'asset.asset',
            res_id: deviceId,
            views: [[false, 'form']],
            target: 'current',
        });
    }

    openAllDevices() {
        this.setActiveTab('all_devices');
    }

    openProtectedDevices() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Protected Linux Devices",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["os_platform", "=", "linux"], ["antivirus_status", "=", "protected"]],
        });
    }

    openUnprotectedDevices() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Unprotected Linux Devices",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["os_platform", "=", "linux"], ["antivirus_status", "in", ["unprotected", "expired"]]],
        });
    }

    openThreats() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Linux Threats",
            res_model: "antivirus.threat",
            view_mode: "tree,form",
            views: [[false, "list"], [false, "form"]],
            domain: [["os_platform", "=", "linux"]],
        });
    }

    openKasperskyDevices() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Kaspersky - Linux Devices",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["os_platform", "=", "linux"], ["antivirus_product", "ilike", "kaspersky"]],
        });
    }

    openClamAVDevices() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "ClamAV / Defender - Linux Devices",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["os_platform", "=", "linux"], ["antivirus_product", "ilike", "clamav"]],
        });
    }

    openMcAfeeDevices() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "McAfee - Linux Devices",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [["os_platform", "=", "linux"], ["antivirus_product", "ilike", "mcafee"]],
        });
    }

    openOtherDevices() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Other Antivirus - Linux Devices",
            res_model: "asset.asset",
            view_mode: "kanban,tree,form",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            domain: [
                ["os_platform", "=", "linux"],
                ["antivirus_status", "=", "protected"],
                ["antivirus_product", "not ilike", "kaspersky"],
                ["antivirus_product", "not ilike", "clamav"],
                ["antivirus_product", "not ilike", "mcafee"]
            ],
        });
    }

    /* =========================
       IMAGE & ICON RESOLVERS
       ========================= */
    resolveDeviceImage(device) {
        if (device.id) {
            return `/web/image/asset.asset/${device.id}/image_1920`;
        }
        return '/asset_management/static/src/img/asset_laptop_default.png';
    }

    resolveDeviceImageSafe(device) {
        return '/asset_management/static/src/img/asset_laptop_default.png';
    }

    getPlatformIcon(platform) {
        const map = {
            'windows': 'fa-windows',
            'linux': 'fa-linux',
            'macos': 'fa-apple',
        };
        return map[(platform || '').toLowerCase()] || 'fa-desktop';
    }

}

/* =========================
   REGISTRY
   ========================= */
LinuxAntivirusDashboard.template = "antivirus_management.LinuxAntivirusDashboard";
registry.category("actions").add("antivirus_management.linux_dashboard", LinuxAntivirusDashboard);
