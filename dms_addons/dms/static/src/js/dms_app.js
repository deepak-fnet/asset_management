import { Component, markup, onMounted, onPatched, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
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

function fmtDateTime(s) {
    if (!s) return "—";
    const d = new Date(s.replace(" ", "T") + "Z");  // server times are UTC
    return d.toLocaleString(undefined, { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
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
        this.helpers = { fileKind, humanSize, timeAgo, fmtDate, fmtDateTime, LEVEL_LABEL, STATE_LABEL };
        this.state = useState({
            phase: "checking", // checking | login | app
            login: "", password: "", loginError: "", loggingIn: false, shake: false, userName: "",
            boot: null, view: "recent", folderId: null, docs: [], loading: false,
            search: "", layout: localStorageGet("dms.layout", "list"),
            expanded: {}, selectedId: null, detail: null, detailTab: "revisions",
            dragging: false, modal: null, busy: false, preview: null,
            upload: {}, checkin: {}, decision: {}, cutoff: {}, share: {},
            folderForm: {}, groups: [], groupForm: {}, requests: [], menuOpen: false, aiSearch: null,
            accessRequests: [], accessForm: {}, hardDelete: {},
            voiceLang: localStorageGet("dms.voiceLang", "en-IN"), listening: false,
            navKey: 0, navDir: "fwd", unlocking: false, flash: null, openingTile: null,
            muted: sfx.muted, sideFolded: localStorageGet("dms.side", "open") === "folded", palette: null, hover: null, starPop: null, chat: { messages: [], input: "", image: null, busy: false, step: 0, starters: null, name: "", listening: false,
                    sessionId: null, sessions: [], renaming: null, renameText: "" }, picked: {}, dropFolder: null, dash: null, dashScope: "me", dashTab: "overview", dashRecent: [], dashFlash: null, moveForm: {}, retireForm: {}, closing: false, fileAccess: null,
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
        // Ctrl+K opens the DMS palette, not Odoo's own command palette (capture phase, so it runs first)
        this._ctrlK = (ev) => {
            if (!(ev.ctrlKey || ev.metaKey) || (ev.key || "").toLowerCase() !== "k") return;
            if (this.state.phase !== "app" || this.state.preview) return;
            ev.preventDefault();
            ev.stopImmediatePropagation();
            if (this.state.palette) this.closePalette();
            else this.openPalette();
        };

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
        onPatched(() => {
            if (this.state.modal && this.state.modal !== this._focusedModal) {
                this._focusedModal = this.state.modal;
                const field = document.querySelector(
                    ".o_dms_modal input:not([type=hidden]):not([type=file]):not([type=checkbox]), .o_dms_modal textarea, .o_dms_modal select");
                (field || document.querySelector(".o_dms_modal_foot button:last-child"))?.focus();
            } else if (!this.state.modal && this._focusedModal) {
                this._focusedModal = null;
                if (!this.state.detail && !this.state.preview) this.searchRef.el?.focus();
            }
        });
        onMounted(() => {
            window.addEventListener("keydown", this._keydown);
            window.addEventListener("keydown", this._ctrlK, true);
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
            window.removeEventListener("keydown", this._ctrlK, true);
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
        setTimeout(() => this.searchRef.el?.focus(), 300);  // ready to type or speak a search
        if (this.state.view === "folder" && this.state.folderId) {
            await this.loadDocs();
        } else {
            await this.openView("recent");
        }
        if (boot.open_doc) this.select({ id: boot.open_doc });  // came from the button in a DMS email
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
            dashboard: "Dashboard", assistant: "Ask DMS", groups: "Groups", requests: "Folder deletion requests", recent: "Home", all: "All documents", accessreq: "Access requests", starred: "Starred", approvals: "Waiting for my approval",
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
    // ------------------------------------------------------------------ Ask DMS
    async loadStarters() {
        const C = this.state.chat;
        if (C.starters) return;
        const res = await this.call("/dms/assistant/starters", {});
        if (res) {
            C.starters = res.starters;
            C.name = res.name;
        }
    }
    get askSteps() {
        // what the assistant is doing while it thinks
        const C = this.state.chat;
        const withImage = C.messages.length && C.messages[C.messages.length - 1].image;
        return [
            withImage && { key: "read", icon: "fa-picture-o", text: "Reading your screenshot" },
            { key: "understand", icon: "fa-lightbulb-o", text: "Understanding the question" },
            { key: "search", icon: "fa-search", text: "Searching documents by meaning" },
            { key: "access", icon: "fa-shield", text: "Checking what you can open" },
            { key: "answer", icon: "fa-pencil", text: "Writing the answer" },
        ].filter(Boolean);
    }
    async loadSessions() {
        this.state.chat.sessions = (await this.call("/dms/assistant/sessions", {})) || [];
    }
    async openSession(sess) {
        const C = this.state.chat;
        if (C.busy || C.renaming) return;
        const res = await this.call("/dms/assistant/session", { session_id: sess.id });
        if (!res) return;
        C.sessionId = res.id;
        C.messages = res.messages.map((m) => ({ ...m, at: null }));
        C.input = "";
        C.image = null;
        sfx.play("paper");
        this.askScroll();
    }
    startRename(sess, ev) {
        ev?.stopPropagation();
        this.state.chat.renaming = sess.id;
        this.state.chat.renameText = sess.name;
        setTimeout(() => document.querySelector(".o_dms_hist_rename")?.select(), 30);
    }
    async saveRename(sess) {
        const C = this.state.chat;
        const name = (C.renameText || "").trim();
        if (name && name !== sess.name) {
            await this.call("/dms/assistant/session/rename", { session_id: sess.id, name });
            sess.name = name;
        }
        C.renaming = null;
    }
    onRenameKey(sess, ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.saveRename(sess);
        } else if (ev.key === "Escape") {
            ev.stopPropagation();
            this.state.chat.renaming = null;
        }
    }
    async deleteSession(sess, ev) {
        ev?.stopPropagation();
        if (!window.confirm(`Delete the chat "${sess.name}"?`)) return;
        await this.call("/dms/assistant/session/delete", { session_id: sess.id });
        const C = this.state.chat;
        C.sessions = C.sessions.filter((x) => x.id !== sess.id);
        if (C.sessionId === sess.id) this.newChat();
        sfx.play("trash");
    }
    get chatGroups() {
        // Today / Yesterday / This week / Older
        const now = new Date();
        const day = (d) => Math.floor((now - d) / 864e5);
        const groups = [];
        for (const s of this.state.chat.sessions) {
            const d = new Date((s.last_at || "").replace(" ", "T") + "Z");
            const n = day(d);
            const label = n < 1 && d.getDate() === now.getDate() ? "Today" : n < 2 ? "Yesterday" : n < 7 ? "This week" : "Older";
            if (!groups.length || groups[groups.length - 1].label !== label) groups.push({ label, items: [] });
            groups[groups.length - 1].items.push(s);
        }
        return groups;
    }
    newChat() {
        const C = this.state.chat;
        C.sessionId = null;
        C.messages = [];
        C.input = "";
        C.image = null;
        sfx.play("whoosh");
        setTimeout(() => document.querySelector(".o_dms_ask_input")?.focus(), 50);
    }
    askScroll() {
        setTimeout(() => {
            const el = document.querySelector(".o_dms_ask_thread");
            if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
        }, 60);
    }
    async ask(text = null) {
        const C = this.state.chat;
        if (C.busy) return;
        const message = (text ?? C.input).trim();
        if (!message && !C.image) return;
        const image = C.image;
        C.messages.push({ role: "user", text: message, image: image?.url || null, at: new Date() });
        C.input = "";
        C.image = null;
        C.busy = true;
        C.step = 0;
        sfx.play("tap");
        this.askScroll();
        const n = this.askSteps.length;
        clearInterval(this._askTimer);
        this._askTimer = setInterval(() => { if (C.step < n - 1) C.step++; }, 1400);
        const history = C.messages.slice(-7, -1).map((m) => ({ role: m.role, text: m.text || (m.image ? "(screenshot)" : "") }));
        const context = [...C.messages].reverse().find((m) => m.role === "assistant" && m.focus?.length)?.focus || [];
        try {
            const res = await rpc("/dms/assistant/ask", { message, history, image: image?.url || null, context_doc_ids: context,
                                                          session_id: C.sessionId }, { silent: true });
            C.messages.push({ role: "assistant", ...res, at: new Date() });
            if (res.session) {
                const known = C.sessions.find((x) => x.id === res.session.id);
                if (known) {
                    known.last_at = new Date().toISOString().slice(0, 19).replace("T", " ");
                    C.sessions = [known, ...C.sessions.filter((x) => x !== known)];
                } else {
                    C.sessions = [{ id: res.session.id, name: res.session.name, last_at: new Date().toISOString().slice(0, 19).replace("T", " ") }, ...C.sessions];
                }
                C.sessionId = res.session.id;
            }
            sfx.play(res.cards?.length ? "paper" : "tap");
        } catch (e) {
            C.messages.push({ role: "assistant", error: true, answer: e?.data?.message || e?.message || "Something went wrong. Try again.", cards: [], suggestions: [], at: new Date() });
            sfx.play("error");
        } finally {
            clearInterval(this._askTimer);
            C.busy = false;
            this.askScroll();
            setTimeout(() => document.querySelector(".o_dms_ask_input")?.focus(), 50);
        }
    }
    onAskKey(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.ask();
        }
    }
    onAskInput(ev) {
        this.state.chat.input = ev.target.value;
        ev.target.style.height = "auto";
        ev.target.style.height = Math.min(ev.target.scrollHeight, 160) + "px";
    }
    takeImage(file) {
        if (!file || !file.type.startsWith("image/")) {
            this.notification.add("Paste or drop a picture (PNG or JPG).", { type: "warning" });
            return;
        }
        if (file.size > 8 * 1024 * 1024) {
            this.notification.add("The picture is too big (8 MB at most).", { type: "warning" });
            return;
        }
        const reader = new FileReader();
        reader.onload = () => {
            this.state.chat.image = { url: reader.result, name: file.name || "screenshot.png" };
            sfx.play("paper");
            document.querySelector(".o_dms_ask_input")?.focus();
        };
        reader.readAsDataURL(file);
    }
    onAskPaste(ev) {
        const item = [...(ev.clipboardData?.items || [])].find((i) => i.type.startsWith("image/"));
        if (item) {
            ev.preventDefault();
            this.takeImage(item.getAsFile());
        }
    }
    onAskDrop(ev) {
        const f = ev.dataTransfer?.files?.[0];
        if (f && f.type.startsWith("image/")) {
            ev.preventDefault();
            ev.stopPropagation();
            this.state.dragging = false;
            this.takeImage(f);
        }
    }
    onAskPick(ev) {
        this.takeImage(ev.target.files[0]);
        ev.target.value = "";
    }
    askVoice() {
        const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        const C = this.state.chat;
        if (!SR || !window.isSecureContext) {
            this.notification.add(SR ? "Voice needs a secure address (https, or localhost)." : "This browser does not support voice. Use Chrome or Edge.", { type: "warning" });
            return;
        }
        if (this._askRec) {
            this._askRec.stop();
            return;
        }
        const rec = new SR();
        rec.lang = this.state.voiceLang;
        rec.interimResults = true;
        this._askRec = rec;
        C.listening = true;
        sfx.play("tap");
        rec.onresult = (ev) => {
            C.input = Array.from(ev.results).map((r) => r[0].transcript).join(" ");
            if (ev.results[ev.results.length - 1].isFinal) {
                rec.stop();
                this.ask();
            }
        };
        rec.onend = () => {
            C.listening = false;
            this._askRec = null;
        };
        rec.start();
    }
    askHtml(text) {
        // the assistant's reply as Markdown: headings, lists, tables, quotes, bold, code. HTML is escaped first.
        const esc = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        const inline = (t) => esc(t)
            .replace(/`([^`]+)`/g, "<code>$1</code>")
            .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
            .replace(/(^|[\s(])\*([^*\s][^*]*?)\*(?=[\s).,;:!?]|$)/g, "$1<i>$2</i>");
        const lines = (text || "").replace(/\r/g, "").split("\n");
        const out = [];
        let i = 0;
        while (i < lines.length) {
            const line = lines[i];
            if (/^\s*\|.*\|\s*$/.test(line)) {
                const rows = [];
                while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++]);
                const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
                const body = rows.filter((r) => !(/^[\s|:-]+$/.test(r) && r.includes("-")));  // drop the |---|---| line
                const [head, ...rest] = body;
                out.push('<div class="o_dms_md_tablewrap"><table class="o_dms_md_table"><thead><tr>' +
                    cells(head).map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>" +
                    rest.map((r) => "<tr>" + cells(r).map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</tbody></table></div>");
                continue;
            }
            let m;
            if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
                out.push(`<h${Math.min(m[1].length + 2, 6)} class="o_dms_md_h">${inline(m[2])}</h${Math.min(m[1].length + 2, 6)}>`);
                i++;
            } else if (/^\s*[-*•]\s+/.test(line)) {
                const items = [];
                while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*•]\s+/, ""));
                out.push("<ul>" + items.map((t) => `<li>${inline(t)}</li>`).join("") + "</ul>");
            } else if (/^\s*\d+[.)]\s+/.test(line)) {
                const items = [];
                while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*\d+[.)]\s+/, ""));
                out.push("<ol>" + items.map((t) => `<li>${inline(t)}</li>`).join("") + "</ol>");
            } else if (/^\s*>\s?/.test(line)) {
                const items = [];
                while (i < lines.length && /^\s*>\s?/.test(lines[i])) items.push(lines[i++].replace(/^\s*>\s?/, ""));
                out.push(`<blockquote>${items.map(inline).join("<br/>")}</blockquote>`);
            } else if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
                out.push("<hr/>");
                i++;
            } else if (!line.trim()) {
                i++;
            } else {
                const para = [];
                while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*[-*•]\s|\s*\d+[.)]\s|\s*\||\s*>)/.test(lines[i])) para.push(lines[i++]);
                out.push(`<p>${para.map(inline).join("<br/>")}</p>`);
            }
        }
        return markup(out.join(""));
    }
    levelClass(level) {
        return { edit: "lv_edit", download: "lv_dl", view: "lv_view" }[level] || "";
    }
    openTableRow(card, idx) {
        const id = card.ids?.[idx];
        if (id) this.select({ id });
    }
    money(n, cur) {
        if (!n) return "—";
        const sym = /inr|₹|rs/i.test(cur || "") || !cur ? "₹ " : (cur === "$" ? "$ " : cur + " ");
        return sym + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
    }
    askBarPct(m, list) {
        return Math.max(6, Math.round((m.count / Math.max(1, ...list.map((x) => x.count))) * 100));
    }
    async sendAskRequest(card) {
        if ((card.reason || "").trim().length < 3) {
            card.error = "Tell them why you need it.";
            return;
        }
        card.sending = true;
        try {
            await this.call("/dms/access/ask", { doc_id: card.doc.id, level: card.level, reason: card.reason });
            card.sent = true;
            card.error = "";
            // every card of this document in the chat now shows the request as waiting
            for (const m of this.state.chat.messages) {
                for (const c of m.cards || []) {
                    for (const d of [c, c.doc, ...(c.docs || [])].filter(Boolean)) {
                        if (d.id === card.doc.id && !d.can_see) d.request = { state: "pending", level: card.level };
                    }
                }
            }
            this.showFlash("Requested", "wait", "You will be told when it is decided");
            this.refreshCounts((await this.call("/dms/bootstrap"))?.counts);
        } finally {
            card.sending = false;
        }
    }
    revealPassword(card) {
        card.shown = !card.shown;
        sfx.play(card.shown ? "unlock" : "lock");
    }
    async copyPassword(card) {
        try {
            await navigator.clipboard.writeText(card.password);
            card.copied = true;
            setTimeout(() => (card.copied = false), 1500);
        } catch {
            card.shown = true;
        }
    }

    // ------------------------------------------------------------------ bulk selection
    get pickedDocs() {
        return this.state.docs.filter((d) => this.state.picked[d.id]);
    }
    togglePick(d, ev) {
        ev?.stopPropagation();
        if (this.state.picked[d.id]) delete this.state.picked[d.id];
        else this.state.picked[d.id] = true;
        sfx.play("tap");
    }
    togglePickAll(ev) {
        ev?.stopPropagation();
        const pickable = this.state.docs.filter((d) => d.can_see);
        const all = pickable.length && pickable.every((d) => this.state.picked[d.id]);
        this.state.picked = all ? {} : Object.fromEntries(pickable.map((d) => [d.id, true]));
    }
    get bulkCanMove() {
        return this.pickedDocs.length && this.pickedDocs.every((d) => d.level === "edit" && !["archived", "discarded", "in_review"].includes(d.state) && !d.locked_by);
    }
    get bulkCanRetire() {
        return this.state.boot?.user.is_controller && this.pickedDocs.length
            && this.pickedDocs.every((d) => ["published", "draft"].includes(d.state) && !d.locked_by && d.pending_state !== "in_review");
    }
    async bulkStar() {
        const docs = this.pickedDocs.filter((d) => !d.starred);
        for (const d of docs) {
            await this.call("/dms/action", { action: "star", doc_id: d.id });
            d.starred = true;
        }
        this.showFlash("Starred", "ok", `${docs.length} document(s)`);
        this.state.picked = {};
    }

    // ------------------------------------------------------------------ hover preview
    hoverDoc(d, ev) {
        clearTimeout(this._hoverT);
        if (this.state.dragging || this.state.palette || this.state.modal || !d.can_see) return;
        const x = ev.clientX, y = ev.clientY;
        this._hoverT = setTimeout(() => {
            const w = 300, h = 380;
            const left = x + 24 + w > window.innerWidth ? Math.max(8, x - w - 24) : x + 24;
            const top = Math.max(8, Math.min(window.innerHeight - h - 8, y - 60));
            this.state.hover = { doc: d, left, top, img: d.view_rev_id ? `/dms/thumb/${d.view_rev_id}` : null, failed: false };
        }, 450);
    }
    unhoverDoc() {
        clearTimeout(this._hoverT);
        this.state.hover = null;
    }

    // ------------------------------------------------------------------ drop a file on a folder
    canDropOn(f) {
        return f.level === "edit" && !f.is_archive;
    }
    onFolderDragOver(f, ev) {
        if (!ev.dataTransfer?.types?.includes("Files")) return;
        ev.preventDefault();
        ev.stopPropagation();
        this.state.dragging = false;
        this.state.dropFolder = this.canDropOn(f) ? f.id : null;
        ev.dataTransfer.dropEffect = this.canDropOn(f) ? "copy" : "none";
    }
    onFolderDragLeave(f, ev) {
        if (ev.currentTarget.contains(ev.relatedTarget)) return;
        if (this.state.dropFolder === f.id) this.state.dropFolder = null;
    }
    onFolderDrop(f, ev) {
        ev.preventDefault();
        ev.stopPropagation();
        this.state.dropFolder = null;
        this.state.dragging = false;
        const files = ev.dataTransfer?.files;
        if (!files?.length) return;
        if (!this.canDropOn(f)) {
            return this.notification.add(`You cannot upload into ${f.name}. You need Edit access.`, { type: "warning" });
        }
        sfx.play("folder");
        this.openUpload(files, f);
    }

    // ------------------------------------------------------------------ command palette (Ctrl+K)
    openPalette() {
        this.unhoverDoc();
        this.state.palette = { q: "", idx: 0, docs: [], searching: false };
        sfx.play("open");
        setTimeout(() => document.querySelector(".o_dms_palette input")?.focus(), 30);
    }
    closePalette() {
        this.state.palette = null;
    }
    get paletteItems() {
        const P = this.state.palette;
        if (!P) return [];
        const q = P.q.trim().toLowerCase();
        const hit = (t) => !q || t.toLowerCase().includes(q);
        const boot = this.state.boot;
        const ctl = boot.user.is_controller;
        const items = [];
        const d = this.state.detail && !this.state.detail.restricted ? this.state.detail : null;
        if (d) {
            const cur = d.revisions.find((r) => r.state === "current") || d.revisions[0];
            const docActs = [
                cur?.can_preview && { icon: "fa-eye", label: `Open ${d.code}`, run: () => this.openPreview(cur) },
                cur?.can_download && { icon: "fa-download", label: `Download ${d.code}`, run: () => this.download(cur) },
                d.can_checkout && { icon: "fa-pencil", label: `Check out ${d.code}`, run: () => this.checkout() },
                d.can_checkin && { icon: "fa-arrow-up", label: `Check in a new version of ${d.code}`, run: () => this.openCheckin() },
                d.can_move && { icon: "fa-share", label: `Move ${d.code}`, run: () => this.openMove() },
                d.can_manage_access && { icon: "fa-user-plus", label: `Share ${d.code}`, run: () => this.openShare("doc") },
                { icon: d.starred ? "fa-star" : "fa-star-o", label: `${d.starred ? "Unstar" : "Star"} ${d.code}`, run: () => this.toggleStar(d) },
            ].filter(Boolean);
            for (const a of docActs) if (hit(a.label)) items.push({ ...a, section: "This document" });
        }
        const go = [
            { icon: "fa-arrow-up", label: "Upload a document", keys: "U", run: () => this.openUpload() },
            { icon: "fa-home", label: "Home", run: () => this.openView("recent") },
            { icon: "fa-tachometer", label: "Dashboard", run: () => this.openView("dashboard") },
            ctl && { icon: "fa-shield", label: "Unusual activity", run: async () => { await this.openView("dashboard"); await this.loadDashboard("admin"); this.setDashTab("alerts"); } },
            { icon: "fa-files-o", label: "All documents", run: () => this.openView("all") },
            { icon: "fa-gavel", label: "To approve", run: () => this.openView("approvals") },
            { icon: "fa-pencil-square-o", label: "My documents", run: () => this.openView("my_uploads") },
            { icon: "fa-lock", label: "Checked out", run: () => this.openView("checked_out") },
            { icon: "fa-star-o", label: "Starred", run: () => this.openView("starred") },
            (ctl || boot.counts.access_requests) && { icon: "fa-key", label: "Access requests", run: () => this.openView("accessreq") },
            ctl && { icon: "fa-users", label: "Groups", run: () => this.openView("groups") },
            ctl && { icon: "fa-history", label: "Audit log", run: () => this.openBackend("dms.action_dms_log") },
            { icon: this.state.sideFolded ? "fa-angle-double-right" : "fa-angle-double-left", label: this.state.sideFolded ? "Open the menu" : "Fold the menu", keys: "[", run: () => this.toggleSide() },
            { icon: this.state.muted ? "fa-volume-up" : "fa-volume-off", label: this.state.muted ? "Turn sounds on" : "Turn sounds off", run: () => this.toggleMute() },
        ].filter(Boolean);
        for (const a of go) if (hit(a.label)) items.push({ ...a, section: "Go to" });
        if (q) {
            for (const f of boot.folders.filter((f) => hit(f.name)).slice(0, 5)) {
                items.push({ section: "Folders", icon: f.is_archive ? "fa-archive" : "fa-folder", label: this.folderLabel(f), run: () => this.openFolder(f) });
            }
            for (const doc of P.docs) {
                items.push({ section: "Documents", icon: "fa-file-text-o", label: `${doc.code || ""} ${doc.name}`, sub: doc.folder, run: () => this.select(doc) });
            }
        }
        return items.slice(0, 40);
    }
    onPaletteInput(ev) {
        const P = this.state.palette;
        P.q = ev.target.value;
        P.idx = 0;
        clearTimeout(this._palT);
        const q = P.q.trim();
        if (q.length < 2) {
            P.docs = [];
            return;
        }
        this._palT = setTimeout(async () => {
            P.searching = true;
            const docs = await this.call("/dms/documents", { view: "search", search: q });
            if (this.state.palette === P && P.q.trim() === q) P.docs = (docs || []).slice(0, 8);
            P.searching = false;
        }, 220);
    }
    onPaletteKey(ev) {
        const P = this.state.palette;
        const n = this.paletteItems.length;
        if (ev.key === "ArrowDown") {
            ev.preventDefault();
            P.idx = n ? (P.idx + 1) % n : 0;
        } else if (ev.key === "ArrowUp") {
            ev.preventDefault();
            P.idx = n ? (P.idx - 1 + n) % n : 0;
        } else if (ev.key === "Enter") {
            ev.preventDefault();
            const it = this.paletteItems[P.idx];
            if (it) this.runPalette(it);
        } else if (ev.key === "Escape") {
            ev.preventDefault();
            ev.stopPropagation();
            this.closePalette();
        }
        setTimeout(() => document.querySelector(".o_dms_palette li.active")?.scrollIntoView({ block: "nearest" }), 0);
    }
    runPalette(it) {
        this.closePalette();
        sfx.play("click");
        it.run();
    }

    toggleSide() {
        // a folded menu leaves the room to the list and the document panel
        this.state.sideFolded = !this.state.sideFolded;
        localStorageSet("dms.side", this.state.sideFolded ? "folded" : "open");
        sfx.play("tap");
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
        if (view === "assistant") {
            this.loadStarters();
            this.loadSessions();
            setTimeout(() => document.querySelector(".o_dms_ask_input")?.focus(), 120);
        } else if (view === "dashboard") {
            await this.loadDashboard();
        } else if (view === "groups") {
            await this.loadGroups();
        } else if (view === "requests") {
            await this.loadRequests();
        } else if (view === "accessreq") {
            await this.loadAccessRequests();
        } else {
            await this.loadDocs();
        }
    }

    // ------------------------------------------------------------------ folders
    // ------------------------------------------------------------------ dashboard
    async loadDashboard(scope = null) {
        if (scope && scope !== this.state.dashScope) {
            this.state.dashScope = scope;
            this.state.dashTab = "overview";
        }
        if (!this.state.boot?.user.is_controller) this.state.dashScope = "me";
        this.state.loading = true;
        const [data, recent] = await Promise.all([
            this.call("/dms/dashboard", { scope: this.state.dashScope }),
            this.call("/dms/documents", { view: "recent" }),
        ]);
        this.state.loading = false;
        if (data) this.state.dash = { ...data, scope: this.state.dashScope };
        this.state.dashRecent = (recent || []).slice(0, 12);
    }
    get dashTabs() {
        const D = this.state.dash;
        if (!D) return [];
        if (D.scope === "admin") {
            return [
                { key: "overview", label: "Overview", icon: "fa-th-large" },
                { key: "activity", label: "Activity", icon: "fa-line-chart" },
                { key: "alerts", label: "Unusual activity", icon: "fa-shield", count: D.alerts.length, hot: D.alerts.some((a) => a.level === "high") },
                { key: "attention", label: "Needs attention", icon: "fa-flag",
                  count: D.expiring.length + D.overdue.length + D.sensitive.length + D.dups.length + D.stale.length, hot: true },
            ];
        }
        return [
            { key: "overview", label: "Overview", icon: "fa-th-large" },
            { key: "activity", label: "My activity", icon: "fa-line-chart" },
            { key: "attention", label: "My to-do", icon: "fa-flag",
              count: D.expiring.length + D.cutoff_soon.length + D.requests.filter((q) => q.state === "pending").length, hot: true },
        ];
    }
    setDashTab(tab) {
        sfx.play("tap");
        this.state.dashTab = tab;
        this.state.dashFlash = null;
    }
    dashGo(target) {
        // a count card: open a page ("all", "approvals"), the back office ("backend:xmlid"),
        // or a dashboard tab and the section inside it ("tab:attention#sec-expiring")
        if (!target) return;
        if (target.startsWith("tab:")) {
            const [tab, sid] = target.slice(4).split("#");
            this.setDashTab(tab);
            if (sid) {
                setTimeout(() => {
                    document.getElementById(sid)?.scrollIntoView({ behavior: "smooth", block: "start" });
                    this.state.dashFlash = sid;
                    setTimeout(() => { if (this.state.dashFlash === sid) this.state.dashFlash = null; }, 1600);
                }, 60);
            }
        } else if (target.startsWith("backend:")) {
            this.openBackend(target.slice(8));
        } else {
            this.openView(target);
        }
    }
    get dashTrend() {
        const t = this.state.dash?.trend;
        if (!t) return { days: [], max: 1, sum: { up: 0, view: 0, down: 0 } };
        const days = t.labels.map((label, i) => ({
            label, up: t.series.upload[i], view: t.series.view[i], down: t.series.download[i],
            total: t.series.upload[i] + t.series.view[i] + t.series.download[i],
        }));
        return { days, max: Math.max(1, ...days.map((d) => d.total)),
                 sum: days.reduce((a, d) => ({ up: a.up + d.up, view: a.view + d.view, down: a.down + d.down }), { up: 0, view: 0, down: 0 }) };
    }
    get hours24() {
        return [...Array(24).keys()];
    }
    // one fixed colour per kind of action (validated categorical order), never by rank
    actionColor(key) {
        return { view: "#2a78d6", download: "#eb6834", upload: "#1baf7a", approval: "#eda100", access: "#e87ba4", ai: "#008300", other: "#9aa1ad" }[key] || "#9aa1ad";
    }
    actionLabel(key) {
        return { view: "Views", download: "Downloads", upload: "Uploads & edits", approval: "Approvals", access: "Access", ai: "AI", other: "Other" }[key] || key;
    }
    actionGroup(action) {
        return {
            view: "view", download: "download", upload: "upload", checkin: "upload", checkout: "upload", unlock: "upload", force_unlock: "upload",
            submit: "approval", approve: "approval", refuse: "approval", publish: "approval", cutoff: "approval", archive: "approval",
            access_grant: "access", access_revoke: "access", login: "access", ai_check: "ai", ai_search: "ai",
        }[action] || "other";
    }
    actionIcon(group, action = null) {
        const exact = { approve: "fa-check", refuse: "fa-times", checkout: "fa-lock", checkin: "fa-arrow-up", unlock: "fa-unlock",
                        force_unlock: "fa-chain-broken", access_revoke: "fa-user-times", login: "fa-sign-in", discard: "fa-trash", ai_search: "fa-search" };
        return (action && exact[action]) || { view: "fa-eye", download: "fa-download", upload: "fa-arrow-up", approval: "fa-gavel",
                                              access: "fa-key", ai: "fa-magic", other: "fa-circle" }[group] || "fa-circle";
    }
    alertIcon(kind) {
        return { bulk_download: "fa-download", bulk_view: "fa-eye", off_hours: "fa-moon-o", mass_change: "fa-trash-o",
                 permission_spike: "fa-unlock-alt", access_fishing: "fa-key", force_unlock: "fa-chain-broken", asked_again: "fa-repeat" }[kind] || "fa-exclamation";
    }
    heatColor(n, max) {
        // one hue, light to dark; empty cells stay paper
        if (!n) return "transparent";
        const ramp = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"];
        return ramp[Math.min(ramp.length - 1, Math.floor((n / max) * (ramp.length - 1) + 0.5))];
    }
    feedByDay(list, maxRows = 18) {
        const out = [];
        let rows = 0;
        const today = new Date().toDateString();
        const yday = new Date(Date.now() - 864e5).toDateString();
        for (const l of list) {
            const d = new Date(l.date.replace(" ", "T") + "Z");
            const key = d.toDateString();
            const day = key === today ? "Today" : key === yday ? "Yesterday" : d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
            if (!out.length || out[out.length - 1].day !== day) out.push({ day, items: [], dots: [] });
            const g = out[out.length - 1];
            g.dots.push(l);
            const prev = g.items[g.items.length - 1];
            if (prev && prev.user === l.user && prev.action === l.action) {
                prev.times = (prev.times || 1) + 1;
                if (prev.doc !== l.doc) prev.doc = "";
                continue;
            }
            if (rows >= maxRows) continue;
            g.items.push({ ...l });
            rows += 1;
        }
        return out.filter((g) => g.items.length);
    }
    heatTitle(day, hour, n) {
        return `${day}, ${String(hour).padStart(2, "0")}:00 - ${n} action(s)`;
    }
    feedTime(s) {
        return new Date(s.replace(" ", "T") + "Z").toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
    }
    get alertSeverity() {
        const alerts = this.state.dash?.alerts || [];
        const levels = [
            { key: "high", label: "High", icon: "fa-exclamation-circle" },
            { key: "medium", label: "Medium", icon: "fa-exclamation-triangle" },
            { key: "low", label: "Low", icon: "fa-info-circle" },
        ].map((l) => ({ ...l, count: alerts.filter((a) => a.level === l.key).length }));
        const by = {};
        for (const a of alerts) {
            const p = (by[a.user_id] ||= { user_id: a.user_id, name: a.user, high: 0, medium: 0, low: 0, total: 0 });
            p[a.level] += 1;
            p.total += 1;
        }
        const people = Object.values(by).sort((a, b) => b.high - a.high || b.total - a.total);
        return { levels, people, max: Math.max(1, ...people.map((p) => p.total)) };
    }
    expiryPos(days, window) {
        // -7 (a week late) .. window, as a percentage across the time line
        const lo = -7;
        const x = Math.round(((Math.max(lo, Math.min(window, days)) - lo) / (window - lo)) * 1000) / 10;
        return Math.max(2, Math.min(96, x));  // keep pins and labels inside the card
    }
    pct(n, list) {
        const max = Math.max(1, ...(list || []).map((x) => x.count));
        return Math.max(3, Math.round((n / max) * 100));
    }
    donut(list) {
        const colors = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
        const total = list.reduce((n, x) => n + x.count, 0) || 1;
        let at = 0;
        const stops = list.map((x, i) => {
            const from = at; at += (x.count / total) * 100;
            // a thin paper gap between slices
            return `${colors[i % colors.length]} ${from}% ${Math.max(from, at - 0.6)}%, var(--paper) ${Math.max(from, at - 0.6)}% ${at}%`;
        });
        return { style: `background: conic-gradient(${stops.join(", ") || "var(--rule) 0 100%"})`, colors, total };
    }
    openDashDoc(id) {
        if (id) this.select({ id });
    }

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
            this.showFlash("Created", "ok", `Folder ${f.name.trim()}`);
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
        this.showFlash("Renamed", "ok");
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
    openMove(docs = null) {
        if (docs) {
            const folders = new Set(docs.map((d) => d.folder_id));
            this.state.moveForm = { target: null, title: `${docs.length} documents`, ids: docs.map((d) => d.id),
                                    current: folders.size === 1 ? docs[0].folder_id : null };
        } else {
            const d = this.state.detail;
            this.state.moveForm = { target: null, title: `${d.code} ${d.name}`, current: d.folder_id, ids: [d.id] };
        }
        this.state.modal = "move";
    }
    async doMove() {
        const m = this.state.moveForm;
        if (!m.target) return this.notification.add("Pick the folder to move it into", { type: "warning" });
        try {
            await this.call("/dms/document/move", { doc_ids: m.ids, folder_id: m.target });
            this.state.modal = null;
            this.state.picked = {};
            const target = this.state.boot.folders.find((f) => f.id === m.target);
            this.showFlash("Moved", "ok", `${m.ids.length > 1 ? m.ids.length + " documents to " : ""}${this.folderLabel(target)}`);
            this.state.boot = await this.call("/dms/bootstrap");
            await this.reloadAll();
        } catch {
            // notified
        }
    }
    openRetire(docs = null) {
        this.state.retireForm = docs
            ? { reason: "", ids: docs.map((d) => d.id), title: `${docs.length} documents` }
            : { reason: "", ids: [this.state.detail.id], title: this.state.detail.code };
        this.state.modal = "retire";
    }
    async doRetire() {
        try {
            const r = this.state.retireForm;
            await this.call("/dms/document/retire", { doc_ids: r.ids, reason: r.reason });
            this.state.modal = null;
            this.state.picked = {};
            this.showFlash("Archived", "old", r.ids.length > 1 ? `${r.ids.length} documents retired` : "Retired to the Archive");
            this.state.boot = await this.call("/dms/bootstrap");
            await this.reloadAll();
        } catch {
            // notified
        }
    }
    // ------------------------------------------------------------------ quick actions, access requests, delete
    folderIcon(f) {
        if (f.is_archive) return "fa-archive";
        const n = (f.name || "").toLowerCase();
        const map = [
            [/account|finance|invoice|bill/, "fa-file-text-o"], [/admin/, "fa-building-o"],
            [/\bhr\b|human|people|staff|employee/, "fa-users"], [/quality|qa\b|audit|compliance/, "fa-shield"],
            [/software|it\b|tech|develop/, "fa-tags"], [/purchase|procure|store/, "fa-shopping-cart"],
            [/sales|market/, "fa-line-chart"], [/project/, "fa-briefcase"], [/legal|contract/, "fa-gavel"],
        ];
        for (const [rx, icon] of map) if (rx.test(n)) return icon;
        return "fa-folder-open-o";
    }
    quickOpen(d, ev) {
        ev?.stopPropagation();
        this.openPreview({ id: d.view_rev_id });
    }
    quickDownload(d, ev) {
        ev?.stopPropagation();
        this.download({ id: d.download_rev_id });
    }
    openAccessRequest(d, ev) {
        ev?.stopPropagation();
        this.state.accessForm = { doc: d, code: d.code, name: d.name, level: d.request?.level || "view", reason: "" };
        this.state.modal = "accessask";
    }
    async sendAccessRequest() {
        const r = this.state.accessForm;
        try {
            const res = await this.call("/dms/access/ask", { doc_id: r.doc.id, level: r.level, reason: r.reason });
            this.refreshCounts(res.counts);
            this.state.modal = null;
            r.doc.request = { state: "pending", level: r.level };
            if (this.state.detail?.id === r.doc.id) this.state.detail.request = { state: "pending", level: r.level };
            this.showFlash("Requested", "wait", "You will be told when it is decided");
        } catch {
            // notified
        }
    }
    async loadAccessRequests() {
        this.state.loading = true;
        try {
            this.state.accessRequests = await this.call("/dms/access/requests");
        } finally {
            this.state.loading = false;
        }
    }
    async decideAccess(q, approve) {
        const note = approve ? null : (window.prompt(`Why refuse ${q.user}'s request?`) || "");
        try {
            const res = await this.call("/dms/access/decide", { request_id: q.id, approve, level: q.level, note });
            this.refreshCounts(res.counts);
            this.showFlash(approve ? "Granted" : "Refused", approve ? "ok" : "bad", approve ? `${q.user}: ${LEVEL_LABEL[q.level]} on ${q.code}` : q.user);
            await this.loadAccessRequests();
        } catch {
            // notified
        }
    }
    openHardDelete() {
        const d = this.state.detail;
        this.state.hardDelete = { id: d.id, code: d.code, name: d.name, confirm: "" };
        this.state.modal = "harddelete";
    }
    async doHardDelete() {
        const x = this.state.hardDelete;
        this.state.busy = true;
        try {
            const res = await this.call("/dms/document/delete", { doc_id: x.id, confirm: x.confirm });
            this.refreshCounts(res.counts);
            this.state.modal = null;
            this.closeDetail();
            this.showFlash("Deleted", "trash", x.code);
            this.state.boot = await this.call("/dms/bootstrap");
            await this.loadDocs();
        } catch {
            // notified
        } finally {
            this.state.busy = false;
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
            this.showFlash("Saved", "ok", `Group ${g.name}`);
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
        if (this.state.view === "recent" && !this.state.search) {
            this.state.docs = [];  // Home shows the folders; recently changed documents are on the dashboard
            return;
        }
        this.state.loading = true;
        this.state.picked = {};
        try {
            const view = this.state.search && this.state.view !== "folder" ? "search" : this.state.view;
            this.state.docs = await this.call("/dms/documents", {
                folder_id: this.state.folderId, view, search: this.state.search || null,
            });
        } finally {
            this.state.loading = false;
        }
        // nothing found by the plain search: let the AI try (spelling, synonyms, file content)
        const q = (this.state.search || "").trim();
        if (!this.state.docs.length && q.length >= 3 && this.state.view === "search" && this.state.boot?.ai && !this.state.aiSearch) {
            this.aiSearch();
        }
    }
    onSearchInput(ev) {
        this.state.search = ev.target.value;
        this.state.aiSearch = null;
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
        const id = this.state.selectedId;
        this.state.detail = await this.call("/dms/document", { doc_id: id });
        // while the AI is still reading a file, look again every few seconds
        clearTimeout(this._aiPoll);
        const pending = this.state.detail?.revisions.some((r) => r.ai?.state === "pending" || r.insights?.state === "pending");
        if (pending) {
            this._aiPoll = setTimeout(async () => {
                if (this.state.selectedId !== id) return;
                const wasPending = this.state.detail.revisions.filter((r) => r.ai?.state === "pending").map((r) => r.id);
                await this.loadDetail();
                const done = this.state.detail?.revisions.find((r) => wasPending.includes(r.id) && r.ai?.state !== "pending");
                if (done) {
                    if (done.ai.blocked) {
                        this.notification.add("AI check: some rules did not pass. See the reasons and upload a corrected file.", { type: "warning" });
                    } else if (done.ai.state === "done") {
                        this.showFlash("Checked", "ok", "All required rules passed");
                    }
                    this.loadDocs();
                }
            }, 4000);
        }
    }
    aiPassCount(r) {
        return r.ai.results.filter((x) => x.passed).length;
    }
    pickCorrected(rev) {
        const input = document.createElement("input");
        input.type = "file";
        input.onchange = async () => {
            const f = input.files[0];
            if (!f) return;
            const res = await this.postForm("/dms/revision/replace", { rev_id: rev.id, file: f });
            if (!res.ok) return;
            sfx.play("upload");
            this.showFlash("Checking", "wait", "Corrected file sent to the AI check");
            await this.reloadAll();
        };
        input.click();
    }
    recheck(rev) {
        return this.act("ai_recheck", { rev_id: rev.id }, "Checking again");
    }
    async aiSearch() {
        const q = (this.state.search || "").trim();
        if (!q || !this.state.boot.ai) return;
        clearTimeout(this._searchTimer);
        this.state.view = "search";
        this.state.folderId = null;
        this.closeDetail();
        this.state.aiSearch = { loading: true, query: q };
        this.state.loading = true;
        sfx.play("tap");
        try {
            const res = await this.call("/dms/ai/search", { query: q });
            if (this.state.aiSearch?.query !== q) return;
            this.state.docs = res.rows;
            this.state.aiSearch = { loading: false, query: q, explanation: res.explanation, keywords: res.keywords, type: res.type, reply: res.reply };
            if (this._speakNext) {
                this._speakNext = false;
                this.speak(res.reply, res.language === "ta" ? "ta-IN" : "en-IN");
            }
        } catch {
            this.state.aiSearch = null;
        } finally {
            this.state.loading = false;
        }
    }
    // ------------------------------------------------------------------ voice search
    toggleVoiceLang() {
        this.state.voiceLang = this.state.voiceLang === "ta-IN" ? "en-IN" : "ta-IN";
        localStorageSet("dms.voiceLang", this.state.voiceLang);
    }
    voiceSearch() {
        const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SR || !window.isSecureContext) {
            this.notification.add(
                SR ? "Voice search needs a secure address (https, or localhost)." : "This browser does not support voice search. Use Chrome or Edge.",
                { type: "warning" });
            return;
        }
        if (this._rec) {
            this._rec.stop();
            return;
        }
        const rec = new SR();
        rec.lang = this.state.voiceLang;
        rec.interimResults = true;
        rec.maxAlternatives = 1;
        this._rec = rec;
        this.state.listening = true;
        sfx.play("tap");
        rec.onresult = (ev) => {
            const text = Array.from(ev.results).map((r) => r[0].transcript).join(" ");
            this.state.search = text;
            if (this.searchRef.el) this.searchRef.el.value = text;
            if (ev.results[ev.results.length - 1].isFinal) {
                this._speakNext = true;
                rec.stop();
                this.aiSearch();
            }
        };
        rec.onerror = (ev) => {
            if (ev.error !== "aborted" && ev.error !== "no-speech") {
                this.notification.add(`Voice search: ${ev.error === "not-allowed" ? "allow the microphone for this site" : ev.error}`, { type: "warning" });
            }
        };
        rec.onend = () => {
            this.state.listening = false;
            this._rec = null;
        };
        rec.start();
    }
    speak(text, lang) {
        if (!text || !window.speechSynthesis || this.state.muted) return;
        const u = new SpeechSynthesisUtterance(text);
        u.lang = lang;
        const voice = window.speechSynthesis.getVoices().find((v) => v.lang === lang) ||
            window.speechSynthesis.getVoices().find((v) => v.lang.startsWith(lang.slice(0, 2)));
        if (voice) u.voice = voice;
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(u);
    }
    onSearchKey(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.aiSearch();
        }
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
            if (okMsg && typeof okMsg === "object") this.showFlash(okMsg.text, okMsg.kind || "ok", okMsg.sub || "");
            else if (okMsg) this.notification.add(okMsg, { type: "success" });
            await this.reloadAll();
            return true;
        } catch {
            return false;
        } finally {
            this.state.busy = false;
        }
    }
    rebuildInsights(rev) {
        return this.act("insights", { rev_id: rev.id }, "Reading the file again");
    }
    riskIcon(level) {
        return { high: "fa-exclamation-circle", medium: "fa-exclamation-triangle", low: "fa-info-circle" }[level] || "fa-info-circle";
    }
    expiryText(e) {
        if (!e) return "";
        if (e.days < 0) return `${e.label || "Expired"}: ${-e.days} day(s) ago`;
        if (e.days === 0) return `${e.label || "Expires"}: today`;
        return `${e.label || "Expires"} in ${e.days} day(s)`;
    }
    async toggleStar(doc, ev) {
        ev?.stopPropagation();
        await this.call("/dms/action", { action: "star", doc_id: doc.id });
        doc.starred = !doc.starred;
        sfx.play(doc.starred ? "stamp" : "tap");
        this.state.starPop = doc.starred ? doc.id : null;
        setTimeout(() => (this.state.starPop = null), 600);
        if (this.state.detail?.id === doc.id) this.state.detail.starred = doc.starred;
        if (this.state.view === "starred") this.loadDocs();
    }
    checkout() {
        return this.act("checkout", {}, { text: "Checked out", kind: "wait", sub: "Download, edit, then check in" });
    }
    unlock() {
        return this.act("unlock", {}, { text: "Unlocked", kind: "ok" });
    }
    submitRev(rev) {
        return this.act("submit", { rev_id: rev.id }, { text: "Sent", kind: "wait", sub: "For approval L1 → L2" + (this.state.detail.approvers.l3 ? " → L3" : "") });
    }

    // ------------------------------------------------------------------ files
    fileUrl(rev, download = false) {
        return `/dms/file/${rev.id}${download ? "?download=1" : ""}`;
    }
    download(rev) {
        const a = document.createElement("a");
        a.href = this.fileUrl(rev, true);
        a.click();
        if (this.state.boot?.download_protected) {
            this.notification.add("The downloaded file is password-protected. Use the DMS download password to open it.", { type: "info" });
        }
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
    openUpload(files = null, into = null) {
        // dropped on a folder: that folder stays, the AI does not move it elsewhere
        const folder = into || (this.canUploadHere ? this.currentFolder : this.editableFolders[0]);
        if (!folder) {
            this.notification.add("You have no folder with Edit access. Ask the Document Controller.", { type: "warning" });
            return;
        }
        const file = files?.[0] || null;
        this.state.upload = {
            folderId: folder.id, typeId: (this.state.boot.types.find((t) => !t.l3) || this.state.boot.types[0])?.id, file,
            title: file ? file.name.replace(/\.[^.]+$/, "") : "", description: "", submit: true,
            check: null, checking: false, ai: null, touched: into ? { folderId: true } : {},
        };
        this.state.modal = "upload";
        if (file) this.aiFill(file);
    }
    onPickFile(ev) {
        const f = ev.target.files[0];
        if (!f) return;
        this.state.upload.file = f;
        this.state.upload.check = null;  // a new file needs a new check
        this.state.upload.error = null;
        if (!this.state.upload.title || !this.state.upload.touched.title) this.state.upload.title = f.name.replace(/\.[^.]+$/, "");
        ev.target.value = "";
        this.aiFill(f);
    }
    onUploadTypeChange() {
        this.state.upload.check = null;
        this.state.upload.touched.typeId = true;
    }
    touchUpload(key) {
        this.state.upload.touched[key] = true;
    }
    async aiFill(file) {
        // the AI reads the file and fills type, folder, title and description; fields the user changed stay as they are
        if (!this.state.boot.ai) return;
        const u = this.state.upload;
        u.ai = { state: "reading" };
        const fd = new FormData();
        fd.append("file", file);
        fd.append("csrf_token", odoo.csrf_token);
        let res;
        try {
            const r = await fetch("/dms/ai/classify", { method: "POST", body: fd });
            res = await r.json();
        } catch {
            res = { ok: false, reason: "The AI could not read this file." };
        }
        if (this.state.upload !== u || u.file !== file) return;  // another file was chosen meanwhile
        if (!res.ok) {
            u.ai = { state: "none", reason: res.reason || res.error, sensitive: res.sensitive, duplicates: res.duplicates };
            if (this.uploadNeedsCheck && !u.check && !u.checking) this.runPrecheck();
            return;
        }
        const filled = [];
        if (res.type_id && !u.touched.typeId && res.type_id !== u.typeId) { u.typeId = res.type_id; u.check = null; filled.push("type"); }
        else if (res.type_id === u.typeId) filled.push("type");
        if (res.folder_id && !u.touched.folderId) { u.folderId = res.folder_id; filled.push("folder"); }
        if (res.title && !u.touched.title) { u.title = res.title; filled.push("title"); }
        if (res.description && !u.touched.description) { u.description = res.description; filled.push("description"); }
        u.ai = { state: "done", ...res, filled };
        sfx.play("success");
        // a type with upload rules: start its check straight away
        if (this.uploadNeedsCheck && !u.check && !u.checking) this.runPrecheck();
    }
    get uploadLocked() {
        // from choosing the file until the AI has picked the type and the rule check is done
        const u = this.state.upload;
        return Boolean(u.file && (u.ai?.state === "reading" || u.checking));
    }
    get uploadNeedsCheck() {
        return Boolean(this.uploadType?.ai && this.state.boot.ai);
    }
    async runPrecheck() {
        const u = this.state.upload;
        if (!u.file) return this.notification.add("Choose a file", { type: "warning" });
        u.checking = true;
        u.check = null;
        u.error = null;
        sfx.play("paper");
        const fd = new FormData();
        fd.append("type_id", u.typeId);
        fd.append("title", u.title || "");
        fd.append("file", u.file);
        fd.append("csrf_token", odoo.csrf_token);
        try {
            const r = await fetch("/dms/ai/precheck", { method: "POST", body: fd });
            const res = await r.json().catch(() => ({ ok: false, error: `The check failed (${r.status})` }));
            if (!res.ok) {
                u.error = res.error;  // shown inside the dialog: notifications sit behind it
                sfx.play("error");
                return;
            }
            u.check = res;
            if (res.blocked) {
                this.notification.add(`${res.total - res.passed} rule(s) not passed. Fix the file and choose the corrected one.`, { type: "warning" });
            } else {
                this.showFlash("Passed", "ok", `${res.passed} of ${res.total} rules passed`);
            }
        } finally {
            u.checking = false;
        }
    }
    chooseCorrected() {
        this.fileRef.el?.click();
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
        if (this.uploadNeedsCheck && (!u.check || u.check.blocked)) {
            return this.notification.add("Check the file first. It can be uploaded once every required rule passes.", { type: "warning" });
        }
        const res = await this.postForm("/dms/upload", {
            folder_id: u.folderId, type_id: u.typeId, title: u.title, description: u.description,
            submit: u.submit ? "1" : "0", file: u.file, precheck_id: u.check ? u.check.id : null,
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
        this.showFlash("Shared", "ok", `${s.pick.name}: ${LEVEL_LABEL[s.level]}`);
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
        sfx.play("trash");
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
        if (this.state.palette) return;  // the palette handles its own keys
        if (ev.key === "Enter" && this.state.modal && !ev.shiftKey && document.activeElement?.tagName !== "TEXTAREA") {
            // Enter in any dialog presses its main button (the last enabled one in the footer)
            const btns = [...document.querySelectorAll(".o_dms_modal_foot button")].filter((b) => !b.disabled);
            const main = btns[btns.length - 1];
            if (main && !main.textContent.trim().match(/^cancel$/i)) {
                ev.preventDefault();
                main.click();
            }
            return;
        }
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
        } else if (ev.key === "[") {
            ev.preventDefault();
            this.toggleSide();
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
