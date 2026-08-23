/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { formatDateTime } from "@web/core/l10n/dates";
import { deserializeDateTime } from "@web/core/l10n/dates";

/**
 * FileAccessWidget — DB-only, hierarchical folder navigation
 *
 * Architecture: reads ONLY from asset.file.access.record via ORM.
 * No live agent calls. No network calls to laptop IPs.
 *
 * Navigation uses parent_path filtering:
 *   - Click folder row → set currentPath = row.path → reload children
 *   - Back button → set currentPath = parent of currentPath → reload
 *   - Tab switch (Desktop/Documents/Downloads) → reset to root of that folder
 */
class FileAccessWidget extends Component {
    static template = "asset_management.FileAccessWidget";
    static props = {
        record: { type: Object, optional: true },
        "*": true,
    };

    setup() {
        this.orm = useService("orm");

        this.state = useState({
            asset: {},
            violations: [],
            policies: [],
            activeFolder: "Desktop",
            loading: true,
            searchQuery: "",
            // All records for the current view (root or subfolder)
            fileRecords: [],
            // Locked records (shown in the Locked tab)
            lockedRecords: [],
            // Stack of paths for back navigation: [{path, label}]
            pathStack: [],
            // The path currently being shown (null = root of activeFolder)
            currentPath: null,
            errorMessage: "",
            isOnline: false,
            lastScanTime: false,
            // Stats across ALL records (not just current view)
            totalAllRecords: 0,
            totalSizeKb: 0,
        });

        onWillStart(() => this.loadData());
    }

    get assetId() {
        if (this.props.record?.resId) return this.props.record.resId;
        if (this.props.record?.data?.id) return this.props.record.data.id;
        return null;
    }

    // ── Online detection ────────────────────────────────────────────────────
    async _checkIsOnline(assetId) {
        try {
            const result = await this.orm.read("asset.asset", [assetId], ["agent_status"]);
            if (!result.length) return false;
            const status = result[0].agent_status;
            return status === "online" || status === "idle";
        } catch {
            return false;
        }
    }

    // ── Initial full load ───────────────────────────────────────────────────
    async loadData() {
        this.state.loading = true;
        this.state.errorMessage = "";
        const assetId = this.assetId;
        if (!assetId) { this.state.loading = false; return; }

        try {
            // 1. Asset info
            let assetData = {};
            try {
                const res = await this.orm.read("asset.asset", [assetId],
                    ["asset_name", "last_file_access_scan"]);
                assetData = res[0] || {};
            } catch {
                assetData = { asset_name: "Unknown", last_file_access_scan: false };
            }

            // 2. Online check
            const isOnline = await this._checkIsOnline(assetId);


            this.state.asset = assetData;
            this.state.isOnline = isOnline;
            this.state.lastScanTime = assetData.last_file_access_scan || false;

            if (!isOnline) {
                // OFFLINE: clear all data — do not show stale cached records
                this.state.violations = [];
                this.state.policies = [];
                this.state.totalAllRecords = 0;
                this.state.totalSizeKb = 0;
                this.state.fileRecords = [];
                this.state.currentPath = null;
                this.state.pathStack = [];
                return;
            }

            // 3. Global stats — read all records once for stat cards
            let allRecs = [];
            try {
                allRecs = await this.orm.searchRead(
                    "asset.file.access.record",
                    [["asset_id", "=", assetId]],
                    ["size_kb"],
                    { limit: 5000 }
                );
            } catch { allRecs = []; }

            // 4. Violations + policies
            let violations = [], policies = [];
            try {
                violations = await this.orm.searchRead(
                    "asset.file.access.violation",
                    [
                        ["asset_id", "=", assetId],
                        ["action_taken", "in", ["blocked", "blocked_by_policy"]],
                    ],
                    ["id", "filename", "path", "folder", "action_taken", "violation_time"],
                    { order: "violation_time desc", limit: 50 }
                );
            } catch { violations = []; }
            try {
                policies = await this.orm.searchRead(
                    "asset.file.access.policy",
                    [["asset_id", "=", assetId]],
                    ["id", "path", "is_blocked", "reason", "created_date"],
                    { order: "created_date desc" }
                );
            } catch { policies = []; }

            this.state.violations = violations;
            this.state.policies = policies;
            this.state.totalAllRecords = allRecs.length;
            this.state.totalSizeKb = allRecs.reduce((s, r) => s + (r.size_kb || 0), 0);

            // 5. Load root of active folder
            this.state.currentPath = null;
            this.state.pathStack = [];
            await this._loadCurrentFolder();

        } catch (e) {
            console.error("FileAccessWidget loadData error:", e);
            this.state.errorMessage = "Failed to load: " + (e.message || String(e));
        } finally {
            this.state.loading = false;
        }
    }

    /**
     * Load records for the current navigation state using ONLY ORM (no fetch).
     *
     * Root (currentPath=null):
     *   - Query all records in this parent_folder
     *   - JS filters to minimum parent_path depth (direct children only)
     *   - Falls back to showing all if no parent_path data (legacy records)
     *
     * Subfolder (currentPath set):
     *   - Query parent_path = currentPath (exact DB match)
     */
    async _loadCurrentFolder() {
        this.state.loading = true;
        const assetId = this.assetId;
        try {
            // ── Locked tab: query all blocked records across all folders ──────
            if (this.state.activeFolder === "Locked") {
                const locked = await this.orm.searchRead(
                    "asset.file.access.record",
                    [["asset_id", "=", assetId], ["is_blocked", "=", true]],
                    ["id", "name", "path", "parent_path", "record_type",
                        "parent_folder", "size_kb", "last_modified", "scanned_at", "is_blocked"],
                    { order: "record_type desc, name asc", limit: 5000 }
                );
                this.state.lockedRecords = locked;
                this.state.fileRecords = locked;
                return;
            }

            // ── Violations tab: query violation records ──────────────────
            // Only show violations where a locked file was attempted to be opened
            if (this.state.activeFolder === "Violations") {
                const violations = await this.orm.searchRead(
                    "asset.file.access.violation",
                    [
                        ["asset_id", "=", assetId],
                        ["action_taken", "in", ["blocked", "blocked_by_policy"]],
                    ],
                    ["id", "filename", "path", "folder", "action_taken", "violation_time"],
                    { order: "violation_time desc", limit: 500 }
                );
                this.state.violations = violations;
                this.state.fileRecords = [];
                return;
            }

            // Always fetch all records for this folder tab (we filter client-side)
            const domain = [
                ["asset_id", "=", assetId],
                ["parent_folder", "=", this.state.activeFolder],
            ];

            const recs = await this.orm.searchRead(
                "asset.file.access.record",
                domain,
                ["id", "name", "path", "parent_path", "record_type",
                    "parent_folder", "size_kb", "last_modified", "scanned_at", "is_blocked"],
                { order: "record_type desc, name asc", limit: 5000 }
            );

            if (!this.state.currentPath) {
                // ── Root level ─────────────────────────────────────────────
                // Find the anchor record: the folder whose name == activeFolder
                const anchor = recs.find(r =>
                    r.name.toLowerCase() === this.state.activeFolder.toLowerCase() &&
                    r.record_type === "folder"
                );

                if (anchor && anchor.path) {
                    const rootPath = anchor.path;
                    // Detect path separator
                    const sep = rootPath.includes("\\") ? "\\" : "/";
                    const rootLower = rootPath.toLowerCase();

                    // Direct children: path starts with rootPath+sep AND has no more sep after that prefix
                    this.state.fileRecords = recs.filter(r => {
                        if (!r.path || r.id === anchor.id) return false;
                        const pLower = r.path.toLowerCase();
                        const prefix = rootLower + sep;
                        if (!pLower.startsWith(prefix)) return false;
                        // The remaining part after rootPath+sep should not contain sep
                        const remainder = r.path.slice(prefix.length);
                        return !remainder.includes(sep);
                    });
                } else {
                    // No anchor found — show all (flat fallback)
                    this.state.fileRecords = recs;
                }
            } else {
                // ── Subfolder level ────────────────────────────────────────
                // currentPath is the path of the clicked folder
                // Show items directly inside it (one level deep)
                const rootPath = this.state.currentPath;
                const sep = rootPath.includes("\\") ? "\\" : "/";
                const rootLower = rootPath.toLowerCase();

                this.state.fileRecords = recs.filter(r => {
                    if (!r.path) return false;
                    const pLower = r.path.toLowerCase();
                    const prefix = rootLower + sep;
                    if (!pLower.startsWith(prefix)) return false;
                    const remainder = r.path.slice(prefix.length);
                    return !remainder.includes(sep);
                });
            }
        } catch (e) {
            console.warn("_loadCurrentFolder error:", e);
            this.state.fileRecords = [];
        } finally {
            this.state.loading = false;
        }
    }

    // ── Folder tab switch ───────────────────────────────────────────────────
    async setFolder(folder) {
        this.state.activeFolder = folder;
        this.state.searchQuery = "";
        this.state.currentPath = null;
        this.state.pathStack = [];
        await this._loadCurrentFolder();
    }


    // ── Folder click → navigate into subfolder ──────────────────────────────
    async navigateInto(item) {
        if (item.record_type !== "folder" && item.type !== "folder") return;
        // Push current location onto the stack
        this.state.pathStack = [
            ...this.state.pathStack,
            {
                path: this.state.currentPath,
                label: this.state.currentPath
                    ? this.state.currentPath.split(/[/\\]/).pop()
                    : this.state.activeFolder,
            }
        ];
        this.state.currentPath = item.path;
        this.state.searchQuery = "";
        await this._loadCurrentFolder();
    }

    // ── Back button ─────────────────────────────────────────────────────────
    async navigateBack() {
        if (!this.state.pathStack.length) return;
        const stack = [...this.state.pathStack];
        const prev = stack.pop();
        this.state.pathStack = stack;
        this.state.currentPath = prev.path;
        this.state.searchQuery = "";
        await this._loadCurrentFolder();
    }

    // ── Computed helpers ────────────────────────────────────────────────────
    get canGoBack() { return this.state.pathStack.length > 0; }

    get filteredRecords() {
        let recs = this.state.fileRecords || [];
        if (this.state.searchQuery) {
            const q = this.state.searchQuery.toLowerCase();
            recs = recs.filter(r =>
                (r.name || "").toLowerCase().includes(q) ||
                (r.path || "").toLowerCase().includes(q)
            );
        }
        return recs;
    }

    get totalSizeMB() { return (this.state.totalSizeKb / 1024).toFixed(1); }
    get totalLocked() { return this.state.policies.length; }
    get totalViolations() { return this.state.violations.length; }

    get currentLabel() {
        if (this.state.currentPath) {
            return this.state.currentPath.split(/[/\\]/).pop() || this.state.currentPath;
        }
        return this.state.activeFolder;
    }

    get breadcrumbs() {
        // [{label, path}] from stack + current
        return [
            ...this.state.pathStack.map(s => ({
                label: s.label || s.path,
                path: s.path,
                isCurrent: false,
            })),
            { label: this.currentLabel, path: this.state.currentPath, isCurrent: true },
        ];
    }

    formatDateTime(dtStr) {
        if (!dtStr) return "";
        try {
            const dt = deserializeDateTime(dtStr);
            return dt.toFormat("dd/MM/yyyy HH:mm:ss");
        } catch (e) {
            return String(dtStr);
        }
    }

    get formattedLastSync() {
        return this.formatDateTime(this.state.lastScanTime) || "Never";
    }

    // ── Actions ─────────────────────────────────────────────────────────────
    setSearch(ev) { this.state.searchQuery = ev.target.value; }

    // ── Named handlers (called from t-on-click in template) ─────────────────
    // OWL requires named methods for reliable `this` binding.
    onClickDesktop() { return this.setFolder("Desktop"); }
    onClickDocuments() { return this.setFolder("Documents"); }
    onClickDownloads() { return this.setFolder("Downloads"); }
    onClickPictures() { return this.setFolder("Pictures"); }
    onClickTrash() { return this.setFolder("Trash"); }
    onClickLocked() { return this.setFolder("Locked"); }
    onClickViolations() { return this.setFolder("Violations"); }

    onRowClick(rec) {
        if (this.isFolder(rec)) this.navigateInto(rec);
    }

    onToggleLockRecord(rec) { return this.toggleLock(rec, "asset.file.access.record"); }
    onToggleLockPolicy(p) { return this.toggleLock(p, "asset.file.access.policy"); }

    async toggleLock(item, model = "asset.file.access.record") {
        if (!this.state.isOnline) return;
        try {
            const method = model === "asset.file.access.policy" ? "unlink_and_update" : "toggle_block";
            await this.orm.call(model, method, [[item.id]]);
            // Only reload policies and current records (no full reload needed)
            const policies = await this.orm.searchRead(
                "asset.file.access.policy",
                [["asset_id", "=", this.assetId]],
                ["id", "path", "is_blocked", "reason", "created_date"],
                { order: "created_date desc" }
            );
            this.state.policies = policies;
            await this._loadCurrentFolder();
        } catch (e) { console.error("toggleLock error:", e); }
    }

    async actionAddPolicy() {
        try {
            const action = await this.orm.call("asset.asset", "action_block_file", [[this.assetId]]);
            if (action) this.env.services.action.doAction(action);
        } catch (e) { console.error("actionAddPolicy error:", e); }
    }

    formatSize(kb, type) {
        if (type === "folder" || type === "directory") return "--";
        if (!kb || kb === 0) return "0 KB";
        if (kb < 1024) return `${kb.toFixed(1)} KB`;
        return `${(kb / 1024).toFixed(1)} MB`;
    }

    typeIcon(rec) {
        const isFolder = rec.record_type === "folder" || rec.type === "folder";
        return isFolder ? "fa-folder" : "fa-file-o";
    }

    isFolder(rec) {
        return rec.record_type === "folder" || rec.type === "folder";
    }

    scrollToSection(id) {
        const el = document.getElementById(id);
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
}

registry.category("fields").add("file_access_widget", {
    component: FileAccessWidget,
    supportedTypes: ["one2many"],
});
registry.category("view_widgets").add("file_access_widget", {
    component: FileAccessWidget,
});