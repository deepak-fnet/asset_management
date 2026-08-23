/** @odoo-module **/

import { registry } from "@web/core/registry";
import { KanbanController } from "@web/views/kanban/kanban_controller";
import { kanbanView } from "@web/views/kanban/kanban_view";
import { View } from "@web/views/view";

// Graph views rendered underneath the unified asset kanban. Each entry uses the
// model's default graph view, so no xml id lookup is needed.
const UNIFIED_GRAPHS = [
    { resModel: "asset.addition", title: "Asset Additions" },
    { resModel: "asset.internal.transfer", title: "Asset Internal Transfers" },
    { resModel: "asset.removal", title: "Asset Removals" },
    { resModel: "physical.verification", title: "Physical Verifications" },
];

export class UnifiedAssetKanbanController extends KanbanController {
    static template = "asset.UnifiedAssetKanbanView";
    static components = { ...KanbanController.components, View };

    setup() {
        super.setup();
        this.unifiedGraphs = UNIFIED_GRAPHS;
    }
}

registry.category("views").add("unified_asset_kanban_with_graph", {
    ...kanbanView,
    Controller: UnifiedAssetKanbanController,
});
