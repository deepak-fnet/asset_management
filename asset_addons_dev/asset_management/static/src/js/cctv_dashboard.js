/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, onMounted, useState } from "@odoo/owl";
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

class CCTVDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            cameras: [],
            stats: {
                total: 0,
                online: 0,
                offline: 0,
                recording: 0,
                motion: 0,
                alerts: 0,
                recordingDisabled: 0,
                recentlyActive: 0,
                scrapped: 0
            },
            events: [],
            alerts: [],
            loading: true,
            lastUpdated: new Date().toLocaleTimeString(),
            autoRefresh: true
        });

        this.charts = {};
        this.chartJsLoaded = false;

        onWillStart(async () => {
            await this.waitForChartJs();
            await this.loadDashboardData();
        });

        onMounted(() => {
            setTimeout(() => {
                if (this.chartJsLoaded) {
                    this.renderCharts();
                }
            }, 300);
        });

        // Auto-refresh every 30 seconds
        this.intervalId = setInterval(() => {
            this.loadDashboardData();
        }, 30000);

        onWillUnmount(() => {
            clearInterval(this.intervalId);
            this.destroyCharts();
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

    renderCharts() {
        this.renderCameraStatusChart();
        this.renderMonitoringChart();
    }

    renderCameraStatusChart() {
        const chartElement = document.getElementById('cameraStatusChart');
        if (!chartElement || !this.chartJsLoaded) return;

        if (this.charts.status) {
            this.charts.status.destroy();
        }

        const data = {
            labels: ['Online', 'Offline', 'Recording'],
            datasets: [{
                data: [this.state.stats.online_idle, this.state.stats.offline, this.state.stats.recording],
                backgroundColor: ['#28a745', '#dc3545', '#fd7e14'],
                hoverOffset: 4,
                borderWidth: 3,
                borderColor: '#ffffff',
                hoverBorderWidth: 4,
                cutout: '72%'
            }]
        };

        this.charts.status = new window.Chart(chartElement, {
            type: 'doughnut',
            data: data,
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: true,
                        position: 'right',
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
                        total: this.state.stats.total,
                        label: 'Cameras'
                    }
                }
            }
        });
    }

    renderMonitoringChart() {
        const chartElement = document.getElementById('cameraMonitoringChart');
        if (!chartElement || !this.chartJsLoaded) return;

        if (this.charts.monitoring) {
            this.charts.monitoring.destroy();
        }

        const total = this.state.stats.alerts + this.state.stats.recentlyActive +
            this.state.stats.recordingDisabled + this.state.stats.scrapped;

        const data = {
            labels: ['Alerts', 'Recently Active', 'Recording Disabled', 'Scrapped'],
            datasets: [{
                data: [
                    this.state.stats.alerts,
                    this.state.stats.recentlyActive,
                    this.state.stats.recordingDisabled,
                    this.state.stats.scrapped
                ],
                backgroundColor: ['#dc3545', '#17a2b8', '#ffc107', '#6c757d'],
                hoverOffset: 4,
                borderWidth: 3,
                borderColor: '#ffffff',
                hoverBorderWidth: 4,
                cutout: '72%'
            }]
        };

        this.charts.monitoring = new window.Chart(chartElement, {
            type: 'doughnut',
            data: data,
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '72%',
                plugins: {
                    legend: {
                        display: true,
                        position: 'right',
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
                        label: 'Monitoring'
                    }
                }
            }
        });
    }

    updateCharts() {
        if (this.charts.status) {
            this.charts.status.data.datasets[0].data = [
                this.state.stats.online_idle,
                this.state.stats.offline,
                this.state.stats.recording
            ];
            this.charts.status.options.plugins.centerText.total = this.state.stats.total;
            this.charts.status.update();
        }

        if (this.charts.monitoring) {
            const total = this.state.stats.alerts + this.state.stats.recentlyActive +
                this.state.stats.recordingDisabled + this.state.stats.scrapped;
            this.charts.monitoring.data.datasets[0].data = [
                this.state.stats.alerts,
                this.state.stats.recentlyActive,
                this.state.stats.recordingDisabled,
                this.state.stats.scrapped
            ];
            this.charts.monitoring.options.plugins.centerText.total = total;
            this.charts.monitoring.update();
        }

        if (!this.charts.status || !this.charts.monitoring) {
            this.renderCharts();
        }
    }

    destroyCharts() {
        if (this.charts.status) {
            this.charts.status.destroy();
        }
        if (this.charts.monitoring) {
            this.charts.monitoring.destroy();
        }
    }

    async loadDashboardData() {
        try {
            // Load all cameras - Removed asset_id filter to show all cameras
            const cameras = await this.orm.searchRead("asset.camera", [], [
                "id", "name", "camera_code", "location", "ip_address", "camera_ip",
                "status", "stream_status",
                "is_online", "is_recording", "is_active", "recording_status", "motion_detected",
                "last_motion_time",  // Added for recently active calculation
                "storage_total_gb", "storage_used_gb", "storage_usage_percent",
                "resolution", "fps", "camera_image"
            ]);

            // Map camera details for events
            const cameraMap = Object.fromEntries(cameras.map(c => [c.id, c]));

            // Pre-process cameras to ensure UI fields are populated correctly
            const processedCameras = cameras.map(c => {
                c.has_image = !!c.camera_image;

                // Use camera_ip as fallback for ip_address
                if (!c.ip_address || c.ip_address === 'false') c.ip_address = c.camera_ip;

                // DO NOT modify is_online or is_recording - use values from database
                // The backend check_camera_status() method is the source of truth

                return c;
            });

            // Load recent camera events (last 10) - Removed asset_id filter
            let events = await this.orm.searchRead("camera.event", [], [
                "id", "camera_id", "event_type", "event_message", "event_time", "severity"
            ], { limit: 10, order: "event_time desc" });

            // Process events with camera info
            const processedEvents = events.map(e => {
                const cameraId = Array.isArray(e.camera_id) ? e.camera_id[0] : e.camera_id;
                const cam = cameraMap[cameraId] || {};
                return {
                    ...e,
                    camera_name: Array.isArray(e.camera_id) ? e.camera_id[1] : (cam.name || "Unknown"),
                    camera_id_raw: cameraId,
                    camera_code: cam.camera_code || "CAM-000",
                    has_image: !!cam.camera_image,
                };
            });

            // Load active alerts (not acknowledged and high severity)
            const alerts = await this.orm.searchRead("camera.event", [
                ["is_acknowledged", "=", false],
                ["severity", "in", ["warning", "critical"]]
            ], [
                "id", "camera_id", "event_type", "event_message", "event_time", "severity"
            ], { order: "event_time desc" });

            const processedAlerts = alerts.map(a => {
                const cameraId = Array.isArray(a.camera_id) ? a.camera_id[0] : a.camera_id;
                const cam = cameraMap[cameraId] || {};
                return {
                    ...a,
                    camera_name: Array.isArray(a.camera_id) ? a.camera_id[1] : (cam.name || "Unknown"),
                    camera_id_raw: cameraId,
                    camera_code: cam.camera_code || "CAM-000",
                    has_image: !!cam.camera_image,
                };
            });

            // Filter only active cameras for main stats (exclude scrapped)
            const activeCameras = processedCameras.filter(c => c.is_active !== false);
            const scrappedCameras = processedCameras.filter(c => c.is_active === false);

            // Update stats - ensure mutual exclusivity for the donut chart
            // Recording: cameras that are actively recording (highest priority status)
            const recordingCameras = activeCameras.filter(c => c.status === 'recording' || c.is_recording || c.recording_status);

            // Online (idle): cameras that are online but NOT recording
            const onlineIdleCameras = activeCameras.filter(c =>
                !recordingCameras.includes(c) && (c.status === 'online' || c.is_online)
            );

            // Offline: cameras that are neither recording nor online (active cameras only)
            const offlineCameras = activeCameras.filter(c =>
                !recordingCameras.includes(c) && !onlineIdleCameras.includes(c)
            );

            // Recently active: cameras with motion detected or recent motion (within last hour)
            const now = new Date();
            const recentlyActiveCameras = activeCameras.filter(c => {
                if (c.motion_detected) return true;
                if (c.last_motion_time) {
                    const lastMotion = new Date(c.last_motion_time);
                    const diffMs = now - lastMotion;
                    return diffMs < 3600000; // 1 hour in milliseconds
                }
                return false;
            });

            // Recording disabled: cameras that are online but should be recording but aren't
            // (online cameras that are not currently recording)
            const recordingDisabledCameras = onlineIdleCameras;

            const stats = {
                total: activeCameras.length,                    // Only count active cameras
                online: recordingCameras.length + onlineIdleCameras.length,  // All online (recording + idle)
                offline: offlineCameras.length,                 // Active cameras that are offline
                recording: recordingCameras.length,             // Cameras actively recording
                online_idle: onlineIdleCameras.length,          // Online but not recording (for chart)
                motion: activeCameras.filter(c => c.motion_detected).length,

                // Monitoring metrics
                alerts: processedAlerts.length,
                recordingDisabled: recordingDisabledCameras.length,  // Online but not recording
                recentlyActive: recentlyActiveCameras.length,        // Motion detected recently
                scrapped: scrappedCameras.length                     // Inactive/decommissioned cameras
            };

            this.state.cameras = processedCameras;
            this.state.events = processedEvents;
            this.state.alerts = processedAlerts;
            this.state.stats = stats;
            this.state.loading = false;
            this.state.lastUpdated = new Date().toLocaleTimeString();

            if (this.chartJsLoaded) {
                this.updateCharts();
            }
        } catch (error) {
            console.error("Error loading CCTV dashboard data:", error);
            this.state.loading = false;
        }
    }

    toggleAutoRefresh() {
        this.state.autoRefresh = !this.state.autoRefresh;
        if (this.state.autoRefresh) {
            this.intervalId = setInterval(() => {
                this.loadDashboardData();
            }, 30000);
        } else {
            clearInterval(this.intervalId);
        }
    }

    resolveCameraImage(cameraId) {
        const camera = this.state.cameras.find(c => c.id === cameraId);
        if (camera && camera.has_image) {
            return `/web/image/asset.camera/${cameraId}/camera_image`;
        }
        return '/asset_management/static/src/img/asset_cctv_default.png';
    }

    async refreshData() {
        this.state.loading = true;
        await this.loadDashboardData();
    }

    getEventIcon(eventType) {
        const icons = {
            'offline': 'fa fa-chain-broken',
            'online': 'fa fa-link',
            'motion': 'fa fa-bolt',
            'recording_started': 'fa fa-play-circle',
            'recording_stopped': 'fa fa-stop-circle',
            'stream_error': 'fa fa-exclamation-triangle',
            'storage_warning': 'fa fa-hdd-o',
            'storage_critical': 'fa fa-hdd-o text-danger'
        };
        return icons[eventType] || 'fa fa-info-circle';
    }

    getEventIconClass(eventType) {
        const classes = {
            'motion': 'bg-success-light text-success',
            'offline': 'bg-danger-light text-danger',
            'online': 'bg-success-light text-success',
            'recording_started': 'bg-info-light text-info',
            'recording_stopped': 'bg-warning-light text-warning',
        };
        return classes[eventType] || 'bg-light text-muted';
    }

    getEventDisplayIcon(eventType) {
        const icons = {
            'motion': 'fa fa-check',
            'offline': 'fa fa-times',
            'online': 'fa fa-check',
            'recording_started': 'fa fa-video-camera',
            'recording_stopped': 'fa fa-stop',
        };
        return icons[eventType] || 'fa fa-info';
    }

    async downloadPDF() {
        window.print();
    }

    async viewAllCameras() {
        // Show all active cameras (excludes scrapped)
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'All CCTV Cameras',
            res_model: 'asset.camera',
            domain: [['is_active', '!=', false]],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    async viewOnlineCameras() {
        // Show all online cameras (including recording) - active only
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Online Cameras',
            res_model: 'asset.camera',
            domain: [
                ['is_active', '!=', false],
                '|',
                ['is_online', '=', true],
                ['is_recording', '=', true]
            ],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    async viewOfflineCameras() {
        // Show offline cameras - active only, exclude recording cameras
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Offline Cameras',
            res_model: 'asset.camera',
            domain: [
                ['is_active', '!=', false],
                ['is_online', '=', false],
                ['is_recording', '=', false]
            ],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    async viewRecordingCameras() {
        // Show cameras that are actively recording - active only
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Recording Cameras',
            res_model: 'asset.camera',
            domain: [
                ['is_active', '!=', false],
                '|', '|',
                ['is_recording', '=', true],
                ['recording_status', '=', true],
                ['status', '=', 'recording']
            ],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    async viewRecentlyActiveCameras() {
        // Show cameras with recent motion detected - active only
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Recently Active Cameras',
            res_model: 'asset.camera',
            domain: [
                ['is_active', '!=', false],
                ['motion_detected', '=', true]
            ],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    async viewRecordingDisabledCameras() {
        // Show online cameras that are NOT recording - active only
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Recording Disabled Cameras',
            res_model: 'asset.camera',
            domain: [
                ['is_active', '!=', false],
                ['is_online', '=', true],
                ['is_recording', '=', false],
                ['recording_status', '=', false]
            ],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    async viewScrappedCameras() {
        // Show inactive/scrapped cameras
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Scrapped Cameras',
            res_model: 'asset.camera',
            domain: [['is_active', '=', false]],
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    async viewAlerts() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Active Alerts',
            res_model: 'camera.event',
            domain: [['is_acknowledged', '=', false], ['severity', 'in', ['warning', 'critical']]],
            view_mode: 'tree,form',
            views: [[false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    async viewAllEvents() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: 'Camera Events',
            res_model: 'camera.event',
            domain: [],
            view_mode: 'tree,form',
            views: [[false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    async acknowledgeAlert(alertId) {
        await this.orm.write("camera.event", [alertId], {
            is_acknowledged: true,
            acknowledged_time: new Date(),
            acknowledged_by: this.orm.user_id
        });
        await this.loadDashboardData();
    }
}

CCTVDashboard.template = "cctv_dashboard.Dashboard";
registry.category("actions").add("cctv_dashboard", CCTVDashboard);
