/** @odoo-module **/

import { Component, useRef, useState, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { _t } from "@web/core/l10n/translation";
import * as BarcodeScanner from "@web/core/barcode/barcode_dialog";
import { isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";

/**
 * Scan station for the Physical Verification form.
 *
 * Handheld scanners (USB / Bluetooth guns, Android handhelds like Zebra or
 * Honeywell in keyboard-wedge mode) "type" the label and press Enter. The
 * scan box keeps focus so a scan lands there; when focus is elsewhere, a
 * page-level listener still catches a burst of very fast keystrokes ending
 * in Enter (people do not type that fast) and treats it as a scan.
 *
 * The printed QR label is two lines ("Asset: <code>" / "Serial: <serial>"):
 * a wedge scanner sends Enter after each, so "Serial:" lines are skipped.
 * The camera button (phones / webcams) stays as before.
 */
class PhysicalVerificationScanner extends Component {
    static template = "general_asset.PhysicalVerificationScanner";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.input = useRef("scanInput");
        this.state = useState({ feed: [], busy: false, flash: "" });
        this.buffer = "";
        this.lastKey = 0;
        this.fastKeys = 0;
        this.onKeydown = this.onKeydown.bind(this);
        onMounted(() => {
            document.addEventListener("keydown", this.onKeydown, true);
            this.focus();
        });
        onWillUnmount(() => document.removeEventListener("keydown", this.onKeydown, true));
    }

    get data() {
        return this.props.record.data;
    }
    get active() {
        return this.data.state === "in_progress";
    }
    get total() {
        return this.data.total_count || 0;
    }
    get found() {
        return this.data.found_count || 0;
    }
    get missing() {
        return this.data.missing_count || 0;
    }
    get extra() {
        return this.data.extra_count || 0;
    }
    get pending() {
        return Math.max(this.total - this.found - this.missing, 0);
    }
    get pct() {
        return this.total ? Math.round((this.found * 100) / this.total) : 0;
    }
    get cameraSupported() {
        return isBarcodeScannerSupported();
    }

    focus() {
        if (this.active && this.input.el) {
            this.input.el.focus({ preventScroll: true });
        }
    }

    // scanner gun when the scan box is not focused
    onKeydown(ev) {
        if (!this.active || ev.target === this.input.el) {
            return;
        }
        const tag = (ev.target.tagName || "").toLowerCase();
        if (tag === "input" || tag === "textarea" || ev.target.isContentEditable) {
            return;
        }
        const now = performance.now();
        const gap = now - this.lastKey;
        this.lastKey = now;
        if (gap > 80) {
            this.buffer = "";
            this.fastKeys = 0;
        }
        if (ev.key === "Enter") {
            if (this.buffer.length >= 3 && this.fastKeys >= 3) {
                ev.preventDefault();
                ev.stopPropagation();
                this.submit(this.buffer);
            }
            this.buffer = "";
            this.fastKeys = 0;
            return;
        }
        if (ev.key.length === 1) {
            this.buffer += ev.key;
            if (gap < 50) {
                this.fastKeys++;
            }
        }
    }

    onInputKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            const value = ev.target.value;
            ev.target.value = "";
            this.submit(value);
        }
    }

    async submit(raw) {
        const code = (raw || "").trim();
        if (!code || /^serial\s*:/i.test(code)) {
            return;
        }
        this.state.busy = true;
        let result;
        try {
            result = await this.orm.call("physical.verification", "action_scan_found", [
                [this.props.record.resId],
                code,
            ]);
        } catch (error) {
            result = { success: false, message: error.message || _t("Scan failed."), code };
        }
        this.state.busy = false;
        const kind = result.success ? (result.already ? "again" : result.extra ? "extra" : "ok") : "bad";
        this.state.feed.unshift({
            id: Date.now() + Math.random(),
            code: result.code || code,
            name: result.name || "",
            message: result.message,
            kind,
            time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
        });
        this.state.feed.splice(6);
        this.feedback(kind);
        if (result.success && !result.already) {
            await this.props.record.load();
        }
        this.focus();
    }

    feedback(kind) {
        this.state.flash = kind;
        setTimeout(() => (this.state.flash = ""), 600);
        if ("vibrate" in navigator) {
            navigator.vibrate(kind === "bad" ? [80, 60, 80] : 80);
        }
        try {
            const ctx = (this.audio = this.audio || new (window.AudioContext || window.webkitAudioContext)());
            const tone = (freq, at, len) => {
                const o = ctx.createOscillator();
                const g = ctx.createGain();
                o.frequency.value = freq;
                o.connect(g);
                g.connect(ctx.destination);
                g.gain.setValueAtTime(0.12, ctx.currentTime + at);
                g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + at + len);
                o.start(ctx.currentTime + at);
                o.stop(ctx.currentTime + at + len);
            };
            if (kind === "ok" || kind === "extra") {
                tone(880, 0, 0.12);
                tone(1320, 0.1, 0.15);
            } else if (kind === "again") {
                tone(660, 0, 0.15);
            } else {
                tone(220, 0, 0.25);
            }
        } catch {
            // no audio - fine
        }
    }

    async onCameraClick() {
        let code;
        try {
            code = await BarcodeScanner.scanBarcode(this.env);
        } catch (error) {
            this.notification.add(error.message || _t("Could not access the camera."), { type: "danger" });
            return;
        }
        if (!code) {
            this.notification.add(_t("Please scan again."), { type: "warning" });
            return;
        }
        await this.submit(code);
    }
}

registry.category("view_widgets").add("physical_verification_scanner", {
    component: PhysicalVerificationScanner,
});
