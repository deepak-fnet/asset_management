/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

class LinuxUpdateWidget extends Component {
    static template = "asset_management.LinuxUpdateWidget";
    static props = {
        ...standardFieldProps,
        record: { type: Object, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");

        this.state = useState({
            loading: true,
            isLocked: false,
            isAdmin: false,
            updateCount: 0,
            pendingCount: 0,
            updates: [],
            activityLog: [],
            isActivityOpen: true,
            assetId: null,
        });

        onWillStart(async () => {
            await this.loadData();
        });
    }

    get assetId() {
        if (this.props.record?.resId) return this.props.record.resId;
        if (this.props.record?.data?.id) return this.props.record.data.id;
        return null;
    }

    async loadData() {
        this.state.loading = true;
        try {
            const assetId = this.assetId;
            if (!assetId) { this.state.loading = false; return; }
            this.state.assetId = assetId;

            const data = await this.orm.call("asset.asset", "get_linux_update_data", [[assetId]]);

            if (data) {
                this.state.isLocked    = data.is_locked    || false;
                this.state.isAdmin     = data.is_admin     || false;
                this.state.updateCount = data.update_count || 0;
                this.state.pendingCount = data.pending_count || 0;
                this.state.updates     = data.updates      || [];
                this.state.activityLog = data.activity_log || [];
            }
        } catch (e) {
            console.error("LU loadData error:", e);
            this.notification.add("Failed to load Linux update data", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    // ── COUNTS ──────────────────────────────────────────────
    getCountByStatus(status) {
        return this.state.updates.filter(u => u.status === status).length;
    }

    // ── ACTION GUARDS ────────────────────────────────────────
    canPush(update) {
        return this.state.isAdmin && ['pending', 'allowed', 'blocked', 'failed'].includes(update.status);
    }
    canBlock(update) {
        return this.state.isAdmin && ['pending', 'allowed'].includes(update.status);
    }
    canAllow(update) {
        return this.state.isAdmin && update.status === 'blocked';
    }
    canUninstall(update) {
        return this.state.isAdmin && ['installed'].includes(update.status);
    }

    // ── ADMIN ACTIONS ────────────────────────────────────────
    async toggleLock() {
        if (!this.state.isAdmin) return;
        try {
            await this.orm.call("asset.asset", "action_lock_all_linux_updates", [[this.state.assetId]]);
            this.notification.add(
                `Updates ${!this.state.isLocked ? "locked" : "unlocked"} successfully`,
                { type: "success" }
            );
            await this.loadData();
        } catch (e) {
            this.notification.add("Failed to toggle lock", { type: "danger" });
        }
    }

    async pushUpdate(ev, updateId) {
        ev.stopPropagation();
        try {
            await this.orm.call("asset.linux.update", "action_push_update", [[updateId]]);
            this.notification.add("Update queued for installation. Agent will install on next sync.", { type: "success" });
            await this.loadData();
        } catch (e) {
            this.notification.add(e.message || "Failed to queue update", { type: "danger" });
        }
    }

    async blockUpdate(ev, updateId) {
        ev.stopPropagation();
        try {
            await this.orm.call("asset.linux.update", "action_block_update", [[updateId]]);
            this.notification.add("Update blocked. Agent will suppress this update.", { type: "warning" });
            await this.loadData();
        } catch (e) {
            this.notification.add(e.message || "Failed to block update", { type: "danger" });
        }
    }

    async allowUpdate(ev, updateId) {
        ev.stopPropagation();
        try {
            await this.orm.call("asset.linux.update", "action_allow_update", [[updateId]]);
            this.notification.add("Update allowed successfully.", { type: "success" });
            await this.loadData();
        } catch (e) {
            this.notification.add(e.message || "Failed to allow update", { type: "danger" });
        }
    }

    async uninstallUpdate(ev, updateId) {
        ev.stopPropagation();
        try {
            await this.orm.call("asset.linux.update", "action_uninstall_update", [[updateId]]);
            this.notification.add("Uninstall queued. Agent will remove this update on next sync.", { type: "warning" });
            await this.loadData();
        } catch (e) {
            this.notification.add(e.message || "Failed to queue uninstall", { type: "danger" });
        }
    }

    toggleActivity() {
        this.state.isActivityOpen = !this.state.isActivityOpen;
    }

    viewUpdateHistory() {
        this.notification.add("Showing all admin actions in the activity log below.", { type: "info" });
    }

    // ── DISPLAY HELPERS ──────────────────────────────────────
    getStatusLabel(status) {
        const map = {
            pending:      'Pending',
            allowed:      'Allowed',
            blocked:      'Blocked',
            installing:   'Installing',
            installed:    'Installed',
            uninstalling: 'Uninstalling',
            uninstalled:  'Uninstalled',
            failed:       'Failed',
        };
        return map[status] || status || 'Unknown';
    }

    getInitials(name) {
        if (!name) return '?';
        return name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);
    }
}

registry.category("fields").add("linux_update_widget", {
    component: LinuxUpdateWidget,
    supportedTypes: ["one2many"],
});

export { LinuxUpdateWidget };
