/** @odoo-module **/

import { Component, useState, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { deserializeDateTime } from "@web/core/l10n/dates";

export class NetworkDashboard extends Component {
    static template = "asset_management.NetworkDashboard";

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");

        this.state = useState({
            devices: [],
            stats: {
                total: 0,
                online: 0,
                offline: 0,
                unreachable: 0,
                unknown: 0
            },
            offlineDevices: [],
            highUsageDevices: [],
            recentDevices: [],
            deviceTypeSummary: [],
            loading: true,
            auto_refresh: true,
            last_refresh: null,
        });

        this.deviceTypeLabels = {
            'router': 'Router',
            'switch': 'Switch',
            'firewall': 'Firewall',
            'access_point': 'Access Point',
            'gateway': 'Gateway'
        };

        this.manufacturerLabels = {
            'cisco': 'Cisco',
            'juniper': 'Juniper',
            'hp': 'HP',
            'dell': 'Dell',
            'mikrotik': 'MikroTik',
            'ubiquiti': 'Ubiquiti',
            'tp_link': 'TP-Link',
            'generic': 'Generic'
        };

        this.charts = {};

        onWillStart(async () => {
            await this.loadData();
        });

        onMounted(() => {
            this.renderCharts();
            this.startAutoRefresh();
        });

        onWillUnmount(() => {
            this.stopAutoRefresh();
            this.destroyCharts();
        });
    }

    async loadData() {
        this.state.loading = true;
        try {
            const deviceFields = [
                "id", "name", "device_code", "device_type", "ip_address",
                "manufacturer", "location", "connection_status", "last_check",
                "cpu_usage", "memory_usage", "ram_total", "ram_used",
                "total_interfaces", "active_interfaces", "is_active"
            ];

            const devices = await this.orm.searchRead(
                "asset.network.device",
                [["is_active", "=", true]],
                deviceFields
            );

            this.state.devices = devices.map(d => ({
                ...d,
                device_type_label: this.deviceTypeLabels[d.device_type] || d.device_type,
                manufacturer_label: this.manufacturerLabels[d.manufacturer] || d.manufacturer,
                last_check_formatted: this.formatTimestamp(d.last_check),
                cpu_usage: d.cpu_usage || 0,
                memory_usage: d.memory_usage || 0,
            }));

            this.calculateStats();
            this.calculateLists();
            this.calculateDeviceTypeSummary();

            // Update last refresh timestamp
            const now = new Date();
            this.state.last_refresh = now.toLocaleTimeString('en-US', {
                hour12: false,
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            });

            if (this.charts.deviceType) {
                this.updateCharts();
            }
        } catch (error) {
            console.error("Failed to load network devices:", error);
            this.notification.add("Failed to load data", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    formatTimestamp(utcTimestamp) {
        if (!utcTimestamp) return "Never";
        try {
            const dt = deserializeDateTime(utcTimestamp);
            return dt.toFormat('dd/MM/yyyy, HH:mm:ss');
        } catch {
            return utcTimestamp;
        }
    }

    calculateStats() {
        const devices = this.state.devices;
        this.state.stats = {
            total: devices.length,
            online: devices.filter(d => d.connection_status === 'online').length,
            offline: devices.filter(d => d.connection_status === 'offline').length,
            unreachable: devices.filter(d => d.connection_status === 'unreachable').length,
            unknown: devices.filter(d => d.connection_status === 'unknown').length,
            // Resource monitoring
            highCPU: devices.filter(d => d.cpu_usage > 80).length,
            highMemory: devices.filter(d => d.memory_usage > 80).length,
            criticalDisk: devices.filter(d => d.ram_used && d.ram_used > 0).length,
            // Device types
            routers: devices.filter(d => d.device_type === 'router').length,
            switches: devices.filter(d => d.device_type === 'switch').length,
            firewalls: devices.filter(d => d.device_type === 'firewall').length,
            accessPoints: devices.filter(d => d.device_type === 'access_point').length,
            gateways: devices.filter(d => d.device_type === 'gateway').length,
        };
    }

    calculateLists() {
        const devices = this.state.devices;

        // Offline devices
        this.state.offlineDevices = devices
            .filter(d => d.connection_status === 'offline' || d.connection_status === 'unreachable')
            .slice(0, 5);

        // High usage devices (CPU > 80% or Memory > 80%)
        this.state.highUsageDevices = devices
            .filter(d => d.connection_status === 'online' && (d.cpu_usage > 80 || d.memory_usage > 80))
            .sort((a, b) => (b.cpu_usage + b.memory_usage) - (a.cpu_usage + a.memory_usage))
            .slice(0, 5);

        // Recently checked (sorted by last_check)
        this.state.recentDevices = [...devices]
            .filter(d => d.last_check)
            .sort((a, b) => new Date(b.last_check) - new Date(a.last_check))
            .slice(0, 5);
    }

    calculateDeviceTypeSummary() {
        const devices = this.state.devices;
        const types = [
            { id: 'router', handler: 'viewRouters' },
            { id: 'switch', handler: 'viewSwitches' },
            { id: 'firewall', handler: 'viewFirewalls' },
            { id: 'access_point', handler: 'viewAccessPoints' },
            { id: 'gateway', handler: 'viewGateways' }
        ];

        this.state.deviceTypeSummary = types.map(typeInfo => {
            const type = typeInfo.id;
            const typeDevices = devices.filter(d => d.device_type === type);
            const onlineDevices = typeDevices.filter(d => d.connection_status === 'online');

            const avgCpu = onlineDevices.length > 0
                ? onlineDevices.reduce((sum, d) => sum + d.cpu_usage, 0) / onlineDevices.length
                : 0;
            const avgMemory = onlineDevices.length > 0
                ? onlineDevices.reduce((sum, d) => sum + d.memory_usage, 0) / onlineDevices.length
                : 0;
            const healthPercent = typeDevices.length > 0
                ? (onlineDevices.length / typeDevices.length) * 100
                : 0;

            return {
                type,
                label: this.deviceTypeLabels[type],
                total: typeDevices.length,
                online: onlineDevices.length,
                offline: typeDevices.length - onlineDevices.length,
                avgCpu,
                avgMemory,
                healthPercent,
                clickHandler: typeInfo.handler
            };
        }).filter(s => s.total > 0);
    }

    renderCharts() {
        this.renderDeviceTypeChart();
        this.renderStatusChart();
        this.renderManufacturerChart();
    }

    _safeDestroyChart(canvasId) {
        const existing = window.Chart && window.Chart.getChart(canvasId);
        if (existing) existing.destroy();
    }

    renderDeviceTypeChart() {
        const canvas = document.getElementById('deviceTypeChart');
        if (!canvas) return;

        this._safeDestroyChart('deviceTypeChart');
        const ctx = canvas.getContext('2d');
        const devices = this.state.devices;

        const typeCounts = {};
        devices.forEach(d => {
            const label = this.deviceTypeLabels[d.device_type] || d.device_type;
            typeCounts[label] = (typeCounts[label] || 0) + 1;
        });

        this.charts.deviceType = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: Object.keys(typeCounts),
                datasets: [{
                    data: Object.values(typeCounts),
                    backgroundColor: [
                        '#667eea', '#764ba2', '#f093fb', '#f5576c', '#4facfe'
                    ],
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: { padding: 15 }
                    }
                }
            }
        });
    }

    renderStatusChart() {
        const canvas = document.getElementById('statusChart');
        if (!canvas) return;

        this._safeDestroyChart('statusChart');
        const ctx = canvas.getContext('2d');
        const stats = this.state.stats;

        this.charts.status = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: ['Online', 'Offline', 'Unreachable', 'Unknown'],
                datasets: [{
                    label: 'Devices',
                    data: [stats.online, stats.offline, stats.unreachable, stats.unknown],
                    backgroundColor: ['#28a745', '#dc3545', '#ffc107', '#6c757d'],
                    borderRadius: 8
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: { stepSize: 1 }
                    }
                }
            }
        });
    }

    renderManufacturerChart() {
        const canvas = document.getElementById('manufacturerChart');
        if (!canvas) return;

        this._safeDestroyChart('manufacturerChart');
        const ctx = canvas.getContext('2d');
        const devices = this.state.devices;

        const mfgCounts = {};
        devices.forEach(d => {
            const label = this.manufacturerLabels[d.manufacturer] || d.manufacturer || 'Unknown';
            mfgCounts[label] = (mfgCounts[label] || 0) + 1;
        });

        this.charts.manufacturer = new Chart(ctx, {
            type: 'pie',
            data: {
                labels: Object.keys(mfgCounts),
                datasets: [{
                    data: Object.values(mfgCounts),
                    backgroundColor: [
                        '#00b4db', '#0083b0', '#00d2ff', '#3a7bd5', '#00d2d3',
                        '#54a0ff', '#5f27cd', '#341f97'
                    ],
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: { padding: 15 }
                    }
                }
            }
        });
    }

    updateCharts() {
        this.destroyCharts();
        this.renderCharts();
    }

    destroyCharts() {
        Object.values(this.charts).forEach(chart => {
            if (chart) chart.destroy();
        });
        this.charts = {};
    }

    getHealthClass(percent) {
        if (percent >= 80) return 'nd_health_good';
        if (percent >= 50) return 'nd_health_warning';
        return 'nd_health_danger';
    }

    async refreshData() {
        await this.loadData();
        this.notification.add("Dashboard refreshed", { type: "success" });
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

    startAutoRefresh() {
        if (!this.state.auto_refresh) return;

        // Clear existing interval
        this.stopAutoRefresh();

        this.refreshInterval = setInterval(() => {
            if (this.state.auto_refresh) {
                this.loadData();
            }
        }, 60000); // Refresh every 60 seconds
    }

    stopAutoRefresh() {
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
            this.refreshInterval = null;
        }
    }

    // Quick Actions Methods
    viewTotalDevices() {
        this.env.services.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'asset.network.device',
            name: 'Network Devices',
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    viewAllDevices() {
        return this.viewTotalDevices();
    }

    viewOfflineDevices() {
        this.env.services.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'asset.network.device',
            name: 'Offline Network Devices',
            view_mode: 'tree,form',
            views: [[false, 'list'], [false, 'form']],
            domain: [['connection_status', 'in', ['offline', 'unreachable']]],
            target: 'current',
        });
    }

    viewOnlineDevices() {
        this.env.services.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'asset.network.device',
            name: 'Online Network Devices',
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            domain: [['connection_status', '=', 'online']],
            target: 'current',
        });
    }

    viewUnreachableDevices() {
        this.env.services.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'asset.network.device',
            name: 'Unreachable Network Devices',
            view_mode: 'tree,form',
            views: [[false, 'list'], [false, 'form']],
            domain: [['connection_status', '=', 'unreachable']],
            target: 'current',
        });
    }

    viewHighCPUDevices() {
        this.env.services.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'asset.network.device',
            name: 'High CPU Usage Devices',
            view_mode: 'tree,form',
            views: [[false, 'list'], [false, 'form']],
            domain: [['cpu_usage', '>', 80]],
            target: 'current',
        });
    }

    viewHighMemoryDevices() {
        this.env.services.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'asset.network.device',
            name: 'High Memory Usage Devices',
            view_mode: 'tree,form',
            views: [[false, 'list'], [false, 'form']],
            domain: [['memory_usage', '>', 80]],
            target: 'current',
        });
    }

    viewCriticalTemperature() {
        this.env.services.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'asset.network.device',
            name: 'Critical Temperature Devices',
            view_mode: 'tree,form',
            views: [[false, 'list'], [false, 'form']],
            domain: [['ram_used', '>', 0]],
            target: 'current',
        });
    }

    viewRouters() {
        this.env.services.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'asset.network.device',
            name: 'Routers',
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            domain: [['device_type', '=', 'router']],
            target: 'current',
        });
    }

    viewSwitches() {
        this.env.services.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'asset.network.device',
            name: 'Switches',
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            domain: [['device_type', '=', 'switch']],
            target: 'current',
        });
    }

    viewFirewalls() {
        this.env.services.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'asset.network.device',
            name: 'Firewalls',
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            domain: [['device_type', '=', 'firewall']],
            target: 'current',
        });
    }

    viewAccessPoints() {
        this.env.services.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'asset.network.device',
            name: 'Access Points',
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            domain: [['device_type', '=', 'access_point']],
            target: 'current',
        });
    }

    viewGateways() {
        this.env.services.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'asset.network.device',
            name: 'Gateways',
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            domain: [['device_type', '=', 'gateway']],
            target: 'current',
        });
    }

    async exportData() {
        try {
            // Prepare CSV data
            const headers = ['Device Code', 'Name', 'Type', 'IP Address', 'Status', 'CPU %', 'Memory %', 'Last Check'];
            const rows = this.state.devices.map(d => [
                d.device_code,
                d.name,
                d.device_type_label,
                d.ip_address || 'N/A',
                d.connection_status,
                d.cpu_usage.toFixed(1),
                d.memory_usage.toFixed(1),
                d.last_check_formatted
            ]);

            const csvContent = [
                headers.join(','),
                ...rows.map(row => row.map(cell => `"${cell}"`).join(','))
            ].join('\n');

            // Download CSV
            const blob = new Blob([csvContent], { type: 'text/csv' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `network_devices_${new Date().toISOString().split('T')[0]}.csv`;
            a.click();
            window.URL.revokeObjectURL(url);

            this.notification.add("Data exported successfully", { type: "success" });
        } catch (error) {
            console.error("Export failed:", error);
            this.notification.add("Failed to export data", { type: "danger" });
        }
    }

    async downloadPDF() {
        window.print();
    }
}

registry.category("actions").add("action_network_dashboard", NetworkDashboard);