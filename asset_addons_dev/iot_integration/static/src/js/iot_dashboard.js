/** @odoo-module **/

import { Component, useState, useRef, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadJS } from "@web/core/assets";

/**
 * IoT Dashboard - filters, KPI tiles, a temperature trend, and the most
 * recent door-open events, across the readings this module has received.
 *
 * Same concept/structure as asset_workspace's All Asset Dashboard (filters
 * row, KPI grid, a Chart.js chart, a drill-down table) - rebuilt small here
 * since iot.data is a standalone model with no relation to asset.asset.
 */
class IotDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.chartRef = useRef("temperatureChart");
        this.chart = null;

        this.state = useState({
            loading: true,
            filters: { device_id: "", date_from: "", date_to: "" },
            devices: [],
            kpis: {},
            door_events: [],
        });

        onWillStart(async () => {
            await loadJS("/web/static/lib/Chart/Chart.js");
            await Promise.all([this.loadFilterOptions(), this.load()]);
        });

        onMounted(() => this.renderChart());
        onWillUnmount(() => this.destroyChart());
    }

    async loadFilterOptions() {
        try {
            const data = await this.orm.call("iot.dashboard", "get_filter_options", []);
            this.state.devices = data.devices || [];
        } catch (err) {
            console.error(err);
        }
    }

    async load() {
        this.state.loading = true;
        try {
            const data = await this.orm.call(
                "iot.dashboard", "get_dashboard_data", [this.state.filters]
            );
            this.state.kpis = data.kpis || {};
            this.state.door_events = data.door_events || [];
            this.temperatureSeries = data.temperature_series || { labels: [], data: [] };
        } catch (err) {
            this.notification.add("Could not load the IoT dashboard.", { type: "danger" });
            console.error(err);
        } finally {
            this.state.loading = false;
            setTimeout(() => this.renderChart());
        }
    }

    onFilterChange(key, ev) {
        this.state.filters[key] = ev.target.value;
    }

    async onApply() {
        await this.load();
    }

    onClear() {
        this.state.filters = { device_id: "", date_from: "", date_to: "" };
        this.load();
    }

    async onRefresh() {
        await this.load();
    }

    destroyChart() {
        if (this.chart) {
            this.chart.destroy();
            this.chart = null;
        }
    }

    renderChart() {
        this.destroyChart();
        const canvas = this.chartRef.el;
        const series = this.temperatureSeries;
        if (!canvas || !series || !series.labels.length) {
            return;
        }
        this.chart = new Chart(canvas, {
            type: "line",
            data: {
                labels: series.labels,
                datasets: [{
                    label: "Temperature (°C)",
                    data: series.data,
                    borderColor: "#2c7be5",
                    backgroundColor: "rgba(44, 123, 229, 0.15)",
                    tension: 0.3,
                    fill: true,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: { y: { ticks: { precision: 1 } } },
            },
        });
    }

    async openIotData() {
        try {
            const action = await this.orm.call("iot.dashboard", "open_iot_data", [this.state.filters]);
            this.action.doAction(action);
        } catch (err) {
            console.error(err);
        }
    }

    async openDoorEvents() {
        try {
            const action = await this.orm.call("iot.dashboard", "open_door_events", [this.state.filters]);
            this.action.doAction(action);
        } catch (err) {
            console.error(err);
        }
    }

    async openAlertRecords() {
        try {
            const action = await this.orm.call("iot.dashboard", "open_alert_records", [this.state.filters]);
            this.action.doAction(action);
        } catch (err) {
            console.error(err);
        }
    }

    async openAlertRules() {
        try {
            const action = await this.orm.call("iot.dashboard", "open_alert_rules", []);
            this.action.doAction(action);
        } catch (err) {
            console.error(err);
        }
    }
}

IotDashboard.template = "iot_integration.IotDashboard";
registry.category("actions").add("iot_dashboard", IotDashboard);

export default IotDashboard;
