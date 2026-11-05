/** @odoo-module **/

import { Component, useState, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

// ── ComplianceDialog ──────────────────────────────────────────────────────────
class ComplianceDialog extends Component {
    static template = "payment_compliance_alert.ComplianceDialog";
    static props = {
        close:          { type: Function },
        moveId:         { type: Number },
        billName:       { type: String },
        vendorName:     { type: String },
        daysOverdue:    { type: Number },
        amountResidual: { type: String },
        currencySymbol: { type: String },
        alertType:      { type: String },
        udyogAdhaar:    { type: String },
        onAcknowledged: { type: Function, optional: true },
    };

    setup() {
        this.state        = useState({ loading: false });
        this.orm          = useService("orm");
        this.notification = useService("notification");
    }

    async onAcknowledge() {
        this.state.loading = true;
        try {
            const result = await this.orm.call(
                "account.move", "acknowledge_compliance_alert",
                [this.props.moveId, this.props.alertType]
            );
            if (result.success) {
                this.notification.add(
                    "Compliance alert acknowledged and logged.",
                    { type: "success", title: "Acknowledged" }
                );
                if (this.props.onAcknowledged) await this.props.onAcknowledged();
                this.props.close();
            } else {
                this.notification.add(result.error || "Failed.", { type: "danger" });
            }
        } catch (e) {
            this.notification.add("Error. Please try again.", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    async onSnooze() {
        try {
            await this.orm.call("account.move", "snooze_compliance_alert",
                [this.props.moveId, this.props.alertType]);
        } catch (e) {}
        this.props.close();
    }

    onGoToBill() { this.props.close(); }
}

registry.category("dialogs").add("compliance_alert_dialog", ComplianceDialog);


// ── ComplianceSystray ─────────────────────────────────────────────────────────
class ComplianceSystray extends Component {
    static template = "payment_compliance_alert.ComplianceSystray";

    setup() {
        this.orm    = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");

        this.state = useState({
            count: 0, count_45: 0, count_180: 0,
            bills: [], open: false,
        });

        this._pollInterval = null;
        this._boundClose   = this._onOutsideClick.bind(this);

        onMounted(async () => {
            await this._loadData();
            this._pollInterval = setInterval(() => this._loadData(), 5 * 60 * 1000);
            document.addEventListener("click", this._boundClose);
        });

        onWillUnmount(() => {
            if (this._pollInterval) clearInterval(this._pollInterval);
            document.removeEventListener("click", this._boundClose);
        });
    }

    async _loadData() {
        try {
            const r = await this.orm.call("account.move", "get_compliance_alert_count", []);
            this.state.count     = r.count     || 0;
            this.state.count_45  = r.count_45  || 0;
            this.state.count_180 = r.count_180 || 0;
            this.state.bills     = r.bills      || [];
        } catch (e) {}
    }

    _onOutsideClick(ev) {
        const el = document.querySelector(".o_compliance_systray_item");
        if (el && !el.contains(ev.target)) this.state.open = false;
    }

    toggleDropdown() {
        this.state.open = !this.state.open;
        if (this.state.open) this._loadData();
    }

    openBill(moveId) {
        this.state.open = false;
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "account.move",
            res_id: moveId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    // View only MSME 45-day bills — uses the saved compliance view
    viewMsmeAlerts() {
        this.state.open = false;
        this.action.doAction("payment_compliance_alert.action_msme_45_compliance_alerts");
    }

    // View only 180-day general bills — uses the saved compliance view
    view180Alerts() {
        this.state.open = false;
        this.action.doAction("payment_compliance_alert.action_180_day_compliance_alerts");
    }

    // View all combined — uses the saved compliance view
    viewAllAlerts() {
        this.state.open = false;
        this.action.doAction("payment_compliance_alert.action_all_compliance_alerts");
    }

    formatAmount(val) {
        if (!val && val !== 0) return "0";
        return new Intl.NumberFormat("en-IN").format(Math.round(val));
    }
}

registry.category("systray").add("payment_compliance_alert.ComplianceSystray", {
    Component: ComplianceSystray,
    sequence: 5,
});


// ── Compliance checker service ────────────────────────────────────────────────
registry.category("services").add("compliance_alert_checker", {
    dependencies: ["orm", "dialog", "action"],
    start(env, { orm, dialog, action }) {

        const checkCurrentView = async () => {
            try {
                const formEl = document.querySelector(".o_form_view");
                if (!formEl) return;

                const controller = action.currentController;
                if (!controller) return;

                const resModel = controller.action?.res_model || controller.resModel;
                if (resModel !== "account.move") return;

                const resId = controller.resId || controller.controller?.resId;
                if (!resId || typeof resId !== "number") return;

                if (window._complianceCheckedId === resId) return;
                window._complianceCheckedId = resId;

                const alertType = await orm.call(
                    "account.move", "should_show_compliance_dialog", [resId]
                );
                if (!alertType) return;

                const records = await orm.read(
                    "account.move", [resId],
                    ["name", "partner_id", "compliance_days_overdue", "amount_residual", "currency_id"]
                );
                if (!records || !records.length) return;
                const rec = records[0];

                let udyogAdhaar = "";
                if (alertType === "45" && rec.partner_id) {
                    try {
                        const p = await orm.read("res.partner", [rec.partner_id[0]], ["udyog_adhaar"]);
                        udyogAdhaar = (p[0] && p[0].udyog_adhaar) || "";
                    } catch (e) {}
                }

                dialog.add(ComplianceDialog, {
                    moveId:         resId,
                    billName:       rec.name || "",
                    vendorName:     (rec.partner_id && rec.partner_id[1]) || "",
                    daysOverdue:    rec.compliance_days_overdue || 0,
                    amountResidual: rec.amount_residual
                        ? new Intl.NumberFormat("en-IN").format(Math.round(rec.amount_residual))
                        : "0",
                    currencySymbol: "₹",
                    alertType,
                    udyogAdhaar,
                    onAcknowledged: async () => {
                        window._complianceCheckedId = null;
                    },
                });
            } catch (e) {}
        };

        setTimeout(() => {
            setInterval(checkCurrentView, 2000);
        }, 3000);

        return {};
    },
});
