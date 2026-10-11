import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";
import { deserializeDateTime } from "@web/core/l10n/dates";
import { scanBarcode } from "@web/core/barcode/barcode_dialog";
import { isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";
import { Component, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { RecordSelector } from "@web/core/record_selectors/record_selector";
import { CameraDialog } from "../camera/camera_dialog";
import { playSound } from "../sounds";

const { DateTime } = luxon;


function emptyForm() {
    return {
        mobile: "",
        visitor_name: "",
        company_name: "",
        email: "",
        purpose_id: false,
        purpose_note: "",
        host_id: false,
        photo: false,
        photo_consent: false,
        expected_hours: 2,
    };
}

export class VisitorDesk extends Component {
    static template = "visitor_desk.Desk";
    static props = ["*"];
    static components = { RecordSelector };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.scanRef = useRef("scanInput");
        this.canRegister = false;
        // camera scanning needs a secure context; USB/Bluetooth scanners work everywhere
        this.canScanCamera = window.isSecureContext && isBarcodeScannerSupported();
        this.state = useState({
            loading: true,
            data: { kpi: {}, inside: [], waiting: [], recent: [] },
            tab: "inside",
            now: DateTime.now(),
            form: emptyForm(),
            returning: null,
            purposes: [],
            saving: false,
            success: null,
            scanCode: "",
            lastScan: null,
            pulse: {},
        });

        onWillStart(async () => {
            this.canRegister = await user.hasGroup("visitor_desk.group_visitor_reception");
            const [purposes] = await Promise.all([
                this.orm.call("visitor.purpose", "desk_purposes", []),
                this.refresh(),
            ]);
            this.state.purposes = purposes;
            this.applyDefaultPurpose();
        });

        const clock = setInterval(() => (this.state.now = DateTime.now()), 1000);
        const poll = setInterval(() => this.refresh(), 20000);
        onWillUnmount(() => {
            clearInterval(clock);
            clearInterval(poll);
        });
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------
    async refresh() {
        this.state.data = await this.orm.call("visitor.visit", "desk_data", []);
        this.state.loading = false;
    }

    get greeting() {
        const h = this.state.now.hour;
        const part = h < 12 ? _t("Good morning") : h < 17 ? _t("Good afternoon") : _t("Good evening");
        return `${part}, ${user.name.split(" ")[0]}`;
    }

    get boardList() {
        return this.state.data[this.state.tab] || [];
    }

    photoUrl(card) {
        const unique = deserializeDateTime(card.write_date).ts;
        return `/web/image/visitor.visit/${card.id}/photo/160x160?unique=${unique}`;
    }

    initials(name) {
        return (name || "?")
            .split(" ")
            .filter(Boolean)
            .slice(0, 2)
            .map((p) => p[0].toUpperCase())
            .join("");
    }

    elapsed(card) {
        const start = card.check_in || card.registered_on;
        if (!start) {
            return "";
        }
        const mins = Math.max(0, Math.floor(this.state.now.diff(deserializeDateTime(start), "minutes").minutes));
        return mins < 60 ? `${mins}m` : `${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, "0")}m`;
    }

    /** 0..100+ share of the expected stay already used, drives the colour bar. */
    stayRatio(card) {
        if (!card.check_in || !card.expected_hours) {
            return 0;
        }
        const hours = this.state.now.diff(deserializeDateTime(card.check_in), "hours").hours;
        return Math.round((hours / card.expected_hours) * 100);
    }

    stayClass(card) {
        const r = this.stayRatio(card);
        return r >= 100 ? "o_vd_over" : r >= 75 ? "o_vd_warn" : "o_vd_ok";
    }

    timeOf(value) {
        return value ? deserializeDateTime(value).toFormat("hh:mm a") : "";
    }

    // ------------------------------------------------------------------
    // Registration
    // ------------------------------------------------------------------
    async onMobileInput(ev) {
        const digits = ev.target.value.replace(/\D/g, "").slice(-10);
        this.state.form.mobile = ev.target.value;
        if (digits.length < 10) {
            this.state.returning = null;
            return;
        }
        const found = await this.orm.call("visitor.visitor", "lookup_by_mobile", [digits]);
        this.state.returning = found || null;
        if (found) {
            playSound(found.is_blocked || found.active_visit ? "error" : "blip");
            const f = this.state.form;
            f.visitor_name = found.name;
            f.company_name = found.company_name;
            f.email = found.email;
            if (this.state.purposes.some((p) => p.id === found.last_purpose_id)) {
                this.choosePurpose(found.last_purpose_id);
            }
            f.host_id = found.last_host_id || f.host_id;
            if (found.has_photo && !f.photo) {
                const [rec] = await this.orm.read("visitor.visitor", [found.id], ["photo"]);
                f.photo = rec.photo || false;
            }
        }
    }

    capturePhoto() {
        this.dialog.add(CameraDialog, {
            onCapture: (b64) => {
                this.state.form.photo = b64;
                this.state.form.photo_consent = true;
            },
        });
    }

    get formPhotoSrc() {
        return this.state.form.photo ? `data:image/jpeg;base64,${this.state.form.photo}` : false;
    }

    get formReady() {
        const f = this.state.form;
        return (
            !this.state.returning?.active_visit &&
            f.visitor_name.trim() &&
            f.mobile.replace(/\D/g, "").length >= 10 &&
            f.purpose_id &&
            (!f.photo || f.photo_consent)
        );
    }

    async register(checkIn) {
        if (!this.formReady || this.state.saving) {
            return;
        }
        if (checkIn && this.state.returning?.is_blocked) {
            this.notification.add(_t("This visitor is blocked and cannot be checked in."), { type: "danger" });
            playSound("error");
            return;
        }
        this.state.saving = true;
        const f = this.state.form;
        const vals = {
            ...f,
            host_id: f.host_id || false,
            expected_hours: parseFloat(f.expected_hours) || 2,
        };
        try {
            const card = await this.orm.call("visitor.visit", "desk_register", [vals, checkIn]);
            this.state.success = { ...card, photoSrc: this.formPhotoSrc, returning: this.state.returning };
            this.state.form = emptyForm();
            this.applyDefaultPurpose();
            this.state.returning = null;
            this.flash(card.id);
            playSound(checkIn ? "checkin" : "success");
            await this.refresh();
        } catch (e) {
            playSound("error");
            throw e;
        } finally {
            this.state.saving = false;
        }
    }

    applyDefaultPurpose() {
        if (!this.state.form.purpose_id && this.state.purposes.length) {
            this.choosePurpose(this.state.purposes[0].id);
        }
    }

    choosePurpose(id) {
        const purpose = this.state.purposes.find((p) => p.id === id);
        this.state.form.purpose_id = id;
        if (purpose?.default_hours) {
            this.state.form.expected_hours = purpose.default_hours;
        }
    }

    purposeStyle(p) {
        return this.state.form.purpose_id === p.id
            ? `background: linear-gradient(120deg, ${p.color}, ${p.color_end}); border-color: ${p.color}; color: #fff;`
            : `--vd-chip: ${p.color};`;
    }

    resetForm() {
        this.state.form = emptyForm();
        this.applyDefaultPurpose();
        this.state.returning = null;
        this.state.success = null;
    }

    // ------------------------------------------------------------------
    // Security actions
    // ------------------------------------------------------------------
    async runScan(code) {
        code = (code || "").trim();
        if (!code) {
            return;
        }
        const res = await this.orm.call("visitor.visit", "desk_scan", [code]);
        this.state.scanCode = "";
        this.state.lastScan = res;
        const name = res.visit?.visitor_name;
        const messages = {
            checked_in: [_t("%s checked in. Welcome!", name), "success"],
            checked_out: [_t("%s checked out. Goodbye!", name), "info"],
            not_found: [_t("No pass found for “%s”.", code), "danger"],
            blocked: [_t("%s is BLOCKED: %s", name, res.reason), "danger"],
            cancelled: [_t("This pass was cancelled."), "warning"],
            expired: [_t("Pass of %s has expired (valid only on the day issued). Register again.", name), "danger"],
            too_soon: [_t("%s was checked in a moment ago. Scan again later to check out.", name), "warning"],
        };
        const [msg, type] = messages[res.status] || [_t("%s has already left.", name), "warning"];
        this.notification.add(msg, { type });
        playSound({ checked_in: "checkin", checked_out: "checkout" }[res.status] || "error");
        if (res.visit) {
            this.flash(res.visit.id);
        }
        await this.refresh();
        this.scanRef.el?.focus();
    }

    onScanKey(ev) {
        if (ev.key === "Enter") {
            this.runScan(this.state.scanCode);
        }
    }

    async openCameraScanner() {
        try {
            const code = await scanBarcode(this.env);
            await this.runScan(code);
        } catch (e) {
            this.notification.add(e?.message || _t("Scanner closed"), { type: "warning" });
        }
    }

    /** Quick actions on the visit that blocks a new registration. */
    async resolveActive(action) {
        const card = this.state.returning.active_visit;
        if (action === "checkin") {
            await this.checkIn(card);
        } else if (action === "checkout") {
            await this.checkOut(card);
        } else if (action === "cancel") {
            await this.orm.call("visitor.visit", "action_cancel", [[card.id]]);
            this.notification.add(_t("Pass %s cancelled", card.name), { type: "warning" });
            await this.refresh();
        }
        // re-run the lookup so the banner disappears and the form unlocks
        await this.onMobileInput({ target: { value: this.state.form.mobile } });
    }

    async checkIn(card) {
        await this.orm.call("visitor.visit", "action_check_in", [[card.id]]);
        this.notification.add(_t("%s checked in", card.visitor_name), { type: "success" });
        playSound("checkin");
        await this.refresh();
    }

    async checkOut(card) {
        // Let the card fade out before the list refreshes
        this.state.pulse[card.id] = "leaving";
        await this.orm.call("visitor.visit", "action_check_out", [[card.id]]);
        playSound("checkout");
        setTimeout(() => this.refresh(), 350);
        this.notification.add(_t("%s checked out after %s", card.visitor_name, this.elapsed(card)), {
            type: "info",
        });
    }

    async printPass(card) {
        const action = await this.orm.call("visitor.visit", "action_print_pass", [[card.id]]);
        await this.action.doAction(action);
    }

    openVisit(card) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "visitor.visit",
            res_id: card.id,
            views: [[false, "form"]],
        });
    }

    openList(filter) {
        this.action.doAction("visitor_desk.action_visitor_visit", {
            additionalContext: { [`search_default_${filter}`]: 1 },
        });
    }

    setHost(id) {
        this.state.form.host_id = id || false;
    }

    flash(id) {
        this.state.pulse[id] = "new";
        setTimeout(() => delete this.state.pulse[id], 2500);
    }
}

registry.category("actions").add("visitor_desk.desk", VisitorDesk);
