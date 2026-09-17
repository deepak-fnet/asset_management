/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Layout } from "@web/search/layout";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { Component, useState, onWillStart } from "@odoo/owl";

export class ConstructionFlowchart extends Component {
    static template = "construction_management.Flowchart";
    static components = { Layout };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.display = { controlPanel: {} };
        this.env.config.setDisplayName(this.props.action.name || _t("Flow Chart"));
        const ctxMasterId = this.props.action && this.props.action.context
            && this.props.action.context.default_master_id;
        // Opened from a smart button (CRM lead / Master Project) -> lock to that
        // master's own data only, no picker to wander into other projects.
        this.lockedToMaster = !!ctxMasterId;
        this.state = useState({
            masterId: ctxMasterId ? String(ctxMasterId) : "",
            projectId: "",
            stageId: "",
            data: {
                masters: [],
                master: false,
                subprojects: [],
                selected_project: false,
                stages: [],
                selected_stage: false,
                tasks: [],
            },
        });
        onWillStart(() => this.fetchData());
    }

    async fetchData() {
        const data = await this.orm.call("cm.dashboard", "get_flowchart_data", [], {
            master_id: this.state.masterId || false,
            project_id: this.state.projectId || false,
            stage_id: this.state.stageId || false,
        });
        this.state.data = data;
    }

    onChangeMaster(ev) {
        this.state.masterId = ev.target.value;
        this.state.projectId = "";
        this.state.stageId = "";
        this.fetchData();
    }

    selectProject(id) {
        this.state.projectId = String(id);
        this.state.stageId = "";
        this.fetchData();
    }

    selectStage(id) {
        this.state.stageId = String(id);
        this.fetchData();
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

    formatMoney(value) {
        if (!value && value !== 0) {
            return "-";
        }
        return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(value);
    }

    financeClass(value) {
        return value < 0 ? "o_flow_finance_loss" : "";
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
}

registry.category("actions").add("cm_flowchart_client_action", ConstructionFlowchart);
