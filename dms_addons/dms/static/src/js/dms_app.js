import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { sfx } from "./dms_sound";

const LOCKED = "odoo.addons.dms.controllers.main.DmsLocked";
const LEVEL_LABEL = { view: "View", download: "View & Download", edit: "Edit", none: "No access" };
const STATE_LABEL = {
    draft: "Draft", in_review: "In Review", published: "Published",
    rejected: "Rejected", archived: "Archived", discarded: "Discarded",
};

const OFFICE_EXT = new Set(["doc", "docx", "odt", "rtf", "xls", "xlsx", "ods", "ppt", "pptx", "odp", "csv"]);

function fileKind(name, mimetype) {
    const ext = (name || "").split(".").pop().toLowerCase();
    if (mimetype === "application/pdf" || ext === "pdf") return { icon: "fa-file-pdf-o", cls: "pdf", label: "PDF" };
    if (/^image\//.test(mimetype || "")) return { icon: "fa-file-image-o", cls: "img", label: ext.toUpperCase() };
    if (["doc", "docx", "odt", "rtf"].includes(ext)) return { icon: "fa-file-word-o", cls: "doc", label: "DOC" };
    if (["xls", "xlsx", "ods", "csv"].includes(ext)) return { icon: "fa-file-excel-o", cls: "xls", label: "XLS" };
    if (["ppt", "pptx", "odp"].includes(ext)) return { icon: "fa-file-powerpoint-o", cls: "ppt", label: "PPT" };
    if (["zip", "rar", "7z", "gz"].includes(ext)) return { icon: "fa-file-archive-o", cls: "zip", label: "ZIP" };
    if (/^text\//.test(mimetype || "")) return { icon: "fa-file-text-o", cls: "txt", label: ext.toUpperCase() };
    return { icon: "fa-file-o", cls: "other", label: (ext || "FILE").toUpperCase().slice(0, 4) };
}

function humanSize(n) {
    if (!n) return "";
    const u = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}

function timeAgo(s) {
    if (!s) return "";
    const d = new Date(s.replace(" ", "T") + (s.length > 10 ? "Z" : ""));
    const sec = (Date.now() - d.getTime()) / 1000;
    if (sec < 60) return "just now";
    if (sec < 3600) return `${Math.floor(sec / 60)} min ago`;
    if (sec < 86400) return `${Math.floor(sec / 3600)} h ago`;
    if (sec < 86400 * 30) return `${Math.floor(sec / 86400)} d ago`;
    return d.toLocaleDateString();
}

function fmtDate(s) {
    if (!s) return "—";
    return new Date(s.slice(0, 10) + "T00:00:00").toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
}

export class DmsApp extends Component {
    static template = "dms.App";
    static props = { ...standardActionServiceProps };

    setup() {
        const notification = useService("notification");
        this.notification = {
            add: (msg, opts = {}) => {
                sfx.play({ success: "success", danger: "error", warning: "error" }[opts.type] || "tap");
                return notification.add(msg, opts);
            },
        };
        this.actionService = useService("action");
        this.searchRef = useRef("search");
        this.rootRef = useRef("root");
        this.passwordRef = useRef("password");
        this.fileRef = useRef("file");
        this.checkinFileRef = useRef("checkinFile");
        this.helpers = { fileKind, humanSize, timeAgo, fmtDate, LEVEL_LABEL, STATE_LABEL };
        this.state = useState({
            phase: "checking", // checking | login | app
            login: "", password: "", loginError: "", loggingIn: false, shake: false, userName: "",
            boot: null, view: "recent", folderId: null, docs: [], loading: false,
            search: "", layout: localStorageGet("dms.layout", "list"),
            expanded: {}, selectedId: null, detail: null, detailTab: "revisions",
            dragging: false, modal: null, busy: false, preview: null,
            upload: {}, checkin: {}, decision: {}, cutoff: {}, share: {},
            folderForm: {}, groups: [], groupForm: {}, requests: [], menuOpen: false,
            navKey: 0, navDir: "fwd", unlocking: false, flash: null, openingTile: null,
            muted: sfx.muted, moveForm: {}, retireForm: {}, closing: false, fileAccess: null,
        });
        loadFonts();
        this._searchTimer = null;
        this._keydown = (ev) => this.onKeydown(ev);
        this._click = () => (this.state.menuOpen = false);
        this._guardKey = (ev) => this.onGuardKey(ev);
        this._blur = () => setTimeout(() => {
            // clicking inside the spreadsheet frame moves focus there; that is not leaving the page
            if (document.activeElement?.tagName === "IFRAME" && document.hasFocus()) return;
            if (document.activeElement?.tagName === "IFRAME" && this.state.preview?.sheet) return;
            this.shieldOn("Window lost focus");
        }, 100);
        this._focus = () => {};
        this._vis = () => document.hidden && this.shieldOn("Tab hidden");
        this._guardEvent = (ev) => {
            if (this.guarded) {
                ev.preventDefault();
                if (ev.type === "copy" && ev.clipboardData) ev.clipboardData.setData("text/plain", "");
            }
        };
        this._mouse = (ev) => this.blockMouse(ev);

        onWillStart(async () => {
            const s = await rpc("/dms/session");
            this.state.login = s.login;
            this.state.userName = s.name;
            if (s.unlocked) {
                await this.enter();
            } else {
                this.state.phase = "login";
            }
        });
        onMounted(() => {
            window.addEventListener("keydown", this._keydown);
            window.addEventListener("click", this._click);
            window.addEventListener("keydown", this._guardKey, true);
            window.addEventListener("keyup", this._guardKey, true);
            window.addEventListener("keypress", this._guardKey, true);
            for (const t of ["copy", "paste", "cut", "selectstart", "dragstart", "contextmenu", "auxclick"]) {
                document.addEventListener(t, this._guardEvent, true);
            }
            document.addEventListener("mousedown", this._mouse, true);
            document.addEventListener("mouseup", this._mouse, true);
            // devtools opened (window chrome grows) → shield
            this._devtools = setInterval(() => {
                const open = window.outerWidth - window.innerWidth > 160 || window.outerHeight - window.innerHeight > 160;
                if (open && !this._dtOpen) this.shieldOn("Developer tools");
                this._dtOpen = open;
            }, 1000);
            // a copied screenshot image is wiped from the clipboard
            this._clipTimer = setInterval(() => {
                if (!this.guarded || !document.hasFocus() || !navigator.clipboard?.read) return;
                navigator.clipboard.read().then((items) => {
                    if (items.some((it) => it.types.some((t) => t.startsWith("image")))) this.nukeClipboard();
                }).catch(() => {});
            }, 300);
            if (navigator.mediaDevices?.getDisplayMedia && !navigator.mediaDevices._dmsWrapped) {
                const og = navigator.mediaDevices.getDisplayMedia.bind(navigator.mediaDevices);
                navigator.mediaDevices.getDisplayMedia = (...a) => {
                    this.shieldOn("Screen sharing");
                    return og(...a);
                };
                navigator.mediaDevices._dmsWrapped = true;
            }
            window.addEventListener("blur", this._blur);
            window.addEventListener("focus", this._focus);
            document.addEventListener("visibilitychange", this._vis);
            if (this.state.phase === "login") this.passwordRef.el?.focus();
        });
        onWillUnmount(() => {
            window.removeEventListener("keydown", this._keydown);
            window.removeEventListener("click", this._click);
            window.removeEventListener("keydown", this._guardKey, true);
            window.removeEventListener("keyup", this._guardKey, true);
            window.removeEventListener("keypress", this._guardKey, true);
            for (const t of ["copy", "paste", "cut", "selectstart", "dragstart", "contextmenu", "auxclick"]) {
                document.removeEventListener(t, this._guardEvent, true);
            }
            document.removeEventListener("mousedown", this._mouse, true);
            document.removeEventListener("mouseup", this._mouse, true);
            clearInterval(this._devtools);
            clearInterval(this._clipTimer);
            window.removeEventListener("blur", this._blur);
            window.removeEventListener("focus", this._focus);
            document.removeEventListener("visibilitychange", this._vis);
        });
    }

    // ------------------------------------------------------------------ rpc
    async call(url, params = {}) {
        try {
            return await rpc(url, params, { silent: true });
        } catch (e) {
            const name = e?.data?.name;
            if (name === LOCKED) {
                this.lockScreen(e.data.message);
            } else {
                this.notification.add(e?.data?.message || e?.message || "Something went wrong", { type: "danger" });
            }
            throw e;
        }
    }

    lockScreen(msg) {
        this.state.phase = "login";
        this.state.password = "";
        this.state.loginError = msg || "";
        this.state.modal = null;
        this.state.preview = null;
        setTimeout(() => this.passwordRef.el?.focus(), 50);
    }

    // ------------------------------------------------------------------ login
    async doLogin(ev) {
        ev?.preventDefault();
        if (!this.state.password) return;
        this.state.loggingIn = true;
        this.state.loginError = "";
        const res = await rpc("/dms/login", { login: this.state.login, password: this.state.password });
        this.state.loggingIn = false;
        if (!res.ok) {
            this.state.loginError = res.error;
            this.state.shake = true;
            setTimeout(() => (this.state.shake = false), 500);
            return;
        }
        this.state.password = "";
        sfx.play("unlock");
        this.state.unlocking = true;
        await new Promise((r) => setTimeout(r, 650));
        await this.enter();
        this.state.unlocking = false;
    }

    async lock() {
        sfx.play("lock");
        await rpc("/dms/logout");
        this.lockScreen("");
    }

    async enter() {
        const boot = await this.call("/dms/bootstrap");
        this.state.boot = boot;
        for (const f of boot.folders) {
            if (!f.parent_id) this.state.expanded[f.id] = true;
        }
        this.state.phase = "app";
        if (this.state.view === "folder" && this.state.folderId) {
            await this.loadDocs();
        } else {
            await this.openView(boot.counts.approvals ? "approvals" : "recent");
        }
    }

    // ------------------------------------------------------------------ navigation
    get rootFolders() {
        return (this.state.boot?.folders || []).filter((f) => !f.parent_id);
    }
    children(id) {
        return (this.state.boot?.folders || []).filter((f) => f.parent_id === id);
    }
    get currentFolder() {
        return (this.state.boot?.folders || []).find((f) => f.id === this.state.folderId);
    }
    get breadcrumb() {
        const out = [];
        let f = this.currentFolder;
        const all = this.state.boot?.folders || [];
        while (f) {
            out.unshift(f);
            f = all.find((x) => x.id === f.parent_id);
        }
        return out;
    }
    get tiles() {
        if (this.state.view === "folder") return this.children(this.state.folderId);
        if (this.state.view === "recent" && !this.state.search) return this.rootFolders;
        return [];
    }
    get canCreateFolderHere() {
        if (this.state.view === "folder") return this.canUploadHere;
        return this.state.view === "recent" && this.state.boot?.user.is_controller;
    }
    get folderTree() {
        // folders in tree order with their depth, for checklists
        const out = [];
        const walk = (list, depth) => {
            for (const f of list) {
                out.push({ f, depth });
                walk(this.children(f.id), depth + 1);
            }
        };
        walk(this.rootFolders.filter((f) => !f.is_archive), 0);
        return out;
    }
    folderTotal(f) {
        return f.doc_count + this.children(f.id).reduce((n, c) => n + this.folderTotal(c), 0);
    }
    get viewTitle() {
        return {
            groups: "Groups", requests: "Folder deletion requests", recent: "Recent", starred: "Starred", approvals: "Waiting for my approval",
            checked_out: "Checked out by me", my_uploads: "My uploads", search: "Search results",
        }[this.state.view];
    }
    get canUploadHere() {
        const f = this.currentFolder;
        return this.state.view === "folder" && f && f.level === "edit" && !f.is_archive;
    }
    get editableFolders() {
        return (this.state.boot?.folders || []).filter((f) => f.level === "edit" && !f.is_archive);
    }
    folderLabel(f) {
        const all = this.state.boot.folders;
        const parts = [f.name];
        let p = all.find((x) => x.id === f.parent_id);
        while (p) { parts.unshift(p.name); p = all.find((x) => x.id === p.parent_id); }
        return parts.join(" / ");
    }

    bump(dir = "fwd") {
        this.state.navDir = dir;
        this.state.navKey++;
    }
    toggleMute() {
        this.state.muted = sfx.toggle();
    }
    showFlash(text, kind = "ok", sub = "") {
        this.state.flash = { text, kind, sub, key: Date.now() };
        sfx.play(kind === "bad" || kind === "trash" ? "trash" : "stamp");
        clearTimeout(this._flashTimer);
        this._flashTimer = setTimeout(() => (this.state.flash = null), 1100);
    }
    async openTile(f, ev) {
        if (this.state.cine) return;
        const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
        const tile = ev?.currentTarget;
        const root = this.rootRef.el;
        if (reduce || !tile || !root) {
            sfx.play("open");
            return this.openFolder(f);
        }
        // 1. click: ripple where the pointer landed
        sfx.play("click");
        const t = tile.getBoundingClientRect();
        const r = root.getBoundingClientRect();
        tile.style.setProperty("--rx", `${(ev.clientX || t.left + t.width / 2) - t.left}px`);
        tile.style.setProperty("--ry", `${(ev.clientY || t.top + t.height / 2) - t.top}px`);
        tile.classList.remove("rippling");
        void tile.offsetWidth;
        tile.classList.add("rippling");

        // the big folder starts exactly on top of the tile, then flies to the centre
        const W = 380;
        const cx = r.width / 2;
        const cy = r.height / 2;
        const tx = t.left - r.left + t.width / 2 - cx;
        const ty = t.top - r.top + t.height / 2 - cy;
        const total = this.folderTotal(f);
        this.state.cine = {
            name: f.name, archive: f.is_archive, phase: "start",
            sub: !f.level ? "Only files shared with you" : f.is_archive ? "Read only"
                : `You can ${{ edit: "edit", download: "view and download", view: "view" }[f.level]}`,
            from: `translate(${tx}px, ${ty}px) scale(${t.width / W})`,
            papers: Math.max(1, Math.min(3, total || 1)),
        };
        const wait = (ms) => new Promise((res) => (this._cineTimer = setTimeout(res, ms)));
        const loading = this.openFolderQuiet(f);
        await wait(40);
        if (!this.state.cine) return;
        // 2. folder lifts & opens
        this.state.cine.phase = "lift";
        sfx.play("folder");
        await wait(480);
        if (!this.state.cine) return;
        this.state.cine.phase = "open";
        await wait(260);
        if (!this.state.cine) return;
        // 3. documents slide out
        this.state.cine.phase = "papers";
        sfx.play("paper");
        await Promise.all([wait(620), loading]);
        if (!this.state.cine) return;
        // 4. smooth transition
        this.state.cine.phase = "sweep";
        sfx.play("whoosh");
        await wait(520);
        // 5. land
        this.state.cine = null;
        sfx.play("land");
    }
    skipCine() {
        clearTimeout(this._cineTimer);
        this.state.cine = null;
    }
    async openFolderQuiet(f) {
        // load the folder behind the curtain, without the slide animation fighting the cinematic
        this.state.menuOpen = false;
        this.state.view = "folder";
        this.state.folderId = f.id;
        this.state.search = "";
        this.state.expanded[f.id] = true;
        this.closeDetail();
        this.bump("land");
        await this.loadDocs();
    }
    async openView(view) {
        sfx.play("tap");
        this.bump("side");
        this.state.view = view;
        this.state.folderId = null;
        this.state.search = "";
        this.closeDetail();
        if (view === "groups") {
            await this.loadGroups();
        } else if (view === "requests") {
            await this.loadRequests();
        } else {
            await this.loadDocs();
        }
    }

    // ------------------------------------------------------------------ folders
    openNewFolder() {
        const parent = this.state.view === "folder" ? this.currentFolder : null;
        this.state.folderForm = { parent, name: "" };
        this.state.modal = "folder";
    }
    async doCreateFolder() {
        const f = this.state.folderForm;
        if (!f.name.trim()) return this.notification.add("Give the folder a name", { type: "warning" });
        try {
            const res = await this.call("/dms/folder/create", { name: f.name, parent_id: f.parent?.id || null });
            this.state.modal = null;
            this.state.boot = await this.call("/dms/bootstrap");
            if (f.parent) this.state.expanded[f.parent.id] = true;
            this.notification.add(`Folder “${f.name.trim()}” created`, { type: "success" });
            await this.openFolder(this.state.boot.folders.find((x) => x.id === res.id));
        } catch {
            // notified
        }
    }
    openRename() {
        this.state.menuOpen = false;
        this.state.folderForm = { mode: "rename", folder: this.currentFolder, name: this.currentFolder.name };
        this.state.modal = "rename";
    }
    async doRename() {
        const f = this.state.folderForm;
        if (!f.name.trim() || f.name === f.folder.name) {
            this.state.modal = null;
            return;
        }
        await this.call("/dms/folder/rename", { folder_id: f.folder.id, name: f.name.trim() });
        f.folder.name = f.name.trim();
        this.state.modal = null;
        this.notification.add("Folder renamed", { type: "success" });
    }
    toggleMenu(ev) {
        ev?.stopPropagation();
        this.state.menuOpen = !this.state.menuOpen;
    }
    openDelete() {
        this.state.menuOpen = false;
        const f = this.currentFolder;
        this.state.folderForm = { mode: "delete", folder: f, reason: "", count: this.folderTotal(f), subs: this.children(f.id).length };
        this.state.modal = "delete";
    }
    async doDelete() {
        const f = this.state.folderForm;
        try {
            const res = await this.call("/dms/folder/delete_request", { folder_id: f.folder.id, reason: f.reason });
            this.state.modal = null;
            if (res.deleted) {
                this.showFlash("Deleted", "trash", f.folder.name);
                const parent = this.state.boot.folders.find((x) => x.id === f.folder.parent_id);
                this.state.boot = await this.call("/dms/bootstrap");
                parent ? await this.openFolder(this.state.boot.folders.find((x) => x.id === parent.id)) : await this.openView("recent");
            } else {
                this.notification.add("Deletion requested. The Document Controller will decide.", { type: "success" });
                f.folder.delete_requested = true;
            }
        } catch {
            // notified
        }
    }
    async moveAll(r) {
        if (!r.target) return this.notification.add("Pick a folder to move the documents into", { type: "warning" });
        try {
            await this.call("/dms/document/move", { doc_ids: r.items.map((d) => d.id), folder_id: Number(r.target) });
            sfx.play("upload");
            this.notification.add(`${r.items.length} document(s) moved`, { type: "success" });
            this.state.boot = await this.call("/dms/bootstrap");
            await this.loadRequests();
        } catch {
            // notified
        }
    }
    async retireAll(r) {
        if (!window.confirm(`Send ${r.items.length} document(s) to the Archive? They can be disposed of after their retention period.`)) return;
        try {
            await this.call("/dms/document/retire", { doc_ids: r.items.map((d) => d.id), reason: `Folder ${r.name} closed` });
            this.showFlash("Archived", "old", `${r.items.length} document(s)`);
            this.state.boot = await this.call("/dms/bootstrap");
            await this.loadRequests();
        } catch {
            // notified
        }
    }
    moveTargets(excludeId = null) {
        const banned = new Set();
        if (excludeId) {
            const walk = (id) => {
                banned.add(id);
                this.children(id).forEach((c) => walk(c.id));
            };
            walk(excludeId);
        }
        return this.folderTree.filter((row) => row.f.level === "edit" && !banned.has(row.f.id));
    }
    openMove() {
        const d = this.state.detail;
        this.state.moveForm = { target: null, title: `${d.code} ${d.name}`, current: d.folder_id };
        this.state.modal = "move";
    }
    async doMove() {
        const m = this.state.moveForm;
        if (!m.target) return this.notification.add("Pick the folder to move it into", { type: "warning" });
        try {
            await this.call("/dms/document/move", { doc_ids: [this.state.detail.id], folder_id: m.target });
            this.state.modal = null;
            sfx.play("upload");
            const target = this.state.boot.folders.find((f) => f.id === m.target);
            this.notification.add(`Moved to ${this.folderLabel(target)}`, { type: "success" });
            this.state.boot = await this.call("/dms/bootstrap");
            await this.reloadAll();
        } catch {
            // notified
        }
    }
    openRetire() {
        this.state.retireForm = { reason: "" };
        this.state.modal = "retire";
    }
    async doRetire() {
        try {
            await this.call("/dms/document/retire", { doc_ids: [this.state.detail.id], reason: this.state.retireForm.reason });
            this.state.modal = null;
            this.showFlash("Archived", "old", "Retired to the Archive");
            this.state.boot = await this.call("/dms/bootstrap");
            await this.reloadAll();
        } catch {
            // notified
        }
    }
    async loadRequests() {
        this.state.loading = true;
        try {
            this.state.requests = await this.call("/dms/folder/delete_requests");
        } finally {
            this.state.loading = false;
        }
    }
    async decideDelete(r, approve) {
        const note = approve ? null : window.prompt(`Why keep “${r.name}”?`) || "";
        try {
            await this.call("/dms/folder/delete_decide", { folder_id: r.id, approve, note });
            if (approve) {
                this.showFlash("Deleted", "trash", r.name);
            } else {
                this.notification.add(`“${r.name}” kept`, { type: "success" });
            }
            this.state.boot = await this.call("/dms/bootstrap");
            await this.loadRequests();
        } catch {
            // notified
        }
    }

    // ------------------------------------------------------------------ groups
    async loadGroups() {
        this.state.loading = true;
        try {
            this.state.groups = await this.call("/dms/groups");
        } finally {
            this.state.loading = false;
        }
    }
    openGroupForm(g = null) {
        this.state.groupForm = {
            id: g?.id || null, name: g?.name || "", level: g?.level || "view", description: g?.description || "",
            folderIds: new Set((g?.folders || []).map((f) => f.id)), users: [...(g?.users || [])],
            term: "", results: [],
        };
        this.state.modal = "group";
    }
    toggleGroupFolder(id) {
        const ids = this.state.groupForm.folderIds;
        ids.has(id) ? ids.delete(id) : ids.add(id);
    }
    async searchMembers(ev) {
        const g = this.state.groupForm;
        g.term = ev.target.value;
        const res = g.term ? await this.call("/dms/principals", { term: g.term }) : [];
        g.results = res.filter((p) => !g.users.some((u) => u.id === p.id));
    }
    addMember(p) {
        const g = this.state.groupForm;
        g.users.push({ id: p.id, name: p.name, login: p.sub });
        g.term = "";
        g.results = [];
    }
    removeMember(u) {
        const g = this.state.groupForm;
        g.users = g.users.filter((x) => x.id !== u.id);
    }
    async saveGroup() {
        const g = this.state.groupForm;
        try {
            await this.call("/dms/group/save", {
                group_id: g.id, name: g.name, level: g.level, description: g.description,
                folder_ids: [...g.folderIds], user_ids: g.users.map((u) => u.id),
            });
            this.state.modal = null;
            this.notification.add(`Group “${g.name}” saved`, { type: "success" });
            await this.loadGroups();
        } catch {
            // notified
        }
    }
    async deleteGroup() {
        const g = this.state.groupForm;
        if (!window.confirm(`Delete group “${g.name}”? Its members lose the access it gave them.`)) return;
        await this.call("/dms/group/delete", { group_id: g.id });
        this.state.modal = null;
        await this.loadGroups();
    }
    async openFolder(f) {
        this.state.menuOpen = false;
        const cur = this.currentFolder;
        const deeper = !cur || f.parent_id === cur.id || this.state.view !== "folder";
        this.bump(deeper ? "fwd" : "back");
        if (!deeper) sfx.play("back");
        this.state.view = "folder";
        this.state.folderId = f.id;
        this.state.search = "";
        this.state.expanded[f.id] = true;
        this.closeDetail();
        await this.loadDocs();
    }
    toggleExpand(f, ev) {
        ev.stopPropagation();
        this.state.expanded[f.id] = !this.state.expanded[f.id];
    }

    async loadDocs() {
        this.state.loading = true;
        try {
            const view = this.state.search && this.state.view !== "folder" ? "search" : this.state.view;
            this.state.docs = await this.call("/dms/documents", {
                folder_id: this.state.folderId, view, search: this.state.search || null,
            });
        } finally {
            this.state.loading = false;
        }
    }
    onSearchInput(ev) {
        this.state.search = ev.target.value;
        clearTimeout(this._searchTimer);
        this._searchTimer = setTimeout(() => {
            if (this.state.search && !["folder", "search"].includes(this.state.view)) this.state.view = "search";
            if (!this.state.search && this.state.view === "search") this.state.view = "recent";
            this.loadDocs();
        }, 250);
    }
    setLayout(l) {
        this.state.layout = l;
        localStorageSet("dms.layout", l);
    }

    async refreshCounts(counts) {
        if (counts && this.state.boot) this.state.boot.counts = counts;
    }

    // ------------------------------------------------------------------ detail
    async select(doc) {
        if (this.state.selectedId !== doc.id) sfx.play("unroll");
        this.state.closing = false;
        this.state.selectedId = doc.id;
        this.state.detailTab = doc.pending_state === "in_review" && this.state.view === "approvals" ? "approvals" : "revisions";
        await this.loadDetail();
    }
    async loadDetail() {
        if (!this.state.selectedId) return;
        this.state.detail = await this.call("/dms/document", { doc_id: this.state.selectedId });
    }
    closeDetail() {
        this.state.selectedId = null;
        this.state.detail = null;
        this.state.closing = false;
    }
    rollUpDetail() {
        // the paper rolls back up before the panel goes away
        if (!this.state.detail || this.state.closing) return;
        this.state.closing = true;
        sfx.play("rollup");
        setTimeout(() => this.state.closing && this.closeDetail(), 560);
    }
    async openTab(tab) {
        this.state.detailTab = tab;
        if (tab === "access") await this.loadFileAccess();
    }
    async loadFileAccess() {
        this.state.fileAccess = await this.call("/dms/file_access", { doc_id: this.state.detail.id });
    }
    async setFileAccess(row, level) {
        await this.call("/dms/file_access/set", { doc_id: this.state.detail.id, user_id: row.user_id, level: level || false });
        sfx.play(level === "none" ? "trash" : "tap");
        await Promise.all([this.loadFileAccess(), this.loadDetail()]);
    }
    async reloadAll() {
        await Promise.all([this.loadDocs(), this.loadDetail()]);
    }

    async act(action, params = {}, okMsg = null) {
        this.state.busy = true;
        try {
            const res = await this.call("/dms/action", { action, doc_id: this.state.detail?.id, ...params });
            this.refreshCounts(res.counts);
            if (okMsg) this.notification.add(okMsg, { type: "success" });
            await this.reloadAll();
            return true;
        } catch {
            return false;
        } finally {
            this.state.busy = false;
        }
    }
    async toggleStar(doc, ev) {
        ev?.stopPropagation();
        await this.call("/dms/action", { action: "star", doc_id: doc.id });
        doc.starred = !doc.starred;
        if (this.state.detail?.id === doc.id) this.state.detail.starred = doc.starred;
        if (this.state.view === "starred") this.loadDocs();
    }
    checkout() {
        return this.act("checkout", {}, "Checked out — download, edit, then check in.");
    }
    unlock() {
        return this.act("unlock", {}, "Unlocked");
    }
    submitRev(rev) {
        return this.act("submit", { rev_id: rev.id }, "Sent for approval L1 → L2" + (this.state.detail.approvers.l3 ? " → L3" : ""));
    }

    // ------------------------------------------------------------------ files
    fileUrl(rev, download = false) {
        return `/dms/file/${rev.id}${download ? "?download=1" : ""}`;
    }
    download(rev) {
        const a = document.createElement("a");
        a.href = this.fileUrl(rev, true);
        a.click();
    }
    async openPreview(rev) {
        // documents open in the award_management protected viewer, in its own tab
        // (its screenshot protections need to run as the top-level page)
        const tab = window.open("about:blank", "_blank");
        try {
            const res = await this.call("/dms/protected/token", { rev_id: rev.id });
            sfx.play("paper");
            if (tab) {
                tab.location.href = res.url;
            } else {
                window.location.href = res.url;
            }
        } catch {
            tab?.close();
        }
    }
    async openInlinePreview(rev) {
        const kind = fileKind(rev.file_name, rev.mimetype);
        const p = { rev, kind, doc: this.state.detail, loading: true, zoom: 1, page: 1, view: null };
        this.state.preview = p;
        sfx.play("paper");
        let info;
        try {
            info = await this.call("/dms/view/info", { rev_id: rev.id });
        } catch {
            this.state.preview = null;
            return;
        }
        const preview = this.state.preview;
        if (!preview || preview.rev.id !== rev.id) return;
        preview.view = info.kind;
        preview.canDownload = info.can_download;
        const raw = `/dms/file/${rev.id}`;
        if (info.kind === "pdf" || info.kind === "doc") {
            // everyone sees server-rendered pages: the viewer itself never hands out the file
            preview.pages = Array.from({ length: info.pages }, (_, k) => k + 1);
        } else if (info.kind === "sheet") {
            preview.sheet = `/dms/view/${rev.id}/sheet`;
        } else if (info.kind === "image") {
            preview.img = raw;
        } else if (info.kind === "image_convert") {
            preview.img = `/dms/view/${rev.id}/image`;
        } else if (info.kind === "video" || info.kind === "audio") {
            preview.media = raw;
        } else if (info.kind === "text") {
            preview.text = info.text;
        } else if (info.kind === "zip") {
            preview.entries = info.entries;
        }
        preview.loading = false;
        preview.watermark = this.watermarkUrl();
    }
    closePreview() {
        this.state.preview = null;
        this.state.shield = false;
    }
    get guarded() {
        // the viewer is protected for everyone; downloading is a separate, logged action
        const p = this.state.preview;
        return Boolean(p && !p.loading);
    }
    watermarkUrl() {
        const u = this.state.boot.user;
        const stamp = new Date().toLocaleString();
        const text = `${u.name} · ${stamp} · protected`.replace(/[<&>]/g, "");
        const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='420' height='220'><text x='10' y='120' transform='rotate(-24 210 110)' fill='rgba(28,43,74,0.13)' font-family='Arial' font-size='15' font-weight='700'>${text}</text></svg>`;
        return `url("data:image/svg+xml;utf8,${encodeURIComponent(svg)}")`;
    }
    zoomPages(d) {
        const p = this.state.preview;
        p.zoom = Math.max(0.5, Math.min(2.5, Math.round((p.zoom + d) * 10) / 10));
    }
    onPagesScroll(ev) {
        const el = ev.currentTarget;
        const imgs = el.querySelectorAll(".o_dms_pg");
        const mid = el.getBoundingClientRect().top + el.clientHeight / 3;
        for (const img of imgs) {
            const r = img.getBoundingClientRect();
            if (r.top <= mid && r.bottom > mid) {
                this.state.preview.page = Number(img.dataset.page);
                break;
            }
        }
    }
    onFrameLoad(ev) {
        // spreadsheets: same protections inside the frame
        const p = this.state.preview;
        if (!p || p.canDownload) return;
        try {
            const d = ev.target.contentDocument;
            const block = (e) => e.preventDefault();
            d.addEventListener("contextmenu", block);
            d.addEventListener("copy", block);
            d.addEventListener("selectstart", block);
            d.addEventListener("dragstart", block);
            d.addEventListener("keydown", (e) => this.onGuardKey(e), true);
            d.addEventListener("keyup", (e) => this.onGuardKey(e), true);
            d.addEventListener("keypress", (e) => this.onGuardKey(e), true);
            d.addEventListener("mousedown", (e) => this.blockMouse(e), true);
            d.addEventListener("wheel", (e) => {
                if (e.ctrlKey) e.preventDefault();
            }, { passive: false, capture: true });
        } catch {
            // not same-origin
        }
    }
    onPdfLoad(ev) {
        this.onFrameLoad(ev);
    }
    noContext(ev) {
        if (this.guarded) ev.preventDefault();
    }
    // --- capture protection for read-only views (same rules as the award viewer) ---
    // Any key except Ctrl and the arrows, losing focus, hiding the tab, opening devtools or
    // starting a screen share puts up a shield that stays until the user clicks "Show again".
    // A screenshot tool needs a key press or the focus, so it only ever captures the shield.
    nukeClipboard() {
        try {
            navigator.clipboard?.writeText("")?.catch(() => {});
        } catch {
            // not allowed without focus
        }
    }
    shieldOn(reason) {
        if (!this.guarded) return;
        const first = !this.state.shield;
        this.state.shield = true;
        this.nukeClipboard();
        for (const ms of [100, 300, 600, 1000, 2000]) setTimeout(() => this.nukeClipboard(), ms);
        if (first && reason) {
            rpc("/dms/view/capture", { rev_id: this.state.preview.rev.id, how: reason }, { silent: true }).catch(() => {});
        }
    }
    showAgain() {
        // reload the pages so nothing captured from memory survives
        this.state.shield = false;
        const p = this.state.preview;
        if (p?.pages) {
            const pages = p.pages;
            p.pages = [];
            setTimeout(() => (p.pages = pages), 30);
        }
        if (p?.sheet) {
            const sheet = p.sheet;
            p.sheet = null;
            setTimeout(() => (p.sheet = sheet), 30);
        }
    }
    isAllowedKey(ev) {
        return ["Control", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(ev.key);
    }
    onGuardKey(ev) {
        if (!this.guarded) return false;
        const stop = () => {
            ev.preventDefault();
            ev.stopImmediatePropagation();
        };
        const isPrint = ev.key === "PrintScreen" || ev.key === "Print Screen" || ev.code === "PrintScreen" || ev.keyCode === 44;
        let isFn = ev.key === "Fn" || ev.code === "Fn" || ev.keyCode === 255;
        try {
            isFn = isFn || ev.getModifierState?.("Fn");
        } catch {
            // ignore
        }
        if (ev.type === "keyup") {
            if (!this.isAllowedKey(ev)) {
                stop();
                this.nukeClipboard();
                if (isPrint) for (const ms of [50, 150, 300, 600, 1000, 2000]) setTimeout(() => this.nukeClipboard(), ms);
            }
            return true;
        }
        if (ev.type === "keypress") {
            if (!this.isAllowedKey(ev)) stop();
            return true;
        }
        if (isPrint || isFn) {
            stop();
            this.shieldOn(isPrint ? "Print Screen key" : "Fn key");
            return true;
        }
        if (ev.ctrlKey && (ev.key === "ArrowUp" || ev.key === "ArrowDown")) {
            stop();
            this.zoomPages(ev.key === "ArrowUp" ? 0.2 : -0.2);
            return true;
        }
        if (this.isAllowedKey(ev)) return false;
        stop();
        this.shieldOn(`Key ${ev.key || ev.code}`);
        return true;
    }
    onPagesWheel(ev) {
        // Ctrl + wheel zooms around the cursor
        if (!(ev.ctrlKey || ev.metaKey)) return;
        ev.preventDefault();
        const el = ev.currentTarget;
        const p = this.state.preview;
        const old = p.zoom;
        this.zoomPages(ev.deltaY < 0 ? 0.2 : -0.2);
        const ratio = p.zoom / old;
        if (ratio === 1) return;
        const r = el.getBoundingClientRect();
        const ax = ev.clientX - r.left;
        const ay = ev.clientY - r.top;
        const sl = el.scrollLeft;
        const st = el.scrollTop;
        requestAnimationFrame(() => {
            el.scrollLeft = sl * ratio + ax * (ratio - 1);
            el.scrollTop = st * ratio + ay * (ratio - 1);
        });
    }
    blockMouse(ev) {
        if (this.guarded && (ev.button === 1 || ev.button === 2)) {
            ev.preventDefault();
            ev.stopImmediatePropagation();
        }
    }

    // ------------------------------------------------------------------ upload
    openUpload(files = null) {
        const folder = this.canUploadHere ? this.currentFolder : this.editableFolders[0];
        if (!folder) {
            this.notification.add("You have no folder with Edit access. Ask the Document Controller.", { type: "warning" });
            return;
        }
        const file = files?.[0] || null;
        this.state.upload = {
            folderId: folder.id, typeId: (this.state.boot.types.find((t) => !t.l3) || this.state.boot.types[0])?.id, file,
            title: file ? file.name.replace(/\.[^.]+$/, "") : "", description: "", submit: true,
        };
        this.state.modal = "upload";
    }
    onPickFile(ev) {
        const f = ev.target.files[0];
        if (!f) return;
        this.state.upload.file = f;
        if (!this.state.upload.title) this.state.upload.title = f.name.replace(/\.[^.]+$/, "");
    }
    get uploadType() {
        return this.state.boot.types.find((t) => t.id === Number(this.state.upload.typeId));
    }
    get uploadPreviewCode() {
        const f = this.state.boot.folders.find((x) => x.id === Number(this.state.upload.folderId));
        let root = f;
        while (root && !root.code && root.parent_id) root = this.state.boot.folders.find((x) => x.id === root.parent_id);
        return `${root?.code || "DEPT"}-${this.uploadType?.code || "TYPE"}-NNN`;
    }
    async postForm(url, data) {
        const fd = new FormData();
        for (const [k, v] of Object.entries(data)) {
            if (v !== null && v !== undefined) fd.append(k, v);
        }
        fd.append("csrf_token", odoo.csrf_token);
        this.state.busy = true;
        try {
            const r = await fetch(url, { method: "POST", body: fd });
            const res = await r.json().catch(() => ({ ok: false, error: `Upload failed (${r.status})` }));
            if (!res.ok) {
                if ((res.error || "").includes("DMS session has expired")) this.lockScreen(res.error);
                else this.notification.add(res.error, { type: "danger" });
            }
            return res;
        } finally {
            this.state.busy = false;
        }
    }
    async doUpload() {
        const u = this.state.upload;
        if (!u.file) return this.notification.add("Choose a file", { type: "warning" });
        const res = await this.postForm("/dms/upload", {
            folder_id: u.folderId, type_id: u.typeId, title: u.title, description: u.description,
            submit: u.submit ? "1" : "0", file: u.file,
        });
        if (!res.ok) return;
        this.state.modal = null;
        sfx.play("upload");
        this.showFlash(u.submit ? "Sent" : "Saved", "wait", u.submit ? "Waiting for L1 approval" : "Draft, not sent yet");
        const folder = this.state.boot.folders.find((f) => f.id === Number(u.folderId));
        await this.openFolder(folder);
        await this.enterCounts();
        this.select({ id: res.doc_id });
    }
    async enterCounts() {
        const boot = await this.call("/dms/bootstrap");
        this.state.boot.counts = boot.counts;
    }

    // drag & drop straight onto the repository
    onDragOver(ev) {
        if (!ev.dataTransfer?.types?.includes("Files")) return;
        ev.preventDefault();
        this.state.dragging = true;
    }
    onDragLeave(ev) {
        if (ev.currentTarget.contains(ev.relatedTarget)) return;
        this.state.dragging = false;
    }
    onDrop(ev) {
        ev.preventDefault();
        this.state.dragging = false;
        const files = ev.dataTransfer?.files;
        if (files?.length) {
            if (this.state.detail?.can_checkin && this.state.selectedId) {
                this.openCheckin(files);
            } else {
                this.openUpload(files);
            }
        }
    }

    // ------------------------------------------------------------------ check-in
    openCheckin(files = null) {
        this.state.checkin = { file: files?.[0] || null, note: "", submit: true };
        this.state.modal = "checkin";
    }
    onPickCheckin(ev) {
        this.state.checkin.file = ev.target.files[0] || null;
    }
    get checkinNameWarning() {
        const f = this.state.checkin.file;
        const cur = this.state.detail?.revisions.find((r) => r.state === "current");
        return f && cur && f.name !== cur.file_name ? `Tip: keep the same file name (${cur.file_name})` : "";
    }
    async doCheckin() {
        const c = this.state.checkin;
        if (!c.file) return this.notification.add("Choose the edited file", { type: "warning" });
        if (!c.note.trim()) return this.notification.add("Write a short change note", { type: "warning" });
        const res = await this.postForm("/dms/checkin", {
            doc_id: this.state.detail.id, note: c.note, submit: c.submit ? "1" : "0", file: c.file,
        });
        if (!res.ok) return;
        this.state.modal = null;
        sfx.play("upload");
        this.showFlash("Checked in", "wait", c.submit ? "New revision sent for approval" : "New revision saved, not sent");
        await this.reloadAll();
        await this.enterCounts();
    }

    // ------------------------------------------------------------------ approve / refuse / cutoff
    openDecision(rev, kind) {
        this.state.decision = { rev, kind, comment: "" };
        this.state.modal = "decision";
    }
    async doDecision() {
        const d = this.state.decision;
        if (d.kind === "refuse" && !d.comment.trim()) {
            return this.notification.add("Give a reason for refusing", { type: "warning" });
        }
        const ok = await this.act(d.kind, { rev_id: d.rev.id, comment: d.comment });
        if (ok) {
            this.state.modal = null;
            const published = this.state.detail?.revisions.find((r) => r.id === d.rev.id)?.state === "current";
            if (d.kind === "approve") {
                this.showFlash(published ? "Published" : "Approved", "ok", published ? `Revision ${this.state.detail.rev}` : "Sent to the next approver");
            } else {
                this.showFlash("Refused", "bad", "The author has been told why");
            }
            if (this.state.view === "approvals") await this.loadDocs();
        }
    }
    async quickApprove(doc, ev) {
        ev.stopPropagation();
        await this.select(doc);
        const rev = this.state.detail?.revisions.find((r) => r.my_turn);
        if (rev) this.openDecision(rev, "approve");
    }
    openCutoff(rev) {
        this.state.cutoff = { rev, date: rev.cutoff_date };
        this.state.modal = "cutoff";
    }
    async doCutoff() {
        if (await this.act("cutoff", { rev_id: this.state.cutoff.rev.id, date: this.state.cutoff.date }, "Cut-off updated")) {
            this.state.modal = null;
        }
    }

    // ------------------------------------------------------------------ access (Controller)
    async openShare(target) {
        this.state.menuOpen = false;
        const isFolder = target === "folder";
        const t = isFolder ? this.currentFolder : this.state.detail;
        this.state.share = {
            isFolder, id: t.id, title: isFolder ? this.folderLabel(t) : `${t.code} ${t.name}`,
            data: { direct: [], inherited: [], groups: [] }, term: "", results: [], pick: null, level: "view",
        };
        this.state.modal = "share";
        await this.loadShare();
    }
    async loadShare() {
        const s = this.state.share;
        s.data = await this.call("/dms/access", s.isFolder ? { folder_id: s.id } : { doc_id: s.id });
    }
    async searchPrincipals(ev) {
        const s = this.state.share;
        s.term = ev.target.value;
        s.pick = null;
        s.results = s.term ? await this.call("/dms/principals", { term: s.term }) : [];
    }
    pickPrincipal(p) {
        const s = this.state.share;
        s.pick = p;
        s.term = p.name;
        s.results = [];
    }
    async grant() {
        const s = this.state.share;
        if (!s.pick) return this.notification.add("Pick a person first", { type: "warning" });
        await this.call("/dms/access/grant", {
            level: s.level, folder_id: s.isFolder ? s.id : null, doc_id: s.isFolder ? null : s.id,
            user_id: s.pick.id,
        });
        this.notification.add(`${LEVEL_LABEL[s.level]} access granted to ${s.pick.name}`, { type: "success" });
        s.pick = null;
        s.term = "";
        await this.loadShare();
        if (!s.isFolder) {
            await this.loadDetail();
            if (this.state.detailTab === "access") await this.loadFileAccess();
        }
    }
    async revoke(line) {
        await this.call("/dms/access/revoke", { access_id: line.id });
        await this.loadShare();
        if (!this.state.share.isFolder) await this.loadDetail();
    }

    // ------------------------------------------------------------------ backend shortcuts
    openBackend(xmlid) {
        this.actionService.doAction(xmlid);
    }

    // ------------------------------------------------------------------ keyboard
    onKeydown(ev) {
        if (this.state.phase !== "app") return;
        const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
        if (ev.key === "Escape") {
            if (this.state.preview) this.closePreview();
            else if (this.state.modal) this.state.modal = null;
            else if (this.state.detail) this.rollUpDetail();
            return;
        }
        if (typing || this.state.modal || ev.ctrlKey || ev.metaKey || ev.altKey) return;
        if (ev.key === "/") {
            ev.preventDefault();
            this.searchRef.el?.focus();
        } else if (ev.key === "u") {
            ev.preventDefault();
            this.openUpload();
        } else if (["ArrowDown", "ArrowUp", "j", "k"].includes(ev.key) && this.state.docs.length) {
            ev.preventDefault();
            const i = this.state.docs.findIndex((d) => d.id === this.state.selectedId);
            const step = ["ArrowDown", "j"].includes(ev.key) ? 1 : -1;
            const next = this.state.docs[Math.max(0, Math.min(this.state.docs.length - 1, i + step))];
            this.select(next);
        } else if (ev.key === "Enter" && this.state.detail) {
            const rev = this.state.detail.revisions.find((r) => r.can_preview && ["current", "in_review", "draft"].includes(r.state));
            if (rev) this.openPreview(rev);
        }
    }
}

function loadFonts() {
    if (document.getElementById("o_dms_fonts")) return;
    const l = document.createElement("link");
    l.id = "o_dms_fonts";
    l.rel = "stylesheet";
    l.href = "https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@500&family=Cormorant+Garamond:wght@600;700&display=swap";
    document.head.appendChild(l);
}

function localStorageGet(k, d) {
    try {
        return window.localStorage.getItem(k) || d;
    } catch {
        return d;
    }
}
function localStorageSet(k, v) {
    try {
        window.localStorage.setItem(k, v);
    } catch {
        // ignore
    }
}

registry.category("actions").add("dms_app", DmsApp);
