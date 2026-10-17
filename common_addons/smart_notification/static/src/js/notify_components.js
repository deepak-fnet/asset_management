/** @odoo-module **/
import { Component, markup, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

function timeAgo(s) {
    if (!s) return "";
    const d = new Date(s.replace(" ", "T") + "Z");
    const sec = Math.max(1, Math.round((Date.now() - d) / 1000));
    if (sec < 60) return "just now";
    if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
    if (sec < 86400) return `${Math.round(sec / 3600)} h ago`;
    if (sec < 7 * 86400) return `${Math.round(sec / 86400)} d ago`;
    return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

const helpers = {
    timeAgo,
    avatar: (id) => `/web/image/res.users/${id}/avatar_128`,
    html: (s) => markup(s || ""),
    status(item) {
        // one line in plain words: where this notification stands for me
        const n = item.n;
        if (item.state === "closed") return { cls: "closed", icon: "fa-check-circle-o", text: item.closed_reason || n.close_reason || "Closed" };
        if (item.state === "done") {
            return n.policy === "info" ? { cls: "read", icon: "fa-check", text: "Read" }
                : { cls: "done", icon: "fa-check-circle", text: "Done by you" };
        }
        if (n.policy === "info") return { cls: "info", icon: "fa-circle", text: "New" };
        if (n.policy === "all") {
            const done = n.people.filter((p) => p.state === "done").length;
            return { cls: "wait", icon: "fa-hourglass-half", text: `Waiting for you · ${done} of ${n.people.length} done` };
        }
        return { cls: "wait", icon: "fa-hourglass-half", text: n.people.length > 1 ? `Waiting · any one of ${n.people.length}` : "Waiting for you" };
    },
    canAct: (item) => item.n.action_method && item.n.state === "open" && ["new", "seen"].includes(item.state),
    pending: (item) => item.n.state === "open" && ["new", "seen"].includes(item.state),
    fmt(s) {
        if (!s) return "";
        return new Date(s.replace(" ", "T") + "Z").toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
    },
    personState(p) {
        if (p.state === "done") return p.outcome === "rejected" ? { cls: "rej", text: "Rejected" } : { cls: "done", text: p.outcome === "done" ? "Marked done" : "Done" };
        if (p.state === "closed") return { cls: "closed", text: "Closed" };
        if (p.state === "seen") return { cls: "seen", text: "Seen, waiting" };
        return { cls: "new", text: "Not seen yet" };
    },
    kindLabel: (k) => ({ approval: "Approval", info: "Information", success: "Good news", warning: "Warning", danger: "Needs attention",
                         access: "Access", alert: "Alert", reminder: "Reminder" }[k] || "Notification"),
};

// ---------------------------------------------------------------------- systray bell
export class SmartNotificationSystray extends Component {
    static template = "smart_notification.Systray";
    static props = {};
    setup() {
        this.sn = useService("smart_notification");
        this.state = useState(this.sn.state);
    }
}
registry.category("systray").add("smart_notification.systray", { Component: SmartNotificationSystray }, { sequence: 26 });

// ---------------------------------------------------------------------- one card
export class SmartNotificationCard extends Component {
    static template = "smart_notification.Card";
    static props = { item: Object, compact: { type: Boolean, optional: true } };
    setup() {
        this.sn = useService("smart_notification");
        this.state = useState(this.sn.state);
        this.h = helpers;
    }
    toggleBody(ev) {
        ev.stopPropagation();
        this.state.expanded[this.props.item.id] = !this.state.expanded[this.props.item.id];
    }
}

// ---------------------------------------------------------------------- bubble, panel and toasts (always on top)
export class SmartNotificationLayer extends Component {
    static template = "smart_notification.Layer";
    static props = {};
    static components = { SmartNotificationCard };
    setup() {
        this.sn = useService("smart_notification");
        this.state = useState(this.sn.state);
        this.bubbleRef = useRef("bubble");
        this.h = helpers;
        this.drag = null;
        this._onKey = (ev) => ev.key === "Escape" && this.state.open && this.sn.close();
        this._onResize = () => this.clampBubble();
        onMounted(() => {
            window.addEventListener("keydown", this._onKey);
            window.addEventListener("resize", this._onResize);
            this.clampBubble();
        });
        onWillUnmount(() => {
            window.removeEventListener("keydown", this._onKey);
            window.removeEventListener("resize", this._onResize);
        });
    }
    get bubbleStyle() {
        const b = this.state.bubble;
        const x = b.x ?? window.innerWidth - 76;
        const y = b.y ?? window.innerHeight - 140;
        return `left: ${x}px; top: ${y}px;`;
    }
    clampBubble() {
        const b = this.state.bubble;
        if (b.x === null) return;
        b.x = Math.max(8, Math.min(window.innerWidth - 64, b.x));
        b.y = Math.max(56, Math.min(window.innerHeight - 64, b.y));
    }
    onBubbleDown(ev) {
        const el = this.bubbleRef.el;
        const r = el.getBoundingClientRect();
        this.drag = { dx: ev.clientX - r.left, dy: ev.clientY - r.top, sx: ev.clientX, sy: ev.clientY, moved: false };
        el.setPointerCapture(ev.pointerId);
    }
    onBubbleMove(ev) {
        if (!this.drag) return;
        if (Math.abs(ev.clientX - this.drag.sx) + Math.abs(ev.clientY - this.drag.sy) > 5) this.drag.moved = true;
        if (!this.drag.moved) return;
        this.state.bubble.x = ev.clientX - this.drag.dx;
        this.state.bubble.y = ev.clientY - this.drag.dy;
        this.state.bubble.dragging = true;
        this.clampBubble();
    }
    onBubbleUp() {
        if (!this.drag) return;
        const moved = this.drag.moved;
        this.drag = null;
        this.state.bubble.dragging = false;
        if (!moved) {
            this.sn.toggle("bubble");
            return;
        }
        // snap to the nearest side, like a chat head
        const b = this.state.bubble;
        b.x = b.x + 28 < window.innerWidth / 2 ? 12 : window.innerWidth - 68;
        this.sn.saveBubble();
    }
    get panelStyle() {
        if (this.state.full) return "";
        if (this.state.anchor !== "bubble") return "top: 52px; right: 12px;";
        const b = this.state.bubble;
        const x = b.x ?? window.innerWidth - 76;
        const y = b.y ?? window.innerHeight - 140;
        const w = Math.min(520, window.innerWidth - 24);
        const left = x + 28 < window.innerWidth / 2 ? Math.min(x + 66, window.innerWidth - w - 12) : Math.max(12, x - w - 10);
        const top = Math.max(56, Math.min(window.innerHeight - 680, y - 240));
        return `top: ${top}px; left: ${left}px;`;
    }
    get tabs() {
        const c = this.state.counts;
        return [
            { key: "todo", label: "To do", count: c.todo, hot: true },
            { key: "unread", label: "Unread", count: c.unread },
            { key: "all", label: "All" },
            { key: "done", label: "Done" },
        ];
    }
    get viewItem() {
        return this.state.items.find((x) => x.id === this.state.viewId) || null;
    }
    get groups() {
        // newest first, grouped by day
        const out = [];
        const today = new Date().toDateString();
        const yday = new Date(Date.now() - 864e5).toDateString();
        for (const it of this.state.items) {
            const d = new Date(it.n.date.replace(" ", "T") + "Z");
            const k = d.toDateString();
            const label = k === today ? "Today" : k === yday ? "Yesterday" : d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
            if (!out.length || out[out.length - 1].label !== label) out.push({ label, items: [] });
            out[out.length - 1].items.push(it);
        }
        return out;
    }
}
registry.category("main_components").add("smart_notification.layer", { Component: SmartNotificationLayer });
