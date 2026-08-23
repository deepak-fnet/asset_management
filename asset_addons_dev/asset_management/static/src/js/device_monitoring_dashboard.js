/** @odoo-module **/

import { Component, useState, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { deserializeDateTime } from "@web/core/l10n/dates";

export class DeviceMonitoringDashboard extends Component {
    static template = "asset_management.DeviceMonitoringDashboard";

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");

        // Get platform from context or default to windows
        const platform = this.props.action.context?.default_platform || 'windows';
        console.log("[DeviceMonitoring] Setup with platform:", platform, "from context:", this.props.action.context);

        this.state = useState({
            assets: [],
            selectedAssetId: null,
            selectedAsset: null,
            searchQuery: "",
            loading: true,
            selectedPlatform: platform,
        });

        onWillStart(async () => {
            console.log("[DeviceMonitoring] Starting loadAssets...");
            await this.loadAssets();
        });

        onMounted(() => {
            console.log("[DeviceMonitoring] Component mounted, starting auto-refresh...");
            this.startAutoRefresh();
        });

        onWillUnmount(() => {
            console.log("[DeviceMonitoring] Component unmounting, stopping auto-refresh...");
            this.stopAutoRefresh();
        });
    }

    async loadAssets() {
        this.state.loading = true;
        try {
            // Load asset basic info - include all necessary fields
            const assetDomain = [["state", "!=", "scrapped"]];
            const assetFields = [
                "id", "asset_name", "asset_code", "serial_number",
                "assigned_employee_id", "department_id",
                "agent_status", "health_status",
                "ram_size", "rom_size", "last_agent_sync",
                "os_platform", "is_camera", "category_id"
            ];
            
            console.log("[DeviceMonitoring] Loading assets with domain:", assetDomain);
            const assets = await this.orm.searchRead("asset.asset", assetDomain, assetFields);
            console.log("[DeviceMonitoring] Loaded", assets.length, "assets");

            // Load live monitoring data
            const monitoringFields = [
                "asset_id", "serial_number", "is_online", "heartbeat",
                "cpu_usage_percent", "ram_usage_percent", "disk_usage_percent",
                "battery_percentage", "uptime", "recording_status",
                "motion_detected", "last_motion_time"
            ];
            const monitoring = await this.orm.searchRead("asset.live.monitoring", [], monitoringFields);
            console.log("[DeviceMonitoring] Loaded", monitoring.length, "monitoring records");

            // Create a map of asset_id -> monitoring data
            const monitoringMap = {};
            monitoring.forEach(m => {
                if (m.asset_id) {
                    monitoringMap[m.asset_id[0]] = m;
                }
            });

            // Merge asset data with live monitoring
            this.state.assets = assets.map(asset => {
                const liveData = monitoringMap[asset.id] || {};
                const isOnline = liveData.is_online || false;

                // Determine platform - handle boolean is_camera properly
                let platform = 'unknown';
                const isCamera = asset.is_camera === true || asset.is_camera === 1;
                if (isCamera) {
                    platform = 'cctv';
                } else if (asset.os_platform === 'linux') {
                    platform = 'ubuntu';
                } else if (asset.os_platform === 'macos') {
                    platform = 'macos';
                } else if (asset.os_platform === 'windows') {
                    platform = 'windows';
                }

                // Get actual values from backend (always stored, never overwritten)
                const actualCpu = liveData.cpu_usage_percent || 0.0;
                const actualRam = liveData.ram_usage_percent || 0.0;
                const actualDisk = liveData.disk_usage_percent || 0.0;
                const actualBattery = liveData.battery_percentage || 0.0;

                // Display values: Show 0 when offline, actual values when online
                const displayCpu = isOnline ? actualCpu : 0.0;
                const displayRam = isOnline ? actualRam : 0.0;
                const displayDisk = isOnline ? actualDisk : 0.0;
                const displayBattery = isOnline ? actualBattery : 0.0;

                const formatTimestamp = (utcTimestamp) => {
                    if (!utcTimestamp) return "Never";
                    try {
                        const dt = deserializeDateTime(utcTimestamp);
                        return dt.toFormat('dd/MM/yyyy, HH:mm:ss');
                    } catch (e) {
                        return "Invalid date";
                    }
                };

                return {
                    ...asset,
                    platform: platform,
                    is_camera: isCamera,
                    assigned_employee_name: asset.assigned_employee_id ? asset.assigned_employee_id[1] : false,
                    department_name: asset.department_id ? asset.department_id[1] : false,
                    last_agent_sync: formatTimestamp(liveData.heartbeat || asset.last_agent_sync),

                    // Live metrics - Display values (0 when offline, real when online)
                    cpu_usage: displayCpu,
                    memory_usage: displayRam,
                    storage_usage: displayDisk,
                    battery_level: displayBattery,
                    is_online: isOnline,

                    // Ubuntu/Linux specific
                    uptime: liveData.uptime || 'Unknown',

                    // CCTV specific
                    recording_status: liveData.recording_status || 'unknown',
                    motion_detected: liveData.motion_detected || false,
                    last_motion_time: formatTimestamp(liveData.last_motion_time),

                    // Store actual values (for reference, not overwritten)
                    _actualCpu: actualCpu,
                    _actualRam: actualRam,
                    _actualDisk: actualDisk,
                    _actualBattery: actualBattery,

                    // Trends (set to 0 for now, can be computed from history later)
                    cpu_trend: 0,
                    memory_trend: 0,
                    storage_trend: 0,
                    battery_trend: 0
                };
            });

            console.log("[DeviceMonitoring] Processed", this.state.assets.length, "assets with platforms:", 
                this.state.assets.map(a => `${a.asset_name}(${a.platform})`).join(", "));

            // Auto-select first asset if none selected
            if (!this.state.selectedAssetId && this.state.assets.length > 0) {
                const firstPlatformAsset = this.displayedAssets[0];
                console.log("[DeviceMonitoring] Auto-selecting first asset:", firstPlatformAsset?.asset_name);
                if (firstPlatformAsset) {
                    this.selectAsset(firstPlatformAsset.id);
                }
            } else if (this.state.selectedAssetId) {
                // Refresh the selected asset data
                console.log("[DeviceMonitoring] Refreshing selected asset ID:", this.state.selectedAssetId);
                this.selectAsset(this.state.selectedAssetId);
            } else {
                console.log("[DeviceMonitoring] No assets to select");
            }
        } catch (error) {
            console.error("[DeviceMonitoring] Failed to load assets:", error);
            this.notification.add("Failed to load devices: " + error.message, { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    selectAsset(assetId) {
        this.state.selectedAssetId = assetId;
        this.state.selectedAsset = this.state.assets.find(a => a.id === assetId);
    }

    setPlatform(platform) {
        this.state.selectedPlatform = platform;
        this.state.searchQuery = "";
        
        // Select the first asset of the new platform if available
        const firstAsset = this.displayedAssets[0];
        if (firstAsset) {
            this.selectAsset(firstAsset.id);
        } else {
            this.state.selectedAssetId = null;
            this.state.selectedAsset = null;
        }
    }

    onSearchInput(ev) {
        this.state.searchQuery = ev.target.value.toLowerCase();
    }

    get displayedAssets() {
        const filtered = this.state.assets.filter(asset => {
            const matches = asset.platform === this.state.selectedPlatform;
            console.log("[DeviceMonitoring] Filtering asset:", asset.asset_name, 
                "asset.platform=", asset.platform, 
                "selectedPlatform=", this.state.selectedPlatform, 
                "matches=", matches);
            return matches;
        });
        console.log("[DeviceMonitoring] Displayed assets count:", filtered.length, "of", this.state.assets.length);

        if (this.state.searchQuery) {
            return filtered.filter(asset =>
                asset.asset_name.toLowerCase().includes(this.state.searchQuery) ||
                asset.asset_code.toLowerCase().includes(this.state.searchQuery) ||
                (asset.assigned_employee_name && asset.assigned_employee_name.toLowerCase().includes(this.state.searchQuery))
            );
        }
        return filtered;
    }

    getStatusClass(asset) {
        return asset.is_online ? 'healthy' : 'offline';
    }

    getStatusLabel(asset) {
        return asset.is_online ? 'Online' : 'Offline';
    }

    getProgressBarClass(value) {
        if (value < 60) return 'bg-healthy';
        if (value < 85) return 'bg-warning';
        return 'bg-danger';
    }

    getBatteryBarClass(value) {
        if (value > 60) return 'bg-healthy';
        if (value > 20) return 'bg-warning';
        return 'bg-danger';
    }

    getRecordingStatusClass(status) {
        switch (status) {
            case 'recording': return 'text-success';
            case 'stopped': return 'text-danger';
            case 'paused': return 'text-warning';
            default: return 'text-muted';
        }
    }

    getMotionDetectedClass(detected) {
        return detected ? 'text-danger' : 'text-muted';
    }

    async refreshAssetData() {
        await this.loadAssets();
        this.notification.add("Data refreshed", { type: "success" });
    }

    startAutoRefresh() {
        this.refreshInterval = setInterval(() => {
            this.loadAssets();
        }, 30000);
    }

    stopAutoRefresh() {
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
        }
    }
}

registry.category("actions").add("action_device_monitoring", DeviceMonitoringDashboard);
