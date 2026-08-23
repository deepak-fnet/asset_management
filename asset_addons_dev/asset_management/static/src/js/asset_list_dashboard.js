/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { registry } from "@web/core/registry";

export class AssetListDashboard extends Component {
    static template = "asset_management.AssetListDashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        
        let initialFilter = 'all';
        const list = this.props.list;
        if (list) {
            const resModel = list.resModel;
            const context = list.context;
            const domain = list.domain || [];
            
            if (context.search_default_os_windows) initialFilter = 'windows';
            else if (context.search_default_os_linux) initialFilter = 'linux';
            else if (resModel === 'asset.camera') initialFilter = 'cctv';
            else if (resModel === 'asset.network.device') initialFilter = 'network';
            else {
                // Check if there are active filters in the domain that match our categories
                const domainStr = JSON.stringify(domain);
                if (domainStr.includes('os_platform') && domainStr.includes('windows')) initialFilter = 'windows';
                else if (domainStr.includes('os_platform') && domainStr.includes('linux')) initialFilter = 'linux';
            }
        }
        
        this.state = useState({
            total_assets: 0,
            windows_count: 0,
            linux_count: 0,
            camera_count: 0,
            network_count: 0,
            current_filter: initialFilter,
        });

        onWillStart(async () => {
            await this.loadData();
        });
    }

    async loadData() {
        try {
            const data = await this.orm.call("asset.dashboard", "get_system_overview_data", []);
            Object.assign(this.state, data);
        } catch (error) {
            console.error("Failed to load dashboard data:", error);
        }
    }

    setFilter(type) {
        const list = this.props.list;
        if (!list) return;
        const resModel = list.resModel;

        if (type === 'all') {
            if (resModel !== 'asset.asset') {
                this.action.doAction('asset_management.action_all_assets');
            } else {
                this.state.current_filter = 'all';
                this.env.searchModel.clearQuery();
            }
            return;
        }

        if (type === 'cctv') {
            if (resModel !== 'asset.camera') {
                this.action.doAction('asset_management.action_asset_camera', {
                    additionalContext: {}
                });
            } else {
                this.state.current_filter = 'cctv';
                this.env.searchModel.clearQuery();
            }
            return;
        }

        if (type === 'network') {
            if (resModel !== 'asset.network.device') {
                this.action.doAction('asset_management.action_asset_network_device', {
                    additionalContext: {}
                });
            } else {
                this.state.current_filter = 'network';
                this.env.searchModel.clearQuery();
            }
            return;
        }

        // For windows and linux, they are os_platform on asset.asset
        if (resModel !== 'asset.asset') {
            this.action.doAction('asset_management.action_all_assets', {
                additionalContext: { 
                    is_all_assets_view: true,
                    search_default_os_windows: type === 'windows' ? 1 : 0,
                    search_default_os_linux: type === 'linux' ? 1 : 0,
                }
            });
            return;
        }

        if (this.state.current_filter === type) {
            this.state.current_filter = 'all';
            this.env.searchModel.clearQuery();
            return;
        }

        const filterData = {
            'windows': { domain: '[("os_platform", "=", "windows")]', description: 'Windows Assets' },
            'linux': { domain: '[("os_platform", "=", "linux")]', description: 'Linux Assets' },
        }[type];

        if (filterData) {
            this.state.current_filter = type;
            this.env.searchModel.clearQuery();
            this.env.searchModel.createNewFilters([filterData]);
        }
    }
}

import { KanbanRenderer } from "@web/views/kanban/kanban_renderer";
import { ListRenderer } from "@web/views/list/list_renderer";
import { patch } from "@web/core/utils/patch";

patch(KanbanRenderer.prototype, {
    setup() {
        super.setup();
    }
});

patch(ListRenderer.prototype, {
    setup() {
        super.setup();
    }
});

KanbanRenderer.components = {
    ...KanbanRenderer.components,
    AssetListDashboard,
};

ListRenderer.components = {
    ...ListRenderer.components,
    AssetListDashboard,
};
