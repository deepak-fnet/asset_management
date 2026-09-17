/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Layout } from "@web/search/layout";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { Component, useState, onWillStart } from "@odoo/owl";

export class ConstructionDashboard extends Component {
    static template = "construction_management.Dashboard";
    static components = { Layout };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.display = { controlPanel: {} };
        this.env.config.setDisplayName(this.props.action.name || _t("Dashboard"));
        this.state = useState({
            customerId: "",
            masterId: "",
            projectId: "",
            data: {
                customers: [],
                masters: [],
                subprojects: [],
                summary: {},
            },
        });
        onWillStart(() => this.fetchData());
    }

    async fetchData() {
        const data = await this.orm.call("cm.dashboard", "get_dashboard_data", [], {
            partner_id: this.state.customerId || false,
            master_id: this.state.masterId || false,
            project_id: this.state.projectId || false,
        });
        this.state.data = data;
    }

    onChangeCustomer(ev) {
        this.state.customerId = ev.target.value;
        this.state.masterId = "";
        this.state.projectId = "";
        this.fetchData();
    }

    onChangeMaster(ev) {
        this.state.masterId = ev.target.value;
        this.state.projectId = "";
        this.fetchData();
    }

    onChangeProject(ev) {
        this.state.projectId = ev.target.value;
        this.fetchData();
    }

    clearFilters() {
        this.state.customerId = "";
        this.state.masterId = "";
        this.state.projectId = "";
        this.fetchData();
    }

    formatMoney(value) {
        if (!value && value !== 0) {
            return "-";
        }
        return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(value);
    }

    statusColor(status) {
        return {
            draft: "secondary",
            not_started: "secondary",
            active: "primary",
            in_progress: "primary",
            completed: "info",
            certified: "success",
            closed: "success",
        }[status] || "secondary";
    }

    progressColor(pct) {
        if (pct >= 100) return "success";
        if (pct >= 50) return "primary";
        if (pct >= 25) return "warning";
        return "danger";
    }

    initials(name) {
        if (!name) return "?";
        return name.trim().charAt(0).toUpperCase();
    }

    openRecord(model, id) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add("cm_dashboard_client_action", ConstructionDashboard);
