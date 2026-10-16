/** @odoo-module **/
/* Settings > Login Page > Animations: the login page in an iframe, restyled on
   every change of the form (before saving) and a slider field for sizes. */
import { Component, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";
import { useRecordObserver } from "@web/model/relational_model/utils";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const VALUE_TYPES = ["char", "integer", "float", "boolean", "selection"];
const DEVICES = { desktop: [1366, 820], mobile: [400, 820] };

export class PerokLivePreview extends Component {
    static template = "perok_login.LivePreview";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.frame = useRef("frame");
        this.box = useRef("box");
        this.state = useState({ device: "desktop", scale: 0.5, reload: 0, busy: false });
        this.vals = {};
        this.ready = false;
        this.push = useDebounced(() => this.send(), 250);
        useRecordObserver((record) => {
            const vals = {};
            for (const [name, field] of Object.entries(record.fields)) {
                if (VALUE_TYPES.includes(field.type) && name in record.data) {
                    vals[name] = record.data[name];
                }
            }
            this.vals = vals;
            this.push();
        });
        this.onMessage = (ev) => {
            if (ev.origin === location.origin && ev.data && ev.data.type === "perok-preview-ready") {
                this.ready = true;
                this.send();
            }
        };
        onMounted(() => {
            window.addEventListener("message", this.onMessage);
            this.resize = new ResizeObserver(() => this.fit());
            this.resize.observe(this.box.el);
            this.fit();
        });
        onWillUnmount(() => {
            window.removeEventListener("message", this.onMessage);
            this.resize.disconnect();
        });
    }

    get resId() {
        return this.props.record.resId;
    }
    get src() {
        return `/perok_login/preview/${this.resId}?r=${this.state.reload}`;
    }
    get size() {
        return DEVICES[this.state.device];
    }
    get frameStyle() {
        const [w, h] = this.size;
        return `width:${w}px;height:${h}px;left:50%;margin-left:-${w / 2}px;transform:scale(${this.state.scale});`;
    }
    get boxStyle() {
        const [w, h] = this.size;
        return `height:${Math.round(h * this.state.scale)}px;`;
    }

    fit() {
        if (!this.box.el) {
            return;
        }
        const [w] = this.size;
        this.state.scale = Math.min(1, (this.box.el.clientWidth - 2) / w);
    }
    setDevice(device) {
        this.state.device = device;
        this.ready = false;
        this.fit();
    }
    refresh() {
        this.ready = false;
        this.state.reload++;
    }

    async send() {
        const win = this.frame.el && this.frame.el.contentWindow;
        if (!this.ready || !win) {
            return;
        }
        this.state.busy = true;
        try {
            const ids = this.resId ? [this.resId] : [];
            const payload = await this.orm.call("perok.login.animation", "perok_preview_payload", [ids, this.vals]);
            win.postMessage({ type: "perok-preview", ...payload }, location.origin);
        } finally {
            this.state.busy = false;
        }
    }
}

registry.category("view_widgets").add("perok_live_preview", { component: PerokLivePreview });

/* a slider with its value next to it: options="{'min': 0, 'max': 60, 'step': 1, 'unit': 'px'}" */
export class PerokRangeField extends Component {
    static template = "perok_login.RangeField";
    static props = {
        ...standardFieldProps,
        min: { type: Number, optional: true },
        max: { type: Number, optional: true },
        step: { type: Number, optional: true },
        unit: { type: String, optional: true },
    };
    static defaultProps = { min: 0, max: 100, step: 1, unit: "" };

    get value() {
        return this.props.record.data[this.props.name] || 0;
    }
    get percent() {
        const { min, max } = this.props;
        return Math.max(0, Math.min(100, ((this.value - min) / (max - min)) * 100));
    }
    onInput(ev) {
        const v = parseFloat(ev.target.value);
        if (!isNaN(v)) {
            this.props.record.update({ [this.props.name]: v });
        }
    }
}

registry.category("fields").add("pk_range", {
    component: PerokRangeField,
    supportedTypes: ["integer", "float"],
    extractProps: ({ options }) => ({
        min: options.min,
        max: options.max,
        step: options.step,
        unit: options.unit,
    }),
});
