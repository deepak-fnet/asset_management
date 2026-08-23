/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onMounted } from "@odoo/owl";
import { rpc } from "@web/core/network/rpc_service";

export class AssetDashboard extends Component {

    setup() {
        onMounted(this.loadData.bind(this));
        this.charts = {};
    }

    async loadData() {
        const data = await rpc("/asset/dashboard/data", {});

        // KPIs
        const k = data.kpis || {};
        this._setText('total_assets', k.total_assets);
        this._setText('total_amc', k.total_amc);
        this._setText('amc_expiring', k.amc_expiring);
        this._setText('verification_due', k.verification_due);
        this._setText('pending_approvals', k.pending_approvals);
        this._setText('warranty_expiring', k.warranty_expiring);
        this._setText('licence_expiring', k.licence_expiring);
        this._setText('open_transfers', k.open_transfers);

        // Charts
        const charts = data.charts || {};
        this._renderBarDept('chart_assets_by_department', charts.dept);
        this._renderDonut('chart_asset_types', charts.types);
        this._renderLine('chart_additions_by_month', charts.additions);
        this._renderStacked('chart_assets_by_plant', charts.plants);
        this._renderGauge('chart_pending_progress', charts.progress);

        document.getElementById("pending_progress_text").innerText =
            (charts.progress.progress_pct || 0) + "%";

        this._bindButtons();
    }

    _setText(id, value) {
        const el = document.getElementById(id);
        if (el) el.innerText = value || 0;
    }

    // BAR CHART
    _renderBarDept(canvasId, payload) {
        const ctx = document.getElementById(canvasId)?.getContext("2d");
        if (!ctx) return;

        if (this.charts[canvasId]) this.charts[canvasId].destroy();

        this.charts[canvasId] = new Chart(ctx, {
            type: "bar",
            data: {
                labels: payload.labels || [],
                datasets: [{
                    label: "Assets",
                    data: payload.values || [],
                }],
            },
            options: { responsive: true, maintainAspectRatio: false },
        });
    }

    // DONUT
    _renderDonut(canvasId, payload) {
        const ctx = document.getElementById(canvasId)?.getContext("2d");
        if (!ctx) return;

        if (this.charts[canvasId]) this.charts[canvasId].destroy();

        this.charts[canvasId] = new Chart(ctx, {
            type: "doughnut",
            data: {
                labels: payload.labels || [],
                datasets: [{
                    data: payload.values || [],
                    hoverOffset: 4
                }],
            },
            options: { responsive: true, maintainAspectRatio: false },
        });
    }

    // LINE CHART
    _renderLine(canvasId, payload) {
        const ctx = document.getElementById(canvasId)?.getContext("2d");
        if (!ctx) return;

        if (this.charts[canvasId]) this.charts[canvasId].destroy();

        this.charts[canvasId] = new Chart(ctx, {
            type: "line",
            data: {
                labels: payload.labels || [],
                datasets: [{
                    label: "Additions",
                    data: payload.values || [],
                    tension: 0.3,
                    fill: true,
                }],
            },
            options: { responsive: true, maintainAspectRatio: false },
        });
    }

    // STACKED BAR
    _renderStacked(canvasId, payload) {
        const ctx = document.getElementById(canvasId)?.getContext("2d");
        if (!ctx) return;

        if (this.charts[canvasId]) this.charts[canvasId].destroy();

        const datasets = (payload.datasets || []).map(d => ({
            label: d.label,
            data: d.data,
            stack: "stack1",
        }));

        this.charts[canvasId] = new Chart(ctx, {
            type: "bar",
            data: { labels: payload.plants || [], datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: { stacked: true },
                    y: { stacked: true, beginAtZero: true },
                },
            },
        });
    }

    // GAUGE
    _renderGauge(canvasId, payload) {
        const ctx = document.getElementById(canvasId)?.getContext("2d");
        if (!ctx) return;

        if (this.charts[canvasId]) this.charts[canvasId].destroy();

        const pct = payload.progress_pct || 0;

        this.charts[canvasId] = new Chart(ctx, {
            type: "doughnut",
            data: {
                labels: ["Completed", "Remaining"],
                datasets: [{ data: [pct, 100 - pct] }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "70%",
                plugins: { legend: { display: false } },
            },
        });
    }

    // BUTTONS
    _bindButtons() {
        const btnNew = document.getElementById("btn_new_asset");
        if (btnNew) {
            btnNew.addEventListener("click", () => {
                this.env.services.action.doAction({
                    type: "ir.actions.act_window",
                    res_model: "asset.addition",
                    views: [[false, "form"]],
                    target: "current",
                });
            });
        }

        const btnVerify = document.getElementById("btn_verify_assets");
        if (btnVerify) {
            btnVerify.addEventListener("click", () => {
                this.env.services.action.doAction({
                    type: "ir.actions.act_window",
                    res_model: "physical.verification",
                    views: [[false, "kanban"], [false, "tree"]],
                    target: "current",
                });
            });
        }
    }
}

AssetDashboard.template = "asset.asset_dashboard_template";

registry.category("actions").add("asset_dash_tag", AssetDashboard);


