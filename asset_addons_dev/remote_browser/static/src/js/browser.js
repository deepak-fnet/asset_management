/** @odoo-module **/

import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { Component, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";

// DevTools key codes for keys that do not type text
const KEYS = {
    Enter: [13, "Enter", "\r"], Tab: [9, "Tab"], Backspace: [8, "Backspace"], Escape: [27, "Escape"],
    Delete: [46, "Delete"], Insert: [45, "Insert"], Home: [36, "Home"], End: [35, "End"],
    PageUp: [33, "PageUp"], PageDown: [34, "PageDown"], ArrowLeft: [37, "ArrowLeft"], ArrowUp: [38, "ArrowUp"],
    ArrowRight: [39, "ArrowRight"], ArrowDown: [40, "ArrowDown"], " ": [32, "Space", " "],
};
for (let i = 1; i <= 12; i++) {
    KEYS["F" + i] = [111 + i, "F" + i];
}

const BUTTONS = { 0: "left", 1: "middle", 2: "right" };
const BUTTON_BITS = { left: 1, right: 2, middle: 4 };

function modifiers(ev) {
    return (ev.altKey ? 1 : 0) | (ev.ctrlKey ? 2 : 0) | (ev.metaKey ? 4 : 0) | (ev.shiftKey ? 8 : 0);
}

function keyCodeOf(ev) {
    if (KEYS[ev.key]) {
        return KEYS[ev.key][0];
    }
    if (ev.key.length === 1) {
        const up = ev.key.toUpperCase();
        if (/[A-Z0-9]/.test(up)) {
            return up.charCodeAt(0);
        }
    }
    return ev.keyCode || 0;
}

function hostOf(url) {
    try {
        return new URL(url).host;
    } catch {
        return "";
    }
}

const PALETTE = ["#3b6fd8", "#0f9d8a", "#c2410c", "#7c3aed", "#be185d", "#0369a1", "#4d7c0f", "#b45309"];
function colorFor(text) {
    let h = 0;
    for (const c of text || "") {
        h = (h * 31 + c.charCodeAt(0)) >>> 0;
    }
    return PALETTE[h % PALETTE.length];
}

export class RemoteBrowser extends Component {
    static template = "remote_browser.App";
    static props = ["*"];

    setup() {
        this.notification = useService("notification");
        this.rootRef = useRef("root");
        this.viewRef = useRef("view");
        this.imgRef = useRef("img");
        this.kbdRef = useRef("kbd");
        this.omniRef = useRef("omni");
        this.startRef = useRef("start");

        this.state = useState({
            status: "starting", // starting | ready | error
            error: "",
            tabs: [],
            active: null,
            url: "",
            omni: "",
            editing: false,
            title: "",
            loading: false,
            canBack: false,
            canForward: false,
            server: "",
            patterns: [],
            allowAll: false,
            bookmarks: [],
            fullscreen: false,
            menu: false,
            tabMenu: null,
            downloads: [],
            showDownloads: false,
            newDownloads: 0,
            toast: "",
            suggest: [],
            suggestIndex: -1,
            dragId: null,
            latency: null,
            fps: 0,
            zooms: {},
            quality: (() => { try { return localStorage.getItem("remote_browser.quality") || "auto"; } catch { return "auto"; } })(),
            vw: 1280,
            vh: 800,
        });

        this.size = { width: 1280, height: 800 };
        this.lastHash = null;
        this.frameTab = null;
        this.queue = [];
        this.sending = false;
        this.running = true;
        this.delay = 0;
        this.knownDownloads = null;
        this.autoLevel = 1;
        this.goodFrames = 0;
        this.ewma = 0;

        this._onFullscreen = () => {
            this.state.fullscreen = document.fullscreenElement === this.rootRef.el;
            setTimeout(() => this.measure(true), 120);
        };
        this._onDocClick = (ev) => {
            if (!ev.target.closest(".rb-menu, .rb-menu-btn, .rb-tabmenu, .rb-dl-panel, .rb-dl-btn")) {
                this.state.menu = false;
                this.state.tabMenu = null;
                this.state.showDownloads = false;
            }
        };

        onMounted(() => {
            document.addEventListener("fullscreenchange", this._onFullscreen);
            document.addEventListener("click", this._onDocClick);
            this.resizeObserver = new ResizeObserver(() => this.measure());
            this.resizeObserver.observe(this.viewRef.el);
            this.start();
        });
        onWillUnmount(() => {
            this.running = false;
            this.restartStream();
            this.wake();
            this.resizeObserver?.disconnect();
            document.removeEventListener("fullscreenchange", this._onFullscreen);
            document.removeEventListener("click", this._onDocClick);
            clearInterval(this.dlTimer);
            if (document.fullscreenElement) {
                document.exitFullscreen().catch(() => {});
            }
        });
    }

    // ------------------------------------------------------------------ start
    async start() {
        this.state.status = "starting";
        this.state.error = "";
        this.readSize();
        try {
            const res = await rpc("/remote_browser/open", this.size);
            this.state.server = res.server;
            this.state.patterns = res.patterns;
            this.state.allowAll = res.allow_all;
            this.state.bookmarks = res.bookmarks;
            this.mergeTabs(res.tabs);
            this.state.active = res.tabs[0].id;
            this.state.status = "ready";
            this.frameLoop();
            this.stateLoop();
            this.dlTimer = setInterval(() => this.pollDownloads(), 4000);
            this.pollDownloads();
            if (this.isStart) {
                setTimeout(() => this.focusStartIfIdle(), 60);
            } else {
                this.focusView();
            }
        } catch (e) {
            this.state.status = "error";
            this.state.error = e.data?.message || e.message || "The browser did not start.";
        }
    }

    readSize() {
        const el = this.viewRef.el;
        if (el) {
            this.size = { width: Math.max(320, Math.floor(el.clientWidth)), height: Math.max(240, Math.floor(el.clientHeight)) };
            this.state.vw = this.size.width;
            this.state.vh = this.size.height;
        }
    }

    measure(force = false) {
        const before = `${this.size.width}x${this.size.height}`;
        this.readSize();
        if (!force && before === `${this.size.width}x${this.size.height}`) {
            return;
        }
        clearTimeout(this.resizeTimer);
        this.resizeTimer = setTimeout(async () => {
            if (this.state.active && this.state.status === "ready") {
                await rpc("/remote_browser/resize", { tid: this.state.active, ...this.size }, { silent: true }).catch(() => {});
                this.restartStream();
                this.kick();
            }
        }, 180);
    }

    // ------------------------------------------------------------------ screen
    sleep(ms) {
        return new Promise((resolve) => {
            this.wake = () => {
                clearTimeout(this.sleepTimer);
                resolve();
            };
            this.sleepTimer = setTimeout(resolve, ms);
        });
    }

    wake() {}

    kick() {
        this.kickState();
    }

    static ZOOMS = [0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];

    get zoom() {
        return this.state.zooms[this.state.active] || 1;
    }

    get zoomLabel() {
        return Math.round(this.zoom * 100) + "%";
    }

    async setZoom(value) {
        const tid = this.state.active;
        if (!tid) {
            return;
        }
        const z = await rpc("/remote_browser/zoom", { tid, zoom: value, ...this.size }, { silent: true }).catch(() => null);
        if (z !== null) {
            this.state.zooms = { ...this.state.zooms, [tid]: z };
            this.toast(`Zoom ${Math.round(z * 100)}%`);
        }
    }

    zoomStep(dir) {
        const steps = RemoteBrowser.ZOOMS;
        const cur = this.zoom;
        const next = dir > 0 ? steps.find((v) => v > cur + 0.001) : [...steps].reverse().find((v) => v < cur - 0.001);
        if (next) {
            this.setZoom(next);
        }
    }

    zoomIn() {
        this.zoomStep(1);
    }

    zoomOut() {
        this.zoomStep(-1);
    }

    zoomReset() {
        this.setZoom(1);
        this.focusView();
    }

    restartStream() {
        this.streamAbort?.abort();
    }

    async frameLoop() {
        // one streaming response per tab/size/mode; it ends every ~55 s and we simply open the next
        let failures = 0;
        while (this.running && this.state.status === "ready") {
            const tid = this.state.active;
            // only the pixels the screen really shows (a 50% zoomed browser shows half), at most 1920 wide
            const dpr = Math.min(1, window.devicePixelRatio || 1);
            const cap = Math.min(1, 1920 / (this.size.width * dpr));
            const w = Math.round(this.size.width * dpr * cap);
            const h = Math.round(this.size.height * dpr * cap);
            const ctl = new AbortController();
            this.streamAbort = ctl;
            try {
                const resp = await fetch(
                    `/remote_browser/stream?tid=${encodeURIComponent(tid)}&w=${w}&h=${h}&mode=${this.state.quality}`,
                    { credentials: "same-origin", cache: "no-store", signal: ctl.signal }
                );
                if (!resp.ok) {
                    throw new Error(`HTTP ${resp.status}`);
                }
                failures = 0;
                await this.readFrames(resp.body.getReader(), tid);
            } catch (e) {
                if (ctl.signal.aborted) {
                    continue; // tab, size or mode changed: open a new stream at once
                }
                failures++;
                if (failures > 4) {
                    this.state.status = "error";
                    this.state.error = "The connection to the browser was lost.";
                    return;
                }
                await this.sleep(500 * failures);
            }
        }
    }

    async readFrames(reader, tid) {
        let buf = new Uint8Array(0);
        while (true) {
            const { value, done } = await reader.read();
            if (done || tid !== this.state.active) {
                return;
            }
            const merged = new Uint8Array(buf.length + value.length);
            merged.set(buf);
            merged.set(value, buf.length);
            buf = merged;
            while (buf.length >= 4) {
                const n = ((buf[0] << 24) | (buf[1] << 16) | (buf[2] << 8) | buf[3]) >>> 0;
                if (buf.length < 4 + n) {
                    break;
                }
                if (n) {
                    this.draw(buf.slice(4, 4 + n));
                }
                buf = buf.subarray(4 + n);
            }
        }
    }

    async draw(jpeg) {
        // decode off the main thread; when frames come faster than we paint, only the newest is drawn
        this.pending = jpeg;
        if (this.drawing) {
            return;
        }
        this.drawing = true;
        try {
            while (this.pending) {
                const data = this.pending;
                this.pending = null;
                const bitmap = await createImageBitmap(new Blob([data], { type: "image/jpeg" }));
                const canvas = this.imgRef.el;
                if (canvas) {
                    if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
                        canvas.width = bitmap.width;
                        canvas.height = bitmap.height;
                    }
                    canvas.getContext("2d").drawImage(bitmap, 0, 0);
                }
                bitmap.close();
                this.countFrame();
            }
        } catch {
            // a broken frame: skip it
        } finally {
            this.drawing = false;
        }
    }

    countFrame() {
        const now = performance.now();
        this.frameTimes = (this.frameTimes || []).filter((t) => now - t < 1000);
        this.frameTimes.push(now);
        if (now - (this.fpsShown || 0) > 500) {
            this.fpsShown = now;
            this.state.fps = this.frameTimes.length;
        }
    }

    async stateLoop() {
        while (this.running && this.state.status === "ready") {
            await this.refreshState();
            await new Promise((r) => {
                this.wakeState = r;
                this.stateTimer = setTimeout(r, 800);
            });
        }
    }

    kickState() {
        clearTimeout(this.stateTimer);
        this.wakeState?.();
    }

    async refreshState() {
        const tid = this.state.active;
        if (!tid) {
            return;
        }
        try {
            const res = await rpc("/remote_browser/state", { tid }, { silent: true });
            this.mergeTabs(res.tabs);
            if (tid !== this.state.active || res.missing) {
                return;
            }
            this.state.url = res.url;
            this.state.title = res.title;
            this.state.loading = res.loading;
            this.state.canBack = res.can_back;
            this.state.canForward = res.can_forward;
            if (!this.isEditing()) {
                this.state.omni = res.url === "about:blank" ? "" : res.url;
            }
        } catch {
            // the frame loop reports lost connections
        }
    }

    setQuality(ev) {
        this.state.quality = ev.currentTarget.dataset.quality;
        this.state.menu = false;
        this.restartStream();
        try {
            localStorage.setItem("remote_browser.quality", this.state.quality);
        } catch {
            // private window
        }
        this.toast({ auto: "Picture: automatic", sharp: "Picture: sharp", fast: "Picture: fast" }[this.state.quality]);
    }

    focusStartIfIdle() {
        // never steal the focus from someone already typing (e.g. in the top address bar)
        const el = document.activeElement;
        if (!el || el === document.body || el === this.kbdRef.el) {
            this.startRef.el?.focus();
        }
    }

    isEditing() {
        const el = document.activeElement;
        return !!el && (el === this.omniRef.el || el === this.startRef.el);
    }

    mergeTabs(list) {
        const byId = Object.fromEntries(list.map((t) => [t.id, t]));
        const kept = this.state.tabs.filter((t) => byId[t.id]).map((t) => ({ ...t, ...byId[t.id] }));
        const known = new Set(kept.map((t) => t.id));
        for (const t of list) {
            if (!known.has(t.id)) {
                kept.push({ ...t });
            }
        }
        this.state.tabs = kept;
        if (this.state.active && !byId[this.state.active] && kept.length) {
            this.state.active = kept[kept.length - 1].id;
            this.restartStream();
        }
    }

    // ------------------------------------------------------------------ input
    send(ev) {
        if (ev.kind === "mouse" && ev.type === "mouseMoved") {
            const last = this.queue[this.queue.length - 1];
            if (last && last.kind === "mouse" && last.type === "mouseMoved") {
                this.queue[this.queue.length - 1] = ev; // only the newest position matters
                return this.flush();
            }
        }
        if (ev.kind === "mouse" && ev.type === "mouseWheel") {
            const last = this.queue[this.queue.length - 1];
            if (last && last.kind === "mouse" && last.type === "mouseWheel") {
                last.deltaX += ev.deltaX;
                last.deltaY += ev.deltaY;
                return this.flush();
            }
        }
        this.queue.push(ev);
        this.flush();
    }

    async flush() {
        if (this.sending) {
            return;
        }
        this.sending = true;
        try {
            while (this.queue.length && this.state.active) {
                const events = this.queue.splice(0);
                await rpc("/remote_browser/input", { tid: this.state.active, events }, { silent: true });
            }
        } catch {
            this.toast("Input not delivered");
        } finally {
            this.sending = false;
        }
    }

    point(ev) {
        const rect = this.imgRef.el.getBoundingClientRect();
        const sx = this.size.width / (rect.width || 1);
        const sy = this.size.height / (rect.height || 1);
        return {
            x: Math.round((ev.clientX - rect.left) * sx),
            y: Math.round((ev.clientY - rect.top) * sy),
        };
    }

    onMouseDown(ev) {
        ev.preventDefault();
        this.focusView();
        this.state.menu = false;
        this.state.tabMenu = null;
        const button = BUTTONS[ev.button] || "left";
        this.pressed = (this.pressed || 0) | BUTTON_BITS[button];
        this.send({ kind: "mouse", type: "mousePressed", ...this.point(ev), button, buttons: this.pressed,
                    clickCount: ev.detail || 1, modifiers: modifiers(ev) });
    }

    onMouseUp(ev) {
        const button = BUTTONS[ev.button] || "left";
        this.pressed = (this.pressed || 0) & ~BUTTON_BITS[button];
        this.send({ kind: "mouse", type: "mouseReleased", ...this.point(ev), button, buttons: this.pressed,
                    clickCount: ev.detail || 1, modifiers: modifiers(ev) });
    }

    onMouseMove(ev) {
        const now = performance.now();
        if (now - (this.lastMove || 0) < 40 && !this.pressed) {
            return;
        }
        this.lastMove = now;
        const button = this.pressed & 1 ? "left" : this.pressed & 2 ? "right" : this.pressed & 4 ? "middle" : "none";
        this.send({ kind: "mouse", type: "mouseMoved", ...this.point(ev), button, buttons: this.pressed || 0,
                    modifiers: modifiers(ev) });
    }

    onWheel(ev) {
        ev.preventDefault();
        if (ev.ctrlKey || ev.metaKey) {
            const now = performance.now();
            if (now - (this.lastZoomWheel || 0) > 180) {
                this.lastZoomWheel = now;
                this.zoomStep(ev.deltaY < 0 ? 1 : -1);
            }
            return;
        }
        const unit = ev.deltaMode === 1 ? 40 : ev.deltaMode === 2 ? this.size.height : 1;
        this.send({ kind: "mouse", type: "mouseWheel", ...this.point(ev), deltaX: ev.deltaX * unit,
                    deltaY: ev.deltaY * unit, modifiers: modifiers(ev) });
    }

    focusView() {
        this.state.editing = false;
        this.kbdRef.el?.focus({ preventScroll: true });
    }

    onKeyDown(ev) {
        if (ev.isComposing || ev.keyCode === 229) {
            return; // an input method is composing (Tamil, etc.): the text arrives on compositionend
        }
        if (this.localShortcut(ev)) {
            return;
        }
        const key = ev.key.toLowerCase();
        const mod = ev.ctrlKey || ev.metaKey;
        if (mod && (key === "v")) {
            return; // the browser fires "paste" next
        }
        if (mod && (key === "c" || key === "x")) {
            this.copyToLocal();
        }
        ev.preventDefault();
        const known = KEYS[ev.key];
        const printable = ev.key.length === 1 && !mod && !ev.altKey;
        const text = printable ? ev.key : known && known[2] && !mod && !ev.altKey ? known[2] : undefined;
        this.send({
            kind: "key", key: ev.key, code: ev.code || (known && known[1]) || "",
            windowsVirtualKeyCode: keyCodeOf(ev), modifiers: modifiers(ev), location: ev.location || 0, text,
        });
    }

    onCompositionEnd(ev) {
        if (ev.data) {
            this.send({ kind: "text", text: ev.data });
        }
        this.kbdRef.el.value = "";
    }

    onKbdInput(ev) {
        // text that came without a keydown we handled (input methods, dictation)
        if (!ev.isComposing && ev.target.value) {
            this.send({ kind: "text", text: ev.target.value });
            ev.target.value = "";
        }
    }

    onPaste(ev) {
        ev.preventDefault();
        const text = ev.clipboardData?.getData("text/plain");
        if (text) {
            this.send({ kind: "text", text });
            this.toast("Pasted");
        }
    }

    async copyToLocal() {
        try {
            await new Promise((r) => setTimeout(r, 60));
            const text = await rpc("/remote_browser/copy", { tid: this.state.active }, { silent: true });
            if (text) {
                await navigator.clipboard.writeText(text);
                this.toast("Copied");
            }
        } catch {
            this.toast("The browser blocked the clipboard");
        }
    }

    localShortcut(ev) {
        const key = ev.key.toLowerCase();
        const run = (fn) => {
            ev.preventDefault();
            ev.stopPropagation();
            fn();
            return true;
        };
        if ((ev.ctrlKey || ev.metaKey) && key === "l") {
            return run(() => this.focusOmni());
        }
        if ((ev.ctrlKey || ev.metaKey) && !ev.altKey && (key === "=" || key === "+")) {
            return run(() => this.zoomIn());
        }
        if ((ev.ctrlKey || ev.metaKey) && !ev.altKey && (key === "-" || key === "_")) {
            return run(() => this.zoomOut());
        }
        if ((ev.ctrlKey || ev.metaKey) && !ev.altKey && key === "0") {
            return run(() => this.zoomReset());
        }
        if (ev.key === "F5" || ((ev.ctrlKey || ev.metaKey) && key === "r")) {
            return run(() => this.reload());
        }
        if (ev.key === "F11") {
            return run(() => this.toggleFullscreen());
        }
        if (ev.altKey && !ev.ctrlKey) {
            if (ev.key === "ArrowLeft") {
                return run(() => this.back());
            }
            if (ev.key === "ArrowRight") {
                return run(() => this.forward());
            }
            if (key === "t") {
                return run(() => this.newTab());
            }
            if (key === "w") {
                return run(() => this.closeTab(this.state.active));
            }
            if (key === "d") {
                return run(() => this.duplicateTab(this.state.active));
            }
        }
        if (ev.ctrlKey && ev.key === "PageDown") {
            return run(() => this.cycleTab(1));
        }
        if (ev.ctrlKey && ev.key === "PageUp") {
            return run(() => this.cycleTab(-1));
        }
        return false;
    }

    onRootKeyDown(ev) {
        if (ev.target === this.kbdRef.el) {
            return;
        }
        this.localShortcut(ev);
    }

    // ------------------------------------------------------------------ navigation
    focusOmni() {
        this.state.editing = true;
        const el = this.isStart ? this.startRef.el : this.omniRef.el;
        el?.focus();
        el?.select();
    }

    onOmniFocus(ev) {
        this.state.editing = true;
        ev.target.select();
        this.updateSuggest(ev.target.value);
    }

    onOmniBlur() {
        setTimeout(() => {
            if (this.isEditing()) {
                return; // moved from one address box to the other: keep what was typed
            }
            this.state.editing = false;
            this.state.suggest = [];
            this.state.omni = this.state.url === "about:blank" ? "" : this.state.url;
        }, 150);
    }

    onOmniInput(ev) {
        this.state.omni = ev.target.value;
        this.updateSuggest(ev.target.value);
    }

    updateSuggest(text) {
        const q = (text || "").toLowerCase().trim();
        const pool = this.state.bookmarks;
        this.state.suggest = q
            ? pool.filter((b) => b.name.toLowerCase().includes(q) || b.url.toLowerCase().includes(q)).slice(0, 6)
            : [];
        this.state.suggestIndex = -1;
    }

    onOmniKeyDown(ev) {
        if (this.localShortcut(ev)) {
            return;
        }
        const list = this.state.suggest;
        if (ev.key === "ArrowDown" && list.length) {
            ev.preventDefault();
            this.state.suggestIndex = (this.state.suggestIndex + 1) % list.length;
        } else if (ev.key === "ArrowUp" && list.length) {
            ev.preventDefault();
            this.state.suggestIndex = (this.state.suggestIndex - 1 + list.length) % list.length;
        } else if (ev.key === "Enter") {
            ev.preventDefault();
            const pick = list[this.state.suggestIndex];
            this.go(pick ? pick.url : ev.target.value);
        } else if (ev.key === "Escape") {
            ev.target.blur();
            this.focusView();
        }
    }

    pickSuggest(ev) {
        this.go(ev.currentTarget.dataset.url);
    }

    async go(text) {
        if (!text || !text.trim()) {
            return;
        }
        this.state.suggest = [];
        this.state.editing = false;
        const res = await rpc("/remote_browser/navigate", { tid: this.state.active, url: text });
        if (res.error) {
            this.notification.add(res.error, { type: "warning", title: "Not allowed" });
            return;
        }
        this.state.omni = res.url;
        this.state.url = res.url;
        this.state.loading = true;
        this.focusView();
        this.kick();
    }

    openBookmark(ev) {
        this.go(ev.currentTarget.dataset.url);
    }

    async nav(action) {
        await rpc("/remote_browser/nav", { tid: this.state.active, action }, { silent: true }).catch(() => {});
        this.kick();
        this.focusView();
    }

    back() {
        this.nav("back");
    }

    forward() {
        this.nav("forward");
    }

    reload() {
        this.nav(this.state.loading ? "stop" : "reload");
    }

    // ------------------------------------------------------------------ tabs
    async tabAction(action, tid) {
        const res = await rpc("/remote_browser/tab", { action, tid, ...this.size }, { silent: true });
        this.mergeTabs(res.tabs);
        return res;
    }

    async activate(tid) {
        if (!tid || tid === this.state.active) {
            return;
        }
        this.state.active = tid;
        this.restartStream();
        const tab = this.state.tabs.find((t) => t.id === tid);
        this.state.url = tab?.url || "";
        this.state.omni = tab && tab.url !== "about:blank" ? tab.url : "";
        await this.tabAction("activate", tid);
        if (this.state.zooms[tid] && this.state.zooms[tid] !== 1) {
            this.setZoom(this.state.zooms[tid]); // the zoom stays with its tab
        }
        this.kick();
        this.focusView();
    }

    onTabClick(ev) {
        this.activate(ev.currentTarget.dataset.id);
    }

    onTabAux(ev) {
        if (ev.button === 1) {
            ev.preventDefault();
            this.closeTab(ev.currentTarget.dataset.id);
        }
    }

    onTabClose(ev) {
        ev.stopPropagation();
        this.closeTab(ev.currentTarget.closest("[data-id]").dataset.id);
    }

    onTabContext(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        const rootRect = this.rootRef.el.getBoundingClientRect();
        this.state.tabMenu = { id: ev.currentTarget.dataset.id, x: ev.clientX - rootRect.left, y: ev.clientY - rootRect.top };
    }

    async newTab() {
        const res = await this.tabAction("new");
        this.state.active = res.tab.id;
        this.restartStream();
        this.state.url = "about:blank";
        this.state.omni = "";
        this.kick();
        setTimeout(() => this.focusStartIfIdle(), 80);
    }

    async duplicateTab(tid) {
        this.state.tabMenu = null;
        this.state.menu = false;
        const res = await this.tabAction("duplicate", tid || this.state.active);
        // the copy sits right after its source, like Chrome
        const tabs = this.state.tabs.filter((t) => t.id !== res.tab.id);
        const at = tabs.findIndex((t) => t.id === (tid || this.state.active));
        const copy = this.state.tabs.find((t) => t.id === res.tab.id) || res.tab;
        tabs.splice(at + 1, 0, copy);
        this.state.tabs = tabs;
        this.state.active = res.tab.id;
        this.restartStream();
        this.kick();
        this.toast("Tab duplicated");
    }

    async closeTab(tid) {
        this.state.tabMenu = null;
        const index = this.state.tabs.findIndex((t) => t.id === tid);
        const res = await this.tabAction("close", tid);
        if (tid === this.state.active || res.tab) {
            const next = res.tab?.id || (this.state.tabs[Math.min(index, this.state.tabs.length - 1)] || {}).id;
            this.state.active = null;
            await this.activate(next);
        }
    }

    async closeOthers(tid) {
        this.state.tabMenu = null;
        for (const t of this.state.tabs.filter((x) => x.id !== tid)) {
            await this.tabAction("close", t.id);
        }
        await this.activate(tid);
    }

    cycleTab(step) {
        const tabs = this.state.tabs;
        const i = tabs.findIndex((t) => t.id === this.state.active);
        this.activate(tabs[(i + step + tabs.length) % tabs.length].id);
    }

    tabMenuAction(ev) {
        const action = ev.currentTarget.dataset.action;
        const tid = this.state.tabMenu.id;
        this.state.tabMenu = null;
        if (action === "duplicate") {
            this.duplicateTab(tid);
        } else if (action === "reload") {
            this.activate(tid).then(() => this.nav("reload"));
        } else if (action === "copy") {
            const tab = this.state.tabs.find((t) => t.id === tid);
            navigator.clipboard?.writeText(tab?.url || "").then(() => this.toast("Address copied")).catch(() => {});
        } else if (action === "close") {
            this.closeTab(tid);
        } else if (action === "others") {
            this.closeOthers(tid);
        }
    }

    // drag tabs to reorder
    onTabDragStart(ev) {
        this.state.dragId = ev.currentTarget.dataset.id;
        ev.dataTransfer.effectAllowed = "move";
    }

    onTabDragOver(ev) {
        ev.preventDefault();
        const over = ev.currentTarget.dataset.id;
        const from = this.state.dragId;
        if (!from || over === from) {
            return;
        }
        const tabs = [...this.state.tabs];
        const a = tabs.findIndex((t) => t.id === from);
        const b = tabs.findIndex((t) => t.id === over);
        const [moved] = tabs.splice(a, 1);
        tabs.splice(b, 0, moved);
        this.state.tabs = tabs;
    }

    onTabDragEnd() {
        this.state.dragId = null;
    }

    // ------------------------------------------------------------------ toolbar
    toggleMenu() {
        this.state.menu = !this.state.menu;
        this.state.showDownloads = false;
    }

    menuAction(ev) {
        const action = ev.currentTarget.dataset.action;
        this.state.menu = false;
        if (action === "new") {
            this.newTab();
        } else if (action === "duplicate") {
            this.duplicateTab(this.state.active);
        } else if (action === "copy") {
            navigator.clipboard?.writeText(this.state.url).then(() => this.toast("Address copied")).catch(() => {});
        } else if (action === "downloads") {
            this.toggleDownloads();
        } else if (action === "fullscreen") {
            this.toggleFullscreen();
        } else if (action === "close") {
            this.closeSession();
        }
    }

    async toggleFullscreen() {
        const el = this.rootRef.el;
        try {
            if (document.fullscreenElement) {
                await document.exitFullscreen();
            } else {
                await el.requestFullscreen({ navigationUI: "hide" });
                this.toast("Full screen · F11 or Esc to leave");
            }
        } catch {
            // no Fullscreen API: fill the window instead
            this.state.fullscreen = !this.state.fullscreen;
            setTimeout(() => this.measure(true), 120);
        }
        this.focusView();
    }

    get currentMark() {
        return this.state.bookmarks.find((b) => b.url === this.state.url);
    }

    async toggleBookmark() {
        const mark = this.currentMark;
        if (mark && !mark.mine) {
            return this.toast("Shared bookmarks are kept by the administrator");
        }
        const params = mark ? { url: this.state.url, remove_id: mark.id } : { url: this.state.url, name: this.state.title || hostOf(this.state.url) };
        this.state.bookmarks = await rpc("/remote_browser/bookmark", params);
        this.toast(mark ? "Bookmark removed" : "Bookmarked");
    }

    async pollDownloads() {
        const list = await rpc("/remote_browser/downloads", {}, { silent: true }).catch(() => null);
        if (!list) {
            return;
        }
        const done = list.filter((d) => d.done).map((d) => d.name);
        if (this.knownDownloads) {
            const fresh = done.filter((n) => !this.knownDownloads.includes(n));
            if (fresh.length) {
                this.state.newDownloads += fresh.length;
                this.toast(`Downloaded ${fresh[0]}`);
            }
        }
        this.knownDownloads = done;
        this.state.downloads = list;
    }

    toggleDownloads() {
        this.state.showDownloads = !this.state.showDownloads;
        this.state.menu = false;
        this.state.newDownloads = 0;
        if (this.state.showDownloads) {
            this.pollDownloads();
        }
    }

    async saveDownload(ev) {
        const name = ev.currentTarget.dataset.name;
        const resp = await fetch(`/remote_browser/download?name=${encodeURIComponent(name)}`, { credentials: "same-origin" });
        if (!resp.ok) {
            return this.toast("Download failed");
        }
        const url = URL.createObjectURL(await resp.blob());
        const a = Object.assign(document.createElement("a"), { href: url, download: name });
        document.body.appendChild(a);
        a.click();
        setTimeout(() => {
            a.remove();
            URL.revokeObjectURL(url);
        }, 1500);
    }

    async closeSession() {
        this.running = false;
        clearInterval(this.dlTimer);
        await rpc("/remote_browser/close", {}).catch(() => {});
        this.state.status = "error";
        this.state.error = "The browser session was closed. Your tabs are gone.";
        this.state.tabs = [];
    }

    retry() {
        this.running = true;
        this.start();
    }

    toast(text) {
        this.state.toast = text;
        clearTimeout(this.toastTimer);
        this.toastTimer = setTimeout(() => (this.state.toast = ""), 1800);
    }

    // ------------------------------------------------------------------ view helpers
    get isStart() {
        const tab = this.state.tabs.find((t) => t.id === this.state.active);
        const url = this.state.url || tab?.url || "";
        return !url || url === "about:blank" || url.startsWith("chrome://newtab");
    }

    get isSecure() {
        return this.state.url.startsWith("https://");
    }

    get omniHost() {
        return hostOf(this.state.url);
    }

    tabLabel(t) {
        if (!t.url || t.url === "about:blank") {
            return "New tab";
        }
        return t.title || hostOf(t.url) || t.url;
    }

    tabLetter(t) {
        const h = hostOf(t.url);
        return h ? h.replace(/^www\./, "")[0].toUpperCase() : "+";
    }

    tabColor(t) {
        return colorFor(hostOf(t.url));
    }

    markLetter(b) {
        return (b.name || "?").trim()[0].toUpperCase();
    }

    humanSize(n) {
        if (!n) {
            return "0 B";
        }
        const u = ["B", "KB", "MB", "GB"];
        const i = Math.min(3, Math.floor(Math.log(n) / Math.log(1024)));
        return (n / 1024 ** i).toFixed(i ? 1 : 0) + " " + u[i];
    }

    get greeting() {
        const h = new Date().getHours();
        return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
    }
}

registry.category("actions").add("remote_browser.app", RemoteBrowser);
