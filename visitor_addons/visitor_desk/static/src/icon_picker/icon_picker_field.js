import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, useState } from "@odoo/owl";

// Curated Font Awesome 4 icons that make sense for a visit purpose
const ICONS = [
    "fa-handshake-o", "fa-user-circle", "fa-truck", "fa-briefcase", "fa-wrench", "fa-heart",
    "fa-ellipsis-h", "fa-users", "fa-graduation-cap", "fa-id-card-o", "fa-cutlery", "fa-coffee",
    "fa-cube", "fa-cubes", "fa-dropbox", "fa-shopping-cart", "fa-money", "fa-calculator",
    "fa-bank", "fa-legal", "fa-gavel", "fa-shield", "fa-medkit", "fa-stethoscope",
    "fa-user-md", "fa-ambulance", "fa-fire-extinguisher", "fa-bolt", "fa-plug", "fa-cogs",
    "fa-laptop", "fa-desktop", "fa-wifi", "fa-video-camera", "fa-camera", "fa-microphone",
    "fa-bullhorn", "fa-calendar", "fa-clock-o", "fa-building", "fa-industry", "fa-home",
    "fa-car", "fa-bus", "fa-plane", "fa-ship", "fa-paint-brush", "fa-leaf",
    "fa-recycle", "fa-trash", "fa-key", "fa-lock", "fa-search", "fa-clipboard",
    "fa-file-text-o", "fa-envelope", "fa-phone", "fa-gift", "fa-star", "fa-trophy",
    "fa-child", "fa-graduation-cap", "fa-book", "fa-flask", "fa-globe", "fa-question-circle",
];

/** Char field holding a Font Awesome class, edited through a searchable icon grid. */
export class IconPickerField extends Component {
    static template = "visitor_desk.IconPickerField";
    static components = { Dropdown, DropdownItem };
    static props = { ...standardFieldProps };

    setup() {
        this.state = useState({ search: "" });
    }

    get value() {
        return this.props.record.data[this.props.name] || "";
    }

    /** Tint with the record's colour field when there is one (purpose list). */
    get color() {
        return this.props.record.data.color || "";
    }

    get icons() {
        const q = this.state.search.trim().toLowerCase().replace(/^fa-/, "");
        const all = [...new Set(ICONS)];
        return q ? all.filter((i) => i.includes(q)) : all;
    }

    select(icon) {
        this.props.record.update({ [this.props.name]: icon });
    }

    onCustom(ev) {
        // allow any FA4 class not in the curated list
        if (ev.key === "Enter") {
            const v = ev.target.value.trim().toLowerCase();
            if (v) {
                this.select(v.startsWith("fa-") ? v : `fa-${v}`);
            }
        }
    }
}

registry.category("fields").add("vd_icon_picker", {
    component: IconPickerField,
    displayName: _t("Icon Picker"),
    supportedTypes: ["char"],
});
