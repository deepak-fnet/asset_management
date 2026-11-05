/** @odoo-module **/
import { Component, useState, useRef, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const fmtMin = (m) => {
    m = Math.round(m || 0);
    if (m < 60) return `${m}m`;
    return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
};
const ago = (s) => {
    if (!s) return "never";
    const t = new Date(s.replace(" ", "T") + "Z");
    const sec = (Date.now() - t.getTime()) / 1000;
    if (sec < 90) return "just now";
    if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
    if (sec < 86400) return `${Math.round(sec / 3600)} h ago`;
    return `${Math.round(sec / 86400)} days ago`;
};
const localTime = (s) => {
    if (!s) return "-";
    const t = new Date(s.replace(" ", "T") + "Z");
    return t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
};
const localDate = (s) => {
    if (!s) return "-";
    s = String(s);
    if (/^\d{8}$/.test(s)) s = `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;   // 20240311
    const t = new Date((s.length > 10 ? s.replace(" ", "T") + "Z" : s + "T00:00:00"));
    if (isNaN(t.getTime())) return s;
    return t.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
};

export class Asset360 extends Component {
    static template = "asset_fleet.Asset360";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        const p = this.props.action.params || {};
        this.assetId = p.asset_id || (this.props.action.context || {}).active_id;
        this.state = useState({ data: null, day: null, app: null, loading: true, tick: 0, hover: null,
                                section: "overview", open: {} });
        this.scrollRef = useRef("scroll");
        this.sections = [
            ["overview", "Overview", "fa-home"], ["screen", "Screen time", "fa-clock-o"],
            ["apps", "Apps & week", "fa-th-large"], ["device", "Device", "fa-laptop"],
            ["software", "Software & updates", "fa-cubes"], ["osupgrade", "OS upgrade", "fa-level-up"],
            ["security", "Security", "fa-shield"], ["location", "Location", "fa-map-marker"],
            ["procurement", "Procurement", "fa-shopping-cart"], ["warranty", "Warranty & AMC", "fa-certificate"],
            ["depreciation", "Depreciation", "fa-line-chart"], ["service", "Service & history", "fa-wrench"],
            ["all", "All details", "fa-list-alt"],
        ];
        this.fmtMin = fmtMin;
        this.ago = ago;
        this.localTime = localTime;
        this.localDate = localDate;
        onWillStart(() => this.load());
        onMounted(() => {
            const el = this.scrollRef.el;
            if (el) el.addEventListener("scroll", () => this.onScroll());
            this.timer = setInterval(() => this.load(true), 60000);
            this.clock = setInterval(() => (this.state.tick += 1), 1000);
        });
        onWillUnmount(() => { clearInterval(this.timer); clearInterval(this.clock); });
    }

    async load(silent) {
        if (!silent) this.state.loading = true;
        this.state.data = await this.orm.call("asset.asset", "get_asset_360", [this.assetId, this.state.day]);
        this.state.loading = false;
    }

    // ---------------------------------------------------------------- sections
    jump(id) {
        const el = this.scrollRef.el && this.scrollRef.el.querySelector(`[data-sec="${id}"]`);
        if (el) this.scrollRef.el.scrollTo({ top: el.offsetTop - 12, behavior: "smooth" });
        this.state.section = id;
    }
    onScroll() {
        const root = this.scrollRef.el;
        if (!root) return;
        let cur = "overview";
        for (const el of root.querySelectorAll("[data-sec]")) {
            if (el.offsetTop - 60 <= root.scrollTop) cur = el.dataset.sec;
        }
        if (cur !== this.state.section) this.state.section = cur;
    }
    toggle(key) { this.state.open[key] = !this.state.open[key]; }
    isOpen(key, i) { return this.state.open[key] === undefined ? i === 0 : this.state.open[key]; }
    money(v) {
        const f = this.state.data.finance;
        return `${f.symbol} ${Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
    }
    osuCls(k) { return { done: "ok", failed: "bad", in_progress: "run", approved: "run", cancelled: "muted" }[k] || "muted"; }
    openPo() { this.openForm("purchase.order", this.state.data.fixed.po_id); }
    openBill() { this.openForm("account.move", this.state.data.fixed.bill_id); }

    // ---------------------------------------------------------------- navigation
    get screen() { return (this.state.data && this.state.data.screen) || {}; }
    async shiftDay(step) {
        const days = this.screen.days || [];
        const i = days.indexOf(this.screen.day);
        const j = i - step;                       // list is newest first
        if (j >= 0 && j < days.length) { this.state.day = days[j]; this.state.app = null; await this.load(); }
    }
    async pickDay(d) { this.state.day = d; this.state.app = null; await this.load(); }
    get canPrev() { const d = this.screen.days || []; return d.indexOf(this.screen.day) < d.length - 1; }
    get canNext() { const d = this.screen.days || []; return d.indexOf(this.screen.day) > 0; }
    pickApp(name) { this.state.app = this.state.app === name ? null : name; }

    openForm(model, id, viewId = false) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: model, res_id: id, views: [[viewId, "form"]] });
    }
    openAsset() { this.openForm("asset.asset", this.state.data.asset.id, this.state.data.asset.fa_view_id); }
    // the IT asset always opens in the IT asset form (not the fixed-asset form)
    openIt() { this.openForm("asset.asset", this.state.data.asset.it_id, this.state.data.asset.it_view_id); }
    openEmployee() { this.openForm("hr.employee", this.state.data.employee.id); }

    // ---------------------------------------------------------------- visuals
    ring(value, max, r = 54) {
        const c = 2 * Math.PI * r;
        const pct = Math.max(0, Math.min(1, (value || 0) / (max || 1)));
        return { c, dash: `${c * pct} ${c}`, pct: Math.round(pct * 100) };
    }
    gauge(v) {
        const r = 34, c = 2 * Math.PI * r * 0.75;
        const pct = Math.max(0, Math.min(100, v || 0)) / 100;
        const color = v >= 85 ? "#e5484d" : v >= 65 ? "#f5a524" : "#2fb67c";
        return { dash: `${c * pct} ${2 * Math.PI * r}`, base: `${c} ${2 * Math.PI * r}`, color };
    }
    get weekMax() {
        const w = this.screen.week || [];
        return Math.max(60, ...w.map((d) => d.active + d.idle + d.locked));
    }
    barStyle(d, part) {
        const h = ((d[part] || 0) / this.weekMax) * 100;
        return `height:${h}%`;
    }
    get appDays() {
        const a = (this.screen.app_week || []).find((x) => x.name === this.state.app);
        return a ? a.days : null;
    }
    appBar(m) {
        const max = Math.max(30, ...(this.appDays || [0]));
        return `height:${(m / max) * 100}%`;
    }
    segStyle(s) {
        return `left:${(s.s / 1440) * 100}%;width:${Math.max(0.25, ((s.e - s.s) / 1440) * 100)}%`;
    }
    heatStyle(v) {
        const a = Math.min(1, v / 60);
        return `background: rgba(47, 182, 124, ${a ? 0.12 + a * 0.88 : 0.04})`;
    }
    get heatDays() { return (this.screen.week || []).map((w) => w.label); }
    get streak() {
        let s = 0;
        const w = [...(this.screen.week || [])].reverse();
        for (const d of w) { if (d.active >= 240) s += 1; else break; }
        return s;
    }
    get productivity() {
        const s = this.screen;
        if (!s.on) return 0;
        return Math.round((s.active / s.on) * 100);
    }
    get mapUrl() {
        const l = this.state.data.location;
        if (!l.lat && !l.lon) return false;
        const d = l.source_key === "browser" ? 0.01 : 0.06;
        return `https://www.openstreetmap.org/export/embed.html?bbox=${l.lon - d}%2C${l.lat - d * 0.6}%2C${l.lon + d}%2C${l.lat + d * 0.6}&layer=mapnik&marker=${l.lat}%2C${l.lon}`;
    }
    get greeting() {
        const e = this.state.data.employee;
        const s = this.screen;
        if (!e) return "No one is assigned to this asset yet";
        if (!s.has_data) return `${e.name.split(" ")[0]}'s device`;
        const tgt = s.target || 480;
        if (s.active >= tgt) return `${e.name.split(" ")[0]} hit the ${tgt / 60}h goal 🎯`;
        return `${e.name.split(" ")[0]} is ${Math.round((s.active / tgt) * 100)}% to today's goal`;
    }
    get trend() {
        const s = this.screen;
        if (!s.has_data || s.prev_active === null || s.prev_active === undefined) return null;
        const diff = s.active - s.prev_active;
        return { diff, up: diff >= 0, txt: (diff >= 0 ? "+" : "−") + fmtMin(Math.abs(diff)) + " vs previous day" };
    }
    get hourMax() { return Math.max(30, ...((this.screen.hourly) || [0])); }
    hourBar(v) { return `height:${Math.max(2, (v / 60) * 100)}%`; }
    hourCls(v) { return v >= 45 ? "hi" : v >= 20 ? "mid" : v > 0 ? "lo" : "none"; }
    sevCls(s) {
        s = (s || "").toLowerCase();
        return s.startsWith("crit") ? "crit" : s.startsWith("high") ? "high" : s.startsWith("med") ? "med" : "low";
    }
    storCls(p) { return p >= 90 ? "bad" : p >= 75 ? "warn" : "ok"; }
    warrantyPct() {
        const f = this.state.data.finance;
        if (!f.purchase_date || !f.warranty_end) return 0;
        const a = new Date(f.purchase_date), b = new Date(f.warranty_end), n = new Date();
        return Math.max(0, Math.min(100, ((n - a) / (b - a)) * 100));
    }
    initials(n) { return (n || "?").split(" ").map((x) => x[0]).join("").slice(0, 2).toUpperCase(); }
    get workday() {
        const s = this.screen;
        if (!s.first_on || !s.last_on) return 0;
        return (new Date(s.last_on.replace(" ", "T") + "Z") - new Date(s.first_on.replace(" ", "T") + "Z")) / 60000;
    }
    get depChart() {
        const sc = this.state.data.fixed.schedule || [];
        const w = 560, h = 210, left = 54, top = 16, bottom = 22;
        const cost = (this.state.data.finance.cost || 0) || ((sc[0] && (sc[0].rem + sc[0].amount)) || 0);
        const maxV = Math.max(cost, ...sc.map((l) => l.rem + l.amount), 1);
        const ih = h - top - bottom, n = Math.max(sc.length, 1);
        const slot = (w - left) / n, bw = Math.min(46, slot * 0.5);
        const y = (v) => top + ih - (v / maxV) * ih;
        const short = (v) => (v >= 100000 ? (v / 100000).toFixed(1) + "L" : v >= 1000 ? (v / 1000).toFixed(1) + "k" : String(Math.round(v)));
        const bars = sc.map((l, i) => {
            const x = left + slot * i + (slot - bw) / 2;
            return { x, bw, y: y(l.amount), h: Math.max(1, (l.amount / maxV) * ih), label: l.year,
                     short: short(l.amount), title: `${l.year}: depreciation ${this.money(l.amount)}` };
        });
        const pts = sc.map((l, i) => ({ x: left + slot * i + slot / 2, y: y(l.rem), title: `${l.year}: book value ${this.money(l.rem)}` }));
        const line = pts.map((p, i) => `${i ? "L" : "M"}${p.x},${p.y}`).join(" ");
        const area = pts.length ? `${line} L${pts[pts.length - 1].x},${top + ih} L${pts[0].x},${top + ih} Z` : "";
        const grid = [0, 0.25, 0.5, 0.75, 1].map((f) => ({ y: y(maxV * f), label: short(maxV * f) }));
        return { w, h, left, bars, pts, line, area, grid };
    }
    get hours24() { return Array.from({ length: 24 }, (_, i) => i); }
    pad2(h) { return String(h).padStart(2, "0"); }
    round(v) { return Math.round(v || 0); }
    hourLabel(h) { return h % 3 === 0 ? String(h).padStart(2, "0") : ""; }
    pctFill(v) { return `width:${Math.max(0, Math.min(100, v || 0))}%`; }
}

registry.category("actions").add("asset_fleet.asset_360", Asset360);
