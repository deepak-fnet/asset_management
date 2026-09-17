/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onWillStart, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const CAT_COLORS = ["#0e7490", "#8b5cf6", "#f59e0b", "#ec4899", "#10b981", "#3b82f6", "#f97316", "#14b8a6"];
const STATE_LABELS = {
    draft: "Draft", submitted: "Submitted", manager_review: "Manager Review",
    md_approval: "MD Approval", approved: "Approved", rejected: "Rejected", expired: "Expired",
};
const GRAN_LABELS = { day: "daily", week: "weekly", month: "monthly" };

export class VendorDashboard extends Component {
    static template = "vendor_management.VendorDashboard";
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            loading: true,        // first load only — full spinner
            refreshing: false,    // subsequent loads — soft overlay
            error: null,
            period: "month",      // week | month | quarter | year | custom
            dateFrom: null,       // ISO string when period === 'custom'
            dateTo: null,
            showRange: false,     // custom-range popover visibility
            draftFrom: "",        // popover inputs (uncommitted)
            draftTo: "",
            rangeError: "",
            lastUpdated: "",
            data: null,
            donut: [],            // computed arc segments
            donutTotal: 0,
            spend: null,          // computed line chart geometry
        });
        onWillStart(() => this.load());
    }

    // ------------------------------------------------------------ data
    async load() {
        const first = !this.state.data;
        if (first) this.state.loading = true;
        else this.state.refreshing = true;
        this.state.error = null;
        try {
            const data = await this.orm.call(
                "vendor.dashboard", "get_dashboard_data_v2",
                [this.state.period, this.state.dateFrom, this.state.dateTo]
            );
            this.state.data = data;
            this._buildDonut(data.charts.categories);
            this._buildSpend(data.charts.spend_trend);
            this.state.lastUpdated = new Date().toLocaleTimeString("en", {
                hour: "2-digit", minute: "2-digit",
            });
        } catch (e) {
            // A dead session must go back to login — don't trap it in the error card.
            const ename = e?.exceptionName || e?.data?.name || e?.name || "";
            const emsg = e?.data?.message || e?.message || "";
            if (/SessionExpired/i.test(ename) || /session expired/i.test(emsg)) {
                window.location.reload();
                return;
            }
            this.state.error = emsg || "Could not load dashboard data.";
        } finally {
            this.state.loading = false;
            this.state.refreshing = false;
        }
    }

    setPeriod(p) {
        if (p === this.state.period) return;
        this.state.period = p;
        this.state.dateFrom = null;
        this.state.dateTo = null;
        this.state.showRange = false;
        this.load();
    }

    refresh() {
        this.load();
    }

    // ------------------------------------------------------------ custom range popover
    toggleRange() {
        if (this.state.showRange) return this.closeRange();
        const r = this.state.data?.range;
        this.state.draftFrom = this.state.dateFrom || r?.from || "";
        this.state.draftTo = this.state.dateTo || r?.to || "";
        this.state.rangeError = "";
        this.state.showRange = true;
    }

    closeRange() {
        this.state.showRange = false;
        this.state.rangeError = "";
    }

    applyRange() {
        const { draftFrom, draftTo } = this.state;
        if (!draftFrom || !draftTo) {
            this.state.rangeError = "Pick both a start and an end date.";
            return;
        }
        let from = draftFrom, to = draftTo;
        if (from > to) [from, to] = [to, from];   // be forgiving about order
        this.state.period = "custom";
        this.state.dateFrom = from;
        this.state.dateTo = to;
        this.state.showRange = false;
        this.state.rangeError = "";
        this.load();
    }

    onRangeKeydown(ev) {
        if (ev.key === "Escape") this.closeRange();
        if (ev.key === "Enter") this.applyRange();
    }

    // ------------------------------------------------------------ header helpers
    rangeLabel() {
        return this.state.data?.range?.label || "Last 30 days";
    }

    customChip() {
        if (this.state.period !== "custom" || !this.state.dateFrom) return "Custom";
        const f = (iso) => new Date(iso + "T00:00:00").toLocaleDateString("en", { day: "2-digit", month: "short" });
        return `${f(this.state.dateFrom)} – ${f(this.state.dateTo)}`;
    }

    granLabel() {
        return GRAN_LABELS[this.state.data?.range?.granularity] || "monthly";
    }

    rangeDaysTag() {
        const d = this.state.data?.range?.days;
        return d ? `${d}d` : "";
    }

    // ------------------------------------------------------------ svg builders
    _polar(cx, cy, r, deg) {
        const rad = ((deg - 90) * Math.PI) / 180.0;
        return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
    }

    _arcPath(cx, cy, r, start, end) {
        const s = this._polar(cx, cy, r, end);
        const e = this._polar(cx, cy, r, start);
        const large = end - start <= 180 ? "0" : "1";
        return `M ${s.x} ${s.y} A ${r} ${r} 0 ${large} 0 ${e.x} ${e.y}`;
    }

    _buildDonut(cats) {
        const total = cats.reduce((a, c) => a + c.count, 0);
        const segs = [];
        let angle = 0;
        cats.forEach((c, i) => {
            const sweep = total ? (c.count / total) * 360 : 0;
            // cap at 359.9 so a single full category still draws
            const end = angle + Math.min(sweep, 359.9);
            segs.push({
                ...c,
                color: CAT_COLORS[i % CAT_COLORS.length],
                path: this._arcPath(70, 70, 54, angle, end),
                pct: total ? Math.round((c.count / total) * 100) : 0,
            });
            angle += sweep;
        });
        this.state.donut = segs;
        this.state.donutTotal = total;
    }

    /** Catmull-Rom → Bezier smoothing, with control points clamped
     *  to the plot area so flat/zero series never dip below the axis. */
    _smoothPath(pts, top, bottom) {
        if (pts.length < 3) {
            return pts.map((p, i) => `${i ? "L" : "M"} ${p.x} ${p.y}`).join(" ");
        }
        const clamp = (y) => Math.min(Math.max(y, top), bottom);
        let d = `M ${pts[0].x} ${pts[0].y}`;
        for (let i = 0; i < pts.length - 1; i++) {
            const p0 = pts[Math.max(i - 1, 0)];
            const p1 = pts[i];
            const p2 = pts[i + 1];
            const p3 = pts[Math.min(i + 2, pts.length - 1)];
            const c1x = p1.x + (p2.x - p0.x) / 6;
            const c1y = clamp(p1.y + (p2.y - p0.y) / 6);
            const c2x = p2.x - (p3.x - p1.x) / 6;
            const c2y = clamp(p2.y - (p3.y - p1.y) / 6);
            d += ` C ${c1x.toFixed(1)} ${c1y.toFixed(1)}, ${c2x.toFixed(1)} ${c2y.toFixed(1)}, ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
        }
        return d;
    }

    _buildSpend(points) {
        const W = 560, H = 190, PAD = 38, BOT = 28, TOP = 16;
        const max = Math.max(...points.map((p) => p.value), 1);
        const stepX = (W - PAD - 12) / Math.max(points.length - 1, 1);
        const pts = points.map((p, i) => ({
            ...p,
            x: PAD + i * stepX,
            y: H - BOT - (p.value / max) * (H - BOT - TOP),
        }));
        const line = this._smoothPath(pts, TOP, H - BOT);
        const area = `${line} L ${pts[pts.length - 1].x} ${H - BOT} L ${pts[0].x} ${H - BOT} Z`;
        // horizontal grid lines (4)
        const grid = [0.25, 0.5, 0.75, 1].map((f) => ({
            y: H - BOT - f * (H - BOT - TOP),
            label: this.compact(max * f),
        }));
        // thin the x labels so long (daily) ranges stay readable
        const labelStep = Math.max(1, Math.ceil(pts.length / 8));
        this.state.spend = { W, H, BOT, pts, line, area, grid, max, labelStep };
    }

    showXLabel(i) {
        const sp = this.state.spend;
        if (!sp) return false;
        if (i === sp.pts.length - 1) return true;
        // skip a step-mate that would collide with the final label
        if (i % sp.labelStep === 0) return sp.pts.length - 1 - i >= sp.labelStep / 2;
        return false;
    }

    isLastPt(i) {
        return this.state.spend && i === this.state.spend.pts.length - 1;
    }

    // ------------------------------------------------------------ formatting
    compact(v) {
        return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(v || 0);
    }

    money(v) {
        const c = this.state.data?.currency || "";
        return `${c} ${this.compact(v)}`;
    }

    num(v) {
        return new Intl.NumberFormat("en").format(v || 0);
    }

    abs(v) {
        return Math.abs(v || 0);
    }

    timeAgo(dt) {
        const then = new Date(dt.replace(" ", "T") + "Z");
        const mins = Math.floor((Date.now() - then.getTime()) / 60000);
        if (mins < 1) return "just now";
        if (mins < 60) return `${mins}m ago`;
        const h = Math.floor(mins / 60);
        if (h < 24) return `${h}h ago`;
        const d = Math.floor(h / 24);
        return d < 30 ? `${d}d ago` : then.toLocaleDateString();
    }

    stateLabel(s) {
        return STATE_LABELS[s] || s;
    }

    riskClass(r) {
        return r > 70 ? "vd2-risk-high" : r > 30 ? "vd2-risk-med" : "vd2-risk-low";
    }

    eventDot(e) {
        return { approval: "#22c55e", approved: "#22c55e", blacklisted: "#ef4444",
                 state_change: "#0e7490", created: "#14b8a6", deleted: "#ef4444" }[e] || "#94a3b8";
    }

    // ------------------------------------------------------------ drill-downs
    openVendors(domain, name) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: "res.partner",
            views: [[false, "list"], [false, "form"]],
            domain,
            context: { default_is_vendor: true, default_supplier_rank: 1 },
        });
    }

    openDocs(domain, name) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: "vendor.document",
            views: [[false, "list"], [false, "form"]],
            domain,
        });
    }

    onTotal() { this.openVendors([["is_vendor", "=", true]], "All Vendors"); }
    onActive() { this.openVendors([["is_vendor", "=", true], ["vendor_state", "=", "active"]], "Active Vendors"); }
    onReview() { this.openVendors([["is_vendor", "=", true], ["vendor_state", "in", ["submitted", "manager_review"]]], "Under Review"); }
    onBlocked() { this.openVendors([["is_vendor", "=", true], ["vendor_state", "=", "blocked"]], "Blocked Vendors"); }
    onHighRisk() { this.openVendors([["is_vendor", "=", true], ["risk_score", ">", 70]], "High Risk Vendors"); }
    onCompliant() { this.openVendors([["is_vendor", "=", true], ["compliance_status", "=", "ok"]], "Compliant Vendors"); }

    onDocsApproved() { this.openDocs([["state", "=", "approved"]], "Approved Documents"); }
    onDocsPending() { this.openDocs([["state", "in", ["submitted", "manager_review", "md_approval"]]], "Pending Approvals"); }
    onDocsExpiring() { this.openDocs([["is_expiring_soon", "=", true]], "Expiring Soon"); }
    onDocsExpired() { this.openDocs(["|", ["is_expired", "=", true], ["state", "=", "expired"]], "Expired Documents"); }

    /** PO drill-down honours the active range so the list matches the KPI. */
    onPOs() {
        const dom = [["state", "in", ["purchase", "done"]]];
        const r = this.state.data?.range;
        if (r) {
            dom.push(["date_order", ">=", `${r.from} 00:00:00`]);
            dom.push(["date_order", "<=", `${r.to} 23:59:59`]);
        }
        this.action.doAction({
            type: "ir.actions.act_window", name: "Purchase Orders",
            res_model: "purchase.order",
            views: [[false, "list"], [false, "form"]],
            domain: dom,
        });
    }

    onOutstanding() {
        this.action.doAction({
            type: "ir.actions.act_window", name: "Open Vendor Bills",
            res_model: "account.move",
            views: [[false, "list"], [false, "form"]],
            domain: [["move_type", "=", "in_invoice"], ["state", "=", "posted"],
                     ["payment_state", "not in", ["paid", "reversed"]]],
        });
    }

    async onCategory(seg) {
        const act = await this.orm.call("res.partner", "action_open_category_vendors", [seg.id]);
        this.action.doAction(act);
    }

    onRiskBand(band) {
        const dom = { low: [["risk_score", "<=", 30]], med: [["risk_score", ">", 30], ["risk_score", "<=", 70]], high: [["risk_score", ">", 70]] }[band];
        this.openVendors([["is_vendor", "=", true], ...dom], "Vendors by Risk");
    }

    onFunnel(row) { this.openDocs([["state", "=", row.state]], STATE_LABELS[row.state]); }

    openPartner(id) {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "res.partner", res_id: id,
            views: [[false, "form"]], target: "current",
        });
    }

    openDoc(id) {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "vendor.document", res_id: id,
            views: [[false, "form"]], target: "current",
        });
    }

    newVendor() {
        this.action.doAction({
            type: "ir.actions.act_window", name: "New Vendor",
            res_model: "res.partner", views: [[false, "form"]],
            context: { default_is_vendor: true, default_supplier_rank: 1 },
        });
    }

    // helpers for template maths
    riskBarPct(v) {
        const d = this.state.data.charts.risk_dist;
        const max = Math.max(d.low, d.med, d.high, 1);
        return Math.round((v / max) * 100);
    }

    funnelPct(count) {
        const max = Math.max(...this.state.data.charts.doc_funnel.map((f) => f.count), 1);
        return Math.round((count / max) * 100);
    }

    compliancePct() {
        const c = this.state.data.charts.compliance;
        const t = c.ok + c.pending + c.partial;
        return t ? Math.round((c.ok / t) * 100) : 0;
    }

    complianceDash() {
        // ring r=52 -> circumference 326.7
        const C = 326.7;
        const p = this.compliancePct() / 100;
        return `${(C * p).toFixed(1)} ${C.toFixed(1)}`;
    }
}

registry.category("actions").add("vendor_management.dashboard_action", VendorDashboard);
