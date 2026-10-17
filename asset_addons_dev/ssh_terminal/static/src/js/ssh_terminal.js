/** @odoo-module **/
/* global Terminal */

import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { Component, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";

const THEMES = {
    midnight: {
        label: "Midnight",
        background: "#0b1020", foreground: "#d6deeb", cursor: "#7fdbca", cursorAccent: "#0b1020",
        selectionBackground: "#2d3a5c",
        black: "#1d2433", red: "#ef5350", green: "#22da6e", yellow: "#ffcb6b", blue: "#82aaff",
        magenta: "#c792ea", cyan: "#7fdbca", white: "#d6deeb",
        brightBlack: "#637777", brightRed: "#ff6e6e", brightGreen: "#5af78e", brightYellow: "#f4f99d",
        brightBlue: "#9cc4ff", brightMagenta: "#e2b3ff", brightCyan: "#a3f7e8", brightWhite: "#ffffff",
    },
    dracula: {
        label: "Dracula",
        background: "#1e1f29", foreground: "#f8f8f2", cursor: "#ff79c6", cursorAccent: "#1e1f29",
        selectionBackground: "#44475a",
        black: "#21222c", red: "#ff5555", green: "#50fa7b", yellow: "#f1fa8c", blue: "#bd93f9",
        magenta: "#ff79c6", cyan: "#8be9fd", white: "#f8f8f2",
        brightBlack: "#6272a4", brightRed: "#ff6e6e", brightGreen: "#69ff94", brightYellow: "#ffffa5",
        brightBlue: "#d6acff", brightMagenta: "#ff92df", brightCyan: "#a4ffff", brightWhite: "#ffffff",
    },
    matrix: {
        label: "Matrix",
        background: "#020a04", foreground: "#33ff66", cursor: "#33ff66", cursorAccent: "#020a04",
        selectionBackground: "#0f4a1e",
        black: "#021a08", red: "#ff4d4d", green: "#33ff66", yellow: "#b6ff4d", blue: "#2ee6a6",
        magenta: "#7dff9e", cyan: "#4dffb8", white: "#c8ffd6",
        brightBlack: "#1f7a3a", brightRed: "#ff7a7a", brightGreen: "#66ff8c", brightYellow: "#d4ff80",
        brightBlue: "#66ffc2", brightMagenta: "#a6ffbd", brightCyan: "#80ffd0", brightWhite: "#eafff0",
    },
    paper: {
        label: "Paper",
        background: "#fbfaf7", foreground: "#2b2b2b", cursor: "#2563eb", cursorAccent: "#fbfaf7",
        selectionBackground: "#cfe0ff",
        black: "#2b2b2b", red: "#c62828", green: "#2e7d32", yellow: "#a46900", blue: "#1e56c8",
        magenta: "#8e24aa", cyan: "#00838f", white: "#9e9e9e",
        brightBlack: "#616161", brightRed: "#e53935", brightGreen: "#43a047", brightYellow: "#c98a00",
        brightBlue: "#2f6fe4", brightMagenta: "#ab47bc", brightCyan: "#0097a7", brightWhite: "#424242",
    },
};

const PREFS_KEY = "ssh_terminal.prefs";

function loadPrefs() {
    try {
        return JSON.parse(localStorage.getItem(PREFS_KEY)) || {};
    } catch {
        return {};
    }
}

function savePrefs(prefs) {
    try {
        localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
    } catch {
        // private window: preferences are simply not kept
    }
}

export class SecureTerminalWidget extends Component {
    static template = "ssh_terminal.SecureTerminalWidget";
    static props = ["*"];

    setup() {
        this.notification = useService("notification");
        this.actionService = useService("action");
        this.terminalRef = useRef("terminal");
        this.rootRef = useRef("root");

        const prefs = loadPrefs();
        this.state = useState({
            status: "connecting", // connecting | live | closed | error
            serverName: "",
            error: "",
            latency: null,
            elapsed: "00:00",
            theme: THEMES[prefs.theme] ? prefs.theme : "midnight",
            fontSize: prefs.fontSize || 14,
            fullscreen: false,
            showThemes: false,
            size: "",
            toast: "",
        });
        this.themes = Object.entries(THEMES).map(([key, t]) => ({ key, label: t.label, bg: t.background, fg: t.cursor }));

        const ctx = this.props.action?.context || {};
        this.accessRequestId = ctx.access_request_id;
        this.accessToken = ctx.access_token;

        this.sessionId = null;
        this.term = null;
        // keystrokes and pastes wait here and leave one request at a time, in order
        this.outbox = "";
        this.sending = false;
        this.reading = false;

        this._onResize = () => this.fit();
        this._onBeforeUnload = () => this.closeBeacon();

        onMounted(() => {
            this.createTerminal();
            window.addEventListener("resize", this._onResize);
            window.addEventListener("beforeunload", this._onBeforeUnload);
            this.resizeObserver = new ResizeObserver(() => this.fit());
            this.resizeObserver.observe(this.terminalRef.el);
            if (!this.accessRequestId || !this.accessToken) {
                this.state.status = "error";
                this.state.error = "Open the terminal from an approved access request.";
                return;
            }
            this.connect();
        });

        onWillUnmount(() => {
            window.removeEventListener("resize", this._onResize);
            window.removeEventListener("beforeunload", this._onBeforeUnload);
            this.resizeObserver?.disconnect();
            clearInterval(this.clock);
            this.disconnect();
            this.term?.dispose();
        });
    }

    // ------------------------------------------------------------------ terminal

    createTerminal() {
        this.term = new Terminal({
            cursorBlink: true,
            cursorStyle: "bar",
            fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', Menlo, Consolas, 'DejaVu Sans Mono', monospace",
            fontSize: this.state.fontSize,
            lineHeight: 1.15,
            scrollback: 10000,
            allowProposedApi: true,
            macOptionIsMeta: true,
            rightClickSelectsWord: false,
            theme: THEMES[this.state.theme],
        });
        this.term.open(this.terminalRef.el);
        this.term.onData((data) => this.queue(data));
        this.term.onSelectionChange(() => {
            // copy on select, like PuTTY
            const text = this.term.getSelection();
            if (text) {
                navigator.clipboard?.writeText(text).catch(() => {});
            }
        });
        this.term.attachCustomKeyEventHandler((ev) => this.onKey(ev));
        this.terminalRef.el.addEventListener("contextmenu", (ev) => {
            ev.preventDefault();
            this.pasteFromClipboard();
        });
        this.term.writeln("\x1b[38;5;117m  Connecting securely…\x1b[0m");
        this.fit();
        this.term.focus();
    }

    onKey(ev) {
        if (ev.type !== "keydown") {
            return true;
        }
        const key = ev.key.toLowerCase();
        // Ctrl+Shift+C / Ctrl+Insert: copy the selection (Ctrl+C stays the interrupt)
        if ((ev.ctrlKey && ev.shiftKey && key === "c") || (ev.ctrlKey && key === "insert")) {
            this.copySelection();
            ev.preventDefault();
            return false;
        }
        // Ctrl+Shift+V / Shift+Insert: paste; plain Ctrl+V goes through the browser paste event
        if ((ev.ctrlKey && ev.shiftKey && key === "v") || (ev.shiftKey && key === "insert")) {
            this.pasteFromClipboard();
            ev.preventDefault();
            return false;
        }
        if (ev.ctrlKey && !ev.shiftKey && key === "v") {
            return false; // let the browser fire "paste"; xterm turns it into one onData call
        }
        if (ev.ctrlKey && (key === "=" || key === "+")) {
            this.zoom(1);
            ev.preventDefault();
            return false;
        }
        if (ev.ctrlKey && key === "-") {
            this.zoom(-1);
            ev.preventDefault();
            return false;
        }
        return true;
    }

    fit() {
        const term = this.term;
        const el = this.terminalRef.el;
        if (!term || !el) {
            return;
        }
        const cell = term._core?._renderService?.dimensions?.css?.cell;
        if (!cell || !cell.width || !cell.height) {
            return;
        }
        const style = getComputedStyle(el);
        const width = el.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - 12;
        const height = el.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
        const cols = Math.max(20, Math.floor(width / cell.width));
        const rows = Math.max(5, Math.floor(height / cell.height));
        if (cols !== term.cols || rows !== term.rows) {
            term.resize(cols, rows);
            this.state.size = `${cols}×${rows}`;
            clearTimeout(this.resizeTimer);
            this.resizeTimer = setTimeout(() => this.sendResize(), 150);
        }
    }

    async sendResize() {
        if (this.sessionId && this.state.status === "live") {
            await rpc("/ssh_terminal/secure_resize", {
                session_id: this.sessionId, cols: this.term.cols, rows: this.term.rows,
            }, { silent: true }).catch(() => {});
        }
    }

    // ------------------------------------------------------------------ connection

    async connect() {
        this.state.status = "connecting";
        this.state.error = "";
        try {
            const res = await rpc("/ssh_terminal/secure_connect", {
                access_request_id: this.accessRequestId,
                access_token: this.accessToken,
            });
            if (!res.success) {
                return this.fail(res.error);
            }
            this.sessionId = res.session_id;
            this.state.serverName = res.server_name;
            this.state.status = "live";
            this.term.reset();
            if (res.output) {
                this.term.write(res.output);
            }
            this.startedAt = Date.now();
            clearInterval(this.clock);
            this.clock = setInterval(() => this.tick(), 1000);
            this.fit();
            this.sendResize();
            this.term.focus();
            this.readLoop();
        } catch (e) {
            this.fail(e.message || "Connection failed");
        }
    }

    async readLoop() {
        if (this.reading) {
            return;
        }
        this.reading = true;
        let failures = 0;
        while (this.sessionId && this.state.status === "live") {
            try {
                const res = await rpc("/ssh_terminal/secure_read", {
                    session_id: this.sessionId, wait: 0.5,
                }, { silent: true });
                if (!res.success) {
                    if (res.disconnect) {
                        this.fail(res.error, true);
                        break;
                    }
                } else if (res.output) {
                    this.term.write(res.output);
                }
                failures = 0;
            } catch {
                failures++;
                if (failures > 5) {
                    this.fail("Lost contact with the server", true);
                    break;
                }
                await new Promise((r) => setTimeout(r, 400 * failures));
            }
        }
        this.reading = false;
    }

    queue(data) {
        if (this.state.status !== "live") {
            if (this.state.status === "closed" && data === "\r") {
                this.connect(); // Enter reconnects
            }
            return;
        }
        this.outbox += data;
        this.flush();
    }

    async flush() {
        if (this.sending) {
            return; // the running flush picks the new keys up
        }
        this.sending = true;
        try {
            while (this.outbox && this.sessionId) {
                const data = this.outbox;
                this.outbox = "";
                const started = performance.now();
                const res = await rpc("/ssh_terminal/secure_send", {
                    session_id: this.sessionId,
                    data,
                    is_enter: data.includes("\r"),
                }, { silent: true });
                this.state.latency = Math.round(performance.now() - started);
                if (!res.success && res.disconnect) {
                    this.fail(res.error, true);
                    break;
                }
            }
        } catch {
            this.toast("Keys not delivered, try again");
        } finally {
            this.sending = false;
        }
    }

    fail(message, wasLive = false) {
        this.state.status = wasLive ? "closed" : "error";
        this.state.error = message || "Disconnected";
        clearInterval(this.clock);
        this.sessionId = null;
        if (this.term) {
            this.term.write(`\r\n\x1b[38;5;203m● ${this.state.error}\x1b[0m\r\n`);
            if (wasLive) {
                this.term.write("\x1b[38;5;245m  Press Enter or Reconnect to start a new session.\x1b[0m\r\n");
            }
        }
    }

    async disconnect() {
        const sid = this.sessionId;
        if (!sid) {
            return;
        }
        this.sessionId = null;
        this.state.status = "closed";
        clearInterval(this.clock);
        await rpc("/ssh_terminal/secure_close", { session_id: sid }, { silent: true }).catch(() => {});
    }

    closeBeacon() {
        if (this.sessionId && navigator.sendBeacon) {
            const body = JSON.stringify({
                jsonrpc: "2.0", method: "call", id: 1, params: { session_id: this.sessionId },
            });
            navigator.sendBeacon("/ssh_terminal/secure_close", new Blob([body], { type: "application/json" }));
        }
    }

    // ------------------------------------------------------------------ toolbar

    tick() {
        const s = Math.floor((Date.now() - this.startedAt) / 1000);
        const h = Math.floor(s / 3600);
        const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
        const ss = String(s % 60).padStart(2, "0");
        this.state.elapsed = h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
    }

    toast(text) {
        this.state.toast = text;
        clearTimeout(this.toastTimer);
        this.toastTimer = setTimeout(() => (this.state.toast = ""), 1600);
    }

    async copySelection() {
        const text = this.term.getSelection();
        if (!text) {
            return this.toast("Select text first");
        }
        try {
            await navigator.clipboard.writeText(text);
            this.toast("Copied");
        } catch {
            this.toast("Clipboard blocked by the browser");
        }
        this.term.focus();
    }

    async pasteFromClipboard() {
        try {
            const text = await navigator.clipboard.readText();
            if (text) {
                // xterm wraps it in bracketed-paste marks when the shell asked for them
                this.term.paste(text);
            }
        } catch {
            this.toast("Use Ctrl+V to paste here");
        }
        this.term.focus();
    }

    zoom(step) {
        this.state.fontSize = Math.min(28, Math.max(9, this.state.fontSize + step));
        this.term.options.fontSize = this.state.fontSize;
        this.savePrefs();
        setTimeout(() => this.fit(), 30);
        this.toast(`${this.state.fontSize}px`);
    }

    zoomIn() {
        this.zoom(1);
    }

    zoomOut() {
        this.zoom(-1);
    }

    toggleThemes() {
        this.state.showThemes = !this.state.showThemes;
    }

    setTheme(ev) {
        const key = ev.currentTarget.dataset.theme;
        this.state.theme = key;
        this.state.showThemes = false;
        this.term.options.theme = THEMES[key];
        this.savePrefs();
        this.term.focus();
    }

    savePrefs() {
        savePrefs({ theme: this.state.theme, fontSize: this.state.fontSize });
    }

    clearScreen() {
        this.term.clear();
        this.term.focus();
    }

    toggleFullscreen() {
        this.state.fullscreen = !this.state.fullscreen;
        setTimeout(() => {
            this.fit();
            this.term.focus();
        }, 60);
    }

    openFiles() {
        this.actionService.doAction({
            type: "ir.actions.client",
            tag: "ssh_sftp_widget",
            name: `Files · ${this.state.serverName}`,
            context: { access_request_id: this.accessRequestId, access_token: this.accessToken },
        });
    }

    onReconnect() {
        this.term.reset();
        this.connect();
    }

    async onDisconnect() {
        await this.disconnect();
        this.term.write("\r\n\x1b[38;5;245m● Session closed. Press Enter to reconnect.\x1b[0m\r\n");
    }

    focusTerm() {
        this.state.showThemes = false;
        this.term?.focus();
    }

    get statusLabel() {
        return { connecting: "Connecting", live: "Live", closed: "Closed", error: "Not connected" }[this.state.status];
    }

    get latencyClass() {
        const l = this.state.latency;
        return l === null ? "" : l < 120 ? "good" : l < 350 ? "ok" : "slow";
    }
}

registry.category("actions").add("ssh_terminal_widget", SecureTerminalWidget);
