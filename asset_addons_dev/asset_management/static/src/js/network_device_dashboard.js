/** @odoo-module **/

import { Component, useState, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { deserializeDateTime } from "@web/core/l10n/dates";

export class NetworkDeviceDashboard extends Component {
    static template = "asset_management.NetworkDeviceDashboard";

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");

        this.state = useState({
            devices: [],
            selectedDeviceId: null,
            selectedDevice: null,
            searchQuery: "",
            filterDeviceType: "",
            filterManufacturer: "",
            filterStatus: "",
            loading: true,
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

        onWillStart(async () => {
            await this.loadDevices();
        });

        onMounted(() => {
            this.startAutoRefresh();
        });

        onWillUnmount(() => {
            this.stopAutoRefresh();
        });
    }

    async loadDevices() {
        this.state.loading = true;
        try {
            const deviceFields = [
                "id", "name", "device_code", "device_type", "ip_address",
                "manufacturer", "location", "connection_status", "last_check",
                "last_online", "response_time", "uptime", "uptime_seconds",
                "cpu_usage", "memory_usage", "memory_total", "memory_used",
                "ram_total", "ram_used",
                "total_interfaces", "active_interfaces",
                "is_active", "notes", "firmware_version", "serial_number",
                "snmp_port", "snmp_version", "snmp_community"
            ];

            const devices = await this.orm.searchRead(
                "asset.network.device",
                [["is_active", "=", true]],
                deviceFields
            );

            this.state.devices = devices.map(device => this.formatDevice(device));

            if (!this.state.selectedDeviceId && this.displayedDevices.length > 0) {
                await this.selectDevice(this.displayedDevices[0].id);
            } else if (this.state.selectedDeviceId) {
                await this.selectDevice(this.state.selectedDeviceId);
            }
        } catch (error) {
            console.error("Failed to load network devices:", error);
            this.notification.add("Failed to load network devices", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    formatDevice(device) {
        const formatTimestamp = (utcTimestamp) => {
            if (!utcTimestamp) return null;
            try {
                const dt = deserializeDateTime(utcTimestamp);
                return dt.toFormat('dd/MM/yyyy, HH:mm:ss');
            } catch {
                return utcTimestamp;
            }
        };

        return {
            ...device,
            device_type_label: this.deviceTypeLabels[device.device_type] || device.device_type,
            manufacturer_label: this.manufacturerLabels[device.manufacturer] || device.manufacturer,
            last_check_formatted: formatTimestamp(device.last_check),
            last_online_formatted: formatTimestamp(device.last_online),
            cpu_usage: device.cpu_usage || 0,
            memory_usage: device.memory_usage || 0,
            memory_total: device.memory_total || 0,
            memory_used: device.memory_used || 0,
            ram_total: device.ram_total || 0,
            ram_used: device.ram_used || 0,
            total_interfaces: device.total_interfaces || 0,
            active_interfaces: device.active_interfaces || 0,
            response_time: device.response_time || 0,
            interfaces: []
        };
    }

    async selectDevice(deviceId) {
        this.state.selectedDeviceId = deviceId;
        const device = this.state.devices.find(d => d.id === deviceId);
        if (device) {
            const interfaces = await this.loadInterfaces(deviceId);
            this.state.selectedDevice = { ...device, interfaces };
        } else {
            this.state.selectedDevice = null;
        }
    }

    async loadInterfaces(deviceId) {
        try {
            const interfaceFields = [
                "id", "name", "interface_index", "interface_type", "mac_address",
                "ip_address", "subnet_mask", "admin_status", "oper_status",
                "speed", "mtu", "bytes_in", "bytes_out", "packets_in",
                "packets_out", "errors_in", "errors_out", "bandwidth_usage"
            ];
            return await this.orm.searchRead(
                "network.device.interface",
                [["device_id", "=", deviceId]],
                interfaceFields
            );
        } catch (error) {
            console.error("Failed to load interfaces:", error);
            return [];
        }
    }

    onSearchInput(ev) { this.state.searchQuery = ev.target.value.toLowerCase(); }
    onDeviceTypeChange(ev) { this.state.filterDeviceType = ev.target.value; }
    onManufacturerChange(ev) { this.state.filterManufacturer = ev.target.value; }
    onStatusChange(ev) { this.state.filterStatus = ev.target.value; }

    get displayedDevices() {
        let filtered = this.state.devices;
        if (this.state.filterDeviceType) filtered = filtered.filter(d => d.device_type === this.state.filterDeviceType);
        if (this.state.filterManufacturer) filtered = filtered.filter(d => d.manufacturer === this.state.filterManufacturer);
        if (this.state.filterStatus) filtered = filtered.filter(d => d.connection_status === this.state.filterStatus);
        if (this.state.searchQuery) {
            filtered = filtered.filter(d =>
                d.name.toLowerCase().includes(this.state.searchQuery) ||
                d.device_code.toLowerCase().includes(this.state.searchQuery) ||
                d.ip_address.toLowerCase().includes(this.state.searchQuery)
            );
        }
        return filtered;
    }

    get onlineCount() { return this.displayedDevices.filter(d => d.connection_status === 'online').length; }
    get offlineCount() { return this.displayedDevices.filter(d => d.connection_status === 'offline').length; }

    getStatusLabel(status) {
        return { 'online': 'Online', 'offline': 'Offline', 'unreachable': 'Unreachable', 'unknown': 'Unknown' }[status] || 'Unknown';
    }
    getStatusBadgeClass(status) {
        return { 'online': 'nd_badge_success', 'offline': 'nd_badge_danger', 'unreachable': 'nd_badge_warning', 'unknown': 'nd_badge_secondary' }[status] || 'nd_badge_secondary';
    }
    getStatusTextClass(status) {
        return { 'online': 'text-success', 'offline': 'text-danger', 'unreachable': 'text-warning', 'unknown': 'text-muted' }[status] || 'text-muted';
    }
    getStatusIconClass(status) {
        return { 'online': 'bg-success-light', 'offline': 'bg-danger-light', 'unreachable': 'bg-warning-light', 'unknown': 'bg-secondary-light' }[status] || 'bg-secondary-light';
    }
    getStatusIcon(status) {
        return { 'online': 'fa fa-check-circle text-success', 'offline': 'fa fa-times-circle text-danger', 'unreachable': 'fa fa-exclamation-circle text-warning', 'unknown': 'fa fa-question-circle text-muted' }[status] || 'fa fa-question-circle text-muted';
    }
    getProgressBarClass(value) {
        if (value < 60) return 'bg-healthy';
        if (value < 85) return 'bg-warning';
        return 'bg-danger';
    }

    formatBytes(bytes) {
        if (!bytes || bytes === 0) return '0 B';
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        return parseFloat((bytes / Math.pow(1024, i)).toFixed(2)) + ' ' + sizes[i];
    }

    formatSpeed(speed) {
        if (!speed) return 'Unknown';
        const speedNum = parseInt(speed);
        if (isNaN(speedNum)) return speed;
        if (speedNum >= 1000000000) return (speedNum / 1000000000).toFixed(0) + ' Gbps';
        if (speedNum >= 1000000) return (speedNum / 1000000).toFixed(0) + ' Mbps';
        if (speedNum >= 1000) return (speedNum / 1000).toFixed(0) + ' Kbps';
        return speedNum + ' bps';
    }

    async refreshDeviceData() {
        if (!this.state.selectedDeviceId) return;
        try {
            await this.orm.call("asset.network.device", "check_device_status", [[this.state.selectedDeviceId]]);
            await this.loadDevices();
            this.notification.add("Device data refreshed", { type: "success" });
        } catch (error) {
            this.notification.add("Failed to refresh device data", { type: "danger" });
        }
    }

    async testSnmpConnection() {
        if (!this.state.selectedDeviceId) return;
        try {
            const result = await this.orm.call("asset.network.device", "test_snmp_connection", [[this.state.selectedDeviceId]]);
            if (result && result.type === 'ir.actions.client') {
                this.notification.add(result.params.message, {
                    type: result.params.type === 'success' ? 'success' : 'danger',
                    title: result.params.title
                });
            }
        } catch (error) {
            this.notification.add("SNMP connection test failed", { type: "danger" });
        }
    }

    async refreshInterfaces() {
        if (!this.state.selectedDeviceId) return;
        try {
            await this.orm.call("asset.network.device", "refresh_interfaces", [[this.state.selectedDeviceId]]);
            const interfaces = await this.loadInterfaces(this.state.selectedDeviceId);
            if (this.state.selectedDevice) this.state.selectedDevice.interfaces = interfaces;
            await this.loadDevices();
            this.notification.add("Interfaces refreshed", { type: "success" });
        } catch (error) {
            this.notification.add("Failed to refresh interfaces", { type: "danger" });
        }
    }

    startAutoRefresh() {
        this.refreshInterval = setInterval(() => this.loadDevices(), 30000);
    }

    stopAutoRefresh() {
        if (this.refreshInterval) clearInterval(this.refreshInterval);
    }
}

registry.category("actions").add("action_network_live_monitoring", NetworkDeviceDashboard);