/** @odoo-module **/
// One place that holds the notifications of the signed-in user and keeps them live over the bus.
import { reactive } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";

const BUBBLE_KEY = "smart_notification.bubble";

function loadBubble() {
    try {
        const b = JSON.parse(window.localStorage.getItem(BUBBLE_KEY) || "null");
        if (b && typeof b.x === "number") return { visible: b.visible !== false, x: b.x, y: b.y };
    } catch {
        // storage blocked: the bubble starts at its default place
    }
    return { visible: true, x: null, y: null };
}

function loadFull() {
    try {
        return window.localStorage.getItem("smart_notification.full") === "1";
    } catch {
        return false;
    }
}

function chime(kind) {
    // two soft notes; a little lower for things that need attention
    try {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        const ctx = (chime.ctx ||= new Ctx());
        const notes = kind === "done" ? [784, 1046] : kind === "approval" ? [659, 880] : [880, 1175];
        notes.forEach((f, i) => {
            const o = ctx.createOscillator();
            const g = ctx.createGain();
            o.type = "sine";
            o.frequency.value = f;
            const t = ctx.currentTime + i * 0.11;
            g.gain.setValueAtTime(0.0001, t);
            g.gain.exponentialRampToValueAtTime(0.06, t + 0.02);
            g.gain.exponentialRampToValueAtTime(0.0001, t + 0.35);
            o.connect(g).connect(ctx.destination);
            o.start(t);
            o.stop(t + 0.4);
        });
    } catch {
        // no audio: silent is fine
    }
}

export const smartNotificationService = {
    dependencies: ["bus_service", "action", "notification"],
    start(env, { bus_service, action, notification }) {
        const state = reactive({
            counts: { unread: 0, todo: 0 }, items: [], tab: "todo", open: false, anchor: "systray",
            loading: false, more: false, toasts: [], bubble: loadBubble(), busy: null, justDone: null,
            expanded: {}, viewId: null, full: loadFull(), resolve: { mode: null, reason: "", error: "" },
        });
        let toastSeq = 0;

        const upsert = (item) => {
            const i = state.items.findIndex((x) => x.id === item.id);
            if (i >= 0) state.items[i] = item;
            else state.items.unshift(item);
        };
        const refreshCounts = async () => {
            try {
                state.counts = await rpc("/smart_notification/counts", {});
            } catch {
                // offline for a moment; the next push fixes it
            }
        };
        const load = async (tab = state.tab) => {
            state.tab = tab;
            state.loading = true;
            try {
                const res = await rpc("/smart_notification/list", { tab });
                if (state.tab !== tab) return;
                state.items = res.items;
                state.counts = res.counts;
                state.more = res.more;
            } finally {
                state.loading = false;
            }
            // what the user has now seen on screen counts as seen
            clearTimeout(load.seenT);
            load.seenT = setTimeout(() => {
                const ids = state.items.filter((x) => x.state === "new").map((x) => x.id);
                if (state.open && ids.length) api.markSeen(ids);
            }, 1500);
        };
        const pushToast = (item) => {
            const t = { key: ++toastSeq, item, leaving: false };
            state.toasts.push(t);
            if (state.toasts.length > 3) state.toasts.shift();
            if (item.n.priority !== "high" && item.n.policy === "info") {
                setTimeout(() => api.dismissToast(t.key), 7000);
            }
        };

        bus_service.subscribe("smart_notification/new", (item) => {
            if (state.tab === "all" || state.tab === "unread" || (state.tab === "todo" && item.n.policy !== "info")) upsert(item);
            refreshCounts();
            pushToast(item);
            chime(item.n.policy !== "info" ? "approval" : "info");
        });
        bus_service.subscribe("smart_notification/update", (item) => {
            if (state.items.some((x) => x.id === item.id)) upsert(item);
            for (const t of state.toasts) if (t.item.id === item.id) t.item = item;
            refreshCounts();
        });
        bus_service.start?.();
        refreshCounts();

        const api = {
            state,
            load,
            toggle(anchor = "systray") {
                if (state.open && state.anchor === anchor) return api.close();
                state.anchor = anchor;
                state.open = true;
                for (const t of [...state.toasts]) api.dismissToast(t.key);  // the panel shows them all
                load(state.counts.todo ? "todo" : (state.tab === "todo" ? "all" : state.tab));
            },
            close() {
                state.open = false;
                state.viewId = null;
                state.resolve = { mode: null, reason: "", error: "" };
            },
            show(item) {
                // the full story of one notification, inside the panel
                state.viewId = item.id;
                state.resolve = { mode: null, reason: "", error: "" };
                if (item.state === "new") api.markSeen([item.id]);
            },
            back() {
                state.viewId = null;
                state.resolve = { mode: null, reason: "", error: "" };
            },
            toggleFull() {
                state.full = !state.full;
                try {
                    window.localStorage.setItem("smart_notification.full", state.full ? "1" : "0");
                } catch {
                    // fine without storage
                }
                if (state.full && !state.viewId && state.items.length) state.viewId = state.items[0].id;
            },
            startResolve(mode) {
                state.resolve = { mode, reason: "", error: "" };
                setTimeout(() => document.querySelector(".o_sn_reason textarea")?.focus(), 30);
            },
            async resolve(item) {
                const r = state.resolve;
                if ((r.reason || "").trim().length < 3) {
                    r.error = "Write a short reason first.";
                    return;
                }
                state.busy = item.id;
                try {
                    const res = await rpc("/smart_notification/resolve", { line_id: item.id, outcome: r.mode, reason: r.reason });
                    upsert(res.item);
                    state.counts = res.counts;
                    state.resolve = { mode: null, reason: "", error: "" };
                    state.justDone = item.id;
                    setTimeout(() => (state.justDone = null), 1400);
                    chime("done");
                    if (res.action) await action.doAction(res.action);
                } catch (e) {
                    r.error = e?.data?.message || e?.message || "Could not save";
                } finally {
                    state.busy = null;
                }
            },
            async markSeen(ids) {
                state.counts = await rpc("/smart_notification/seen", { line_ids: ids });
                for (const it of state.items) {
                    if (ids.includes(it.id) && it.state === "new") it.state = it.n.policy === "info" ? "done" : "seen";
                }
            },
            async readAll() {
                state.counts = await rpc("/smart_notification/read_all", {});
                await load(state.tab);
            },
            async open(item) {
                const res = await rpc("/smart_notification/open", { line_id: item.id });
                if (item.state === "new") item.state = item.n.policy === "info" ? "done" : "seen";
                api.close();
                api.dismissToastOf(item.id);
                refreshCounts();
                if (res.url) {
                    window.location.href = res.url;
                } else if (res.action_id) {
                    // the menu's list first, then the record in its form: the breadcrumb leads back to the list
                    await action.doAction(res.action_id, { clearBreadcrumbs: true });
                    await action.switchView("form", { resId: res.res_id });
                } else if (res.action) {
                    await action.doAction(res.action);
                }
            },
            async act(item) {
                state.busy = item.id;
                try {
                    const res = await rpc("/smart_notification/act", { line_id: item.id });
                    upsert(res.item);
                    state.counts = res.counts;
                    state.justDone = item.id;
                    setTimeout(() => (state.justDone = null), 1400);
                    chime("done");
                    api.dismissToastOf(item.id);
                    if (res.action) await action.doAction(res.action);
                } catch (e) {
                    notification.add(e?.data?.message || e?.message || "Could not do that", { type: "danger" });
                    load(state.tab);
                } finally {
                    state.busy = null;
                }
            },
            dismissToast(key) {
                const t = state.toasts.find((x) => x.key === key);
                if (!t) return;
                t.leaving = true;
                setTimeout(() => {
                    const i = state.toasts.findIndex((x) => x.key === key);
                    if (i >= 0) state.toasts.splice(i, 1);
                }, 260);
            },
            dismissToastOf(lineId) {
                for (const t of state.toasts.filter((x) => x.item.id === lineId)) api.dismissToast(t.key);
            },
            saveBubble() {
                try {
                    window.localStorage.setItem(BUBBLE_KEY, JSON.stringify(state.bubble));
                } catch {
                    // private window: position is kept for this page only
                }
            },
            setBubbleVisible(v) {
                state.bubble.visible = v;
                api.saveBubble();
            },
        };
        return api;
    },
};

registry.category("services").add("smart_notification", smartNotificationService);
