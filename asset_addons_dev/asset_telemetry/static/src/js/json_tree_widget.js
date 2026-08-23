/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useService } from "@web/core/utils/hooks";

/**
 * A single node in the list. Recursive: renders itself for children.
 *
 * Collapsed by default below depth 1, because a full telemetry payload
 * expanded is thousands of lines and unusable.
 */
class JsonNode extends Component {
    setup() {
        this.state = useState({
            open: this.props.depth < 1,
        });
    }

    toggle() {
        this.state.open = !this.state.open;
    }

    get isContainer() {
        const v = this.props.value;
        return v !== null && typeof v === "object";
    }

    get isArray() {
        return Array.isArray(this.props.value);
    }

    get entries() {
        const v = this.props.value;
        if (v === null || typeof v !== "object") {
            return [];
        }
        if (Array.isArray(v)) {
            return v.map((item, i) => [String(i), item]);
        }
        return Object.entries(v);
    }

    get childCount() {
        return this.entries.length;
    }

    /** Short preview shown next to a collapsed container. */
    get summary() {
        if (this.isArray) {
            return `[ ${this.childCount} item${this.childCount === 1 ? "" : "s"} ]`;
        }
        const keys = this.entries.map(([k]) => k);
        const shown = keys.slice(0, 4).join(", ");
        const more = keys.length > 4 ? `, +${keys.length - 4}` : "";
        return `{ ${shown}${more} }`;
    }

    get valueClass() {
        const v = this.props.value;
        if (v === null) { return "jt-null"; }
        switch (typeof v) {
            case "number": return "jt-number";
            case "boolean": return "jt-bool";
            case "string": return "jt-string";
            default: return "jt-other";
        }
    }

    get displayValue() {
        const v = this.props.value;
        if (v === null) { return "null"; }
        if (typeof v === "string") {
            // Multi-line strings (command output) get their own treatment
            return v;
        }
        return String(v);
    }

    get isMultiline() {
        return typeof this.props.value === "string"
            && this.props.value.includes("\n");
    }
}

JsonNode.template = "asset_telemetry.JsonNode";
JsonNode.props = {
    label: { type: String },
    value: { optional: true },
    depth: { type: Number },
};
JsonNode.components = { JsonNode };

/**
 * Field widget. Use with:  <field name="agent_telemetry" widget="json_tree"/>
 */
class JsonTreeField extends Component {
    setup() {
        this.notification = useService("notification");
        this.state = useState({
            filter: "",
            expandAll: false,
        });
    }

    get data() {
        const raw = this.props.record.data[this.props.name];
        if (!raw) { return null; }
        if (typeof raw === "string") {
            try {
                return JSON.parse(raw);
            } catch {
                return { _raw: raw };
            }
        }
        return raw;
    }

    get isEmpty() {
        const d = this.data;
        return d === null || d === undefined
            || (typeof d === "object" && Object.keys(d).length === 0);
    }

    get topLevelEntries() {
        const d = this.data;
        if (!d || typeof d !== "object") { return []; }
        let entries = Array.isArray(d)
            ? d.map((v, i) => [String(i), v])
            : Object.entries(d);

        const f = this.state.filter.trim().toLowerCase();
        if (f) {
            entries = entries.filter(([k, v]) => {
                if (k.toLowerCase().includes(f)) { return true; }
                try {
                    return JSON.stringify(v).toLowerCase().includes(f);
                } catch {
                    return false;
                }
            });
        }
        return entries;
    }

    async onCopy() {
        try {
            await navigator.clipboard.writeText(
                JSON.stringify(this.data, null, 2)
            );
            this.notification.add("Telemetry copied to clipboard.",
                                  { type: "success" });
        } catch (err) {
            this.notification.add("Could not copy — check browser permissions.",
                                  { type: "warning" });
            console.error(err);
        }
    }

    onDownload() {
        try {
            const blob = new Blob([JSON.stringify(this.data, null, 2)],
                                  { type: "application/json" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            const name = this.props.record.data.serial_number || "asset";
            a.download = `telemetry_${name}_${Date.now()}.json`;
            a.click();
            URL.revokeObjectURL(url);
        } catch (err) {
            this.notification.add("Download failed.", { type: "danger" });
            console.error(err);
        }
    }
}

JsonTreeField.template = "asset_telemetry.JsonTreeField";
JsonTreeField.components = { JsonNode };
JsonTreeField.props = { ...standardFieldProps };

registry.category("fields").add("json_tree", {
    component: JsonTreeField,
    supportedTypes: ["json", "text", "char"],
});

export { JsonTreeField, JsonNode };
