/** @odoo-module **/

import { Component, useState, onWillUpdateProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { Field } from "@web/views/fields/field";
import { evaluateBooleanExpr } from "@web/core/py_js/py";

/**
 * Renders the general asset form's Classification/Placement area from the
 * SELECTED CATEGORY's own configuration (asset.category.left_field_ids /
 * right_field_ids) instead of a fixed set of fields hardcoded in this
 * module's XML - an admin can add/remove/reorder fields per category from
 * Categories > <category> > "General Asset Form - Left/Right Column",
 * with per-field readonly/invisible Python expressions, no code change.
 *
 * One RPC (asset.category.get_dynamic_field_lines) whenever category_id
 * changes; empty until a category is picked.
 */
class AssetDynamicCategoryFields extends Component {
    static template = "general_asset.AssetDynamicCategoryFields";
    static props = { ...standardWidgetProps };
    static components = { Field };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ left: [], right: [] });
        this._loadFor(this._categoryId(this.props.record));

        onWillUpdateProps((nextProps) => {
            const newId = this._categoryId(nextProps.record);
            if (newId !== this._categoryId(this.props.record)) {
                this._loadFor(newId);
            }
        });
    }

    _categoryId(record) {
        // Many2one values in record.data are { id, display_name } objects
        // in this framework version, NOT [id, name] arrays - indexing with
        // [0] silently returned undefined here, which is why the widget
        // never loaded anything even with a category correctly selected.
        const value = record.data.category_id;
        return value ? value.id : false;
    }

    async _loadFor(categoryId) {
        if (!categoryId) {
            this.state.left = [];
            this.state.right = [];
            return;
        }
        const result = await this.orm.call(
            "asset.category", "get_dynamic_field_lines", [categoryId]
        );
        this.state.left = result.left;
        this.state.right = result.right;
    }

    /** True unless the field actually exists on this record's model -
     * a category config referencing a field that got removed/renamed
     * since must not crash the whole form. */
    isKnownField(line) {
        return line.field_name in this.props.record.fields;
    }

    isInvisible(line) {
        if (!line.invisible_condition) {
            return false;
        }
        try {
            return evaluateBooleanExpr(
                line.invisible_condition,
                this.props.record.evalContextWithVirtualIds
            );
        } catch {
            // A broken expression hides the field rather than crashing the
            // form - same fail-closed spirit as asset.button.access.
            return true;
        }
    }

    isReadonly(line) {
        if (!line.readonly_condition) {
            return false;
        }
        try {
            return evaluateBooleanExpr(
                line.readonly_condition,
                this.props.record.evalContextWithVirtualIds
            );
        } catch {
            return false;
        }
    }
}

registry.category("view_widgets").add(
    "asset_dynamic_category_fields", { component: AssetDynamicCategoryFields }
);
