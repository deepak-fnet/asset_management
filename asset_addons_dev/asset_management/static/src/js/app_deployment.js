/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";

/**
 * App Deployment Dashboard Component
 *
 * Modern dashboard for managing application deployments
 * with package manager-based installation commands.
 */
export class AppDeploymentDashboard extends Component {
    static template = "asset_management.AppDeploymentDashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        // Get platform from context (set by action)
        // In Odoo 19, context is passed via this.props.action.context
        const actionContext = this.props.action?.context || {};
        this.platformFilter = actionContext.default_platform || null;
        
        // Debug log to check context
        console.log('[App Deployment] Setup - Full props:', this.props);
        console.log('[App Deployment] Setup - Action:', this.props.action);
        console.log('[App Deployment] Setup - Action Context:', actionContext);
        console.log('[App Deployment] Setup - Platform Filter:', this.platformFilter);

        this.state = useState({
            loading: true,
            deployments: [],
            assets: [],
            filteredAssets: [],
            stats: {
                pending: 0,
                in_progress: 0,
                success: 0,
                failed: 0,
            },
            assetSearchQuery: "",
            assetStatusFilter: "all",
            platform: this.platformFilter, // Keep for backward compatibility
            platformFilter: this.platformFilter, // Use this for template checks
            activePlatform: this.platformFilter || 'windows', // Default to windows if no filter
            autoRefresh: true,
            lastRefresh: null,
            selectedAssets: new Set(), // Track selected asset IDs
            selectAll: false, // Track select all state
        });

        this.refreshInterval = null;

        onWillStart(async () => {
            await this.loadDashboardData();
        });

        onMounted(() => {
            // Start auto-refresh every 30 seconds
            this.startAutoRefresh();
        });

        onWillUnmount(() => {
            // Clean up interval
            this.stopAutoRefresh();
        });
    }

    /**
     * Start auto-refresh timer
     */
    startAutoRefresh() {
        this.refreshInterval = setInterval(() => {
            if (this.state.autoRefresh) {
                this.loadDashboardData();
            }
        }, 30000); // Refresh every 30 seconds
    }

    /**
     * Stop auto-refresh timer
     */
    stopAutoRefresh() {
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
            this.refreshInterval = null;
        }
    }

    /**
     * Toggle auto-refresh
     */
    toggleAutoRefresh() {
        this.state.autoRefresh = !this.state.autoRefresh;
        if (this.state.autoRefresh) {
            this.loadDashboardData();
            this.notification.add("Auto-refresh enabled", { type: "success" });
        } else {
            this.notification.add("Auto-refresh disabled", { type: "warning" });
        }
    }

    /**
     * Load all dashboard data
     */
    async loadDashboardData() {
        try {
            this.state.loading = true;

            // Load deployment statistics
            await this.loadDeploymentStats();

            // Load available assets for the platform
            await this.loadAvailableAssets();

            // Apply asset filters
            this.filterAssets();

            // Update last refresh time
            this.state.lastRefresh = new Date().toLocaleTimeString();
        } catch (error) {
            console.error("Error loading dashboard data:", error);
            this.notification.add("Failed to load deployment data", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    /**
     * Load deployment statistics
     */
    async loadDeploymentStats() {
        try {
            // Build domain filter based on platform
            let domain = [];
            if (this.platformFilter) {
                // Use device_id's os_platform field
                const platformAssets = await this.orm.searchRead("asset.asset", 
                    [["os_platform", "=", this.platformFilter]], 
                    ["id"]
                );
                const assetIds = platformAssets.map(a => a.id);
                if (assetIds.length > 0) {
                    domain = [["device_id", "in", assetIds]];
                } else {
                    // No assets for this platform, set empty stats
                    this.state.stats = {
                        pending: 0,
                        in_progress: 0,
                        success: 0,
                        failed: 0,
                    };
                    return;
                }
            }

            // Load deployments
            const deployments = await this.orm.searchRead("asset_management.app_deployment", domain, [
                "id",
                "status",
            ]);

            // Calculate stats
            this.state.stats = {
                pending: deployments.filter(d => d.status === "pending").length,
                in_progress: deployments.filter(d => d.status === "in_progress").length,
                success: deployments.filter(d => d.status === "success").length,
                failed: deployments.filter(d => d.status === "failed").length,
            };
        } catch (error) {
            console.error("Error loading deployment stats:", error);
        }
    }

    /**
     * Load available assets for the current platform
     */
    async loadAvailableAssets() {
        try {
            let domain = [];
            // If we have a hard platform filter from context, use it
            if (this.platformFilter) {
                domain = [["os_platform", "=", this.platformFilter]];
            } else {
                // Otherwise fetch for the active tab
                domain = [["os_platform", "=", this.state.activePlatform]];
            }

            const assets = await this.orm.searchRead("asset.asset", domain, [
                "id",
                "asset_name",
                "asset_code",
                "serial_number",
                "assigned_employee_id",
                "department_id",
                "state",
                "last_sync_time",
                "os_platform",
                "image_1920",
            ], {
                limit: 100,
                order: "asset_name asc",
            });

            const assetIds = (assets || []).map(a => a.id);

            // Fetch the latest deployment status for each asset
            let latestDepByAsset = {};
            // Fetch installed app count per asset
            let appCountByAsset = {};

            if (assetIds.length > 0) {
                try {
                    const deps = await this.orm.searchRead(
                        "asset_management.app_deployment",
                        [["device_id", "in", assetIds]],
                        ["device_id", "status", "deployment_created"],
                        { order: "deployment_created desc", limit: 500 }
                    );
                    // Keep only the most recent deployment per asset
                    deps.forEach(d => {
                        const aid = d.device_id[0];
                        if (!latestDepByAsset[aid]) {
                            latestDepByAsset[aid] = d.status;
                        }
                    });
                } catch (e) {
                    console.warn("Could not load deployment statuses:", e);
                }

                try {
                    // Odoo 19 uses formattedReadGroup (replaces old readGroup)
                    // aggregates: ["__count"] returns the group record count in g.__count
                    const appGroups = await this.orm.formattedReadGroup(
                        "asset.installed.application",
                        [["asset_id", "in", assetIds]],
                        ["asset_id"],
                        ["__count"]
                    );
                    appGroups.forEach(g => {
                        if (g.asset_id) {
                            appCountByAsset[g.asset_id[0]] = g.__count || 0;
                        }
                    });
                } catch (e) {
                    console.warn("Could not load installed app counts:", e);
                }
            }

            this.state.assets = (assets || []).map(asset => {
                const isOnline = this.calculateOnlineStatus(asset.last_sync_time);
                return {
                    id: asset.id,
                    asset_name: asset.asset_name,
                    asset_code: asset.asset_code,
                    serial_number: asset.serial_number,
                    assigned_employee: asset.assigned_employee_id?.[1] || 'Unassigned',
                    department: asset.department_id?.[1] || '-',
                    state: asset.state,
                    last_sync_time: asset.last_sync_time,
                    is_online: isOnline,
                    os_platform: asset.os_platform,
                    has_image: !!asset.image_1920,
                    dep_status: latestDepByAsset[asset.id] || null,
                    app_count: appCountByAsset[asset.id] || 0,
                };
            });
        } catch (error) {
            console.error("Error loading assets:", error);
        }
    }

    /**
     * Calculate online status based on last_sync_time
     * Online if synced within last 3 minutes (180 seconds) - matches server logic
     */
    calculateOnlineStatus(lastSyncTime) {
        if (!lastSyncTime) {
            console.log('[DEBUG] Offline: No last_sync_time');
            return false; // Never synced = Offline
        }
        
        try {
            // Parse the datetime string from Odoo (format: "2026-03-16 10:45:00")
            // Odoo stores datetimes in UTC, so we parse as UTC
            const lastSync = new Date(lastSyncTime + 'Z'); // Append Z to treat as UTC
            if (isNaN(lastSync.getTime())) {
                console.log('[DEBUG] Offline: Invalid date:', lastSyncTime);
                return false; // Invalid date = Offline
            }
            
            const now = new Date();
            const diffMs = now - lastSync;
            const diffSeconds = Math.floor(diffMs / 1000);
            const diffMinutes = Math.floor(diffSeconds / 60);
            
            console.log('[DEBUG] Last sync:', lastSyncTime, '| Diff:', diffSeconds, 'seconds (', diffMinutes, 'minutes )');
            
            // Online if synced within last 180 seconds (3 minutes) - matches server timeout
            const isOnline = diffSeconds <= 180;
            console.log('[DEBUG] Status:', isOnline ? 'ONLINE' : 'OFFLINE');
            return isOnline;
        } catch (error) {
            console.error('[DEBUG] Error calculating online status:', error);
            return false;
        }
    }

    /**
     * Filter assets based on search query and status
     */
    filterAssets() {
        const query = this.state.assetSearchQuery.toLowerCase();
        const statusFilter = this.state.assetStatusFilter;

        this.state.filteredAssets = this.state.assets.filter(asset => {
            // Search filter
            const matchesSearch = !query || 
                asset.asset_name.toLowerCase().includes(query) ||
                asset.asset_code.toLowerCase().includes(query) ||
                asset.serial_number.toLowerCase().includes(query) ||
                asset.assigned_employee.toLowerCase().includes(query);

            // Status filter
            let matchesStatus = true;
            if (statusFilter === 'online') {
                matchesStatus = asset.is_online;
            } else if (statusFilter === 'offline') {
                matchesStatus = !asset.is_online;
            }

            // Platform filter (for unified view)
            let matchesPlatform = true;
            if (!this.platformFilter) {
                matchesPlatform = asset.os_platform === this.state.activePlatform;
            }

            return matchesSearch && matchesStatus && matchesPlatform;
        });
    }

    /**
     * Switch the active platform tab
     */
    async switchPlatform(platform) {
        if (this.state.activePlatform === platform) return;
        
        this.state.activePlatform = platform;
        this.state.selectedAssets.clear();
        this.state.selectAll = false;
        
        // Reload assets for the new platform
        await this.loadAvailableAssets();
        this.filterAssets();
    }

    /**
     * Get icon class for platform
     */
    getPlatformIcon(platform) {
        switch (platform?.toLowerCase()) {
            case 'windows': return 'fa-windows';
            case 'linux': return 'fa-linux';
            case 'macos': return 'fa-apple';
            default: return 'fa-desktop';
        }
    }

    /**
     * Handle asset search input
     */
    onAssetSearchInput(ev) {
        this.state.assetSearchQuery = ev.target.value.toLowerCase();
        this.filterAssets();
    }

    /**
     * Handle asset status filter change
     */
    onAssetStatusFilterChange(ev) {
        this.state.assetStatusFilter = ev.target.value;
        this.filterAssets();
    }

    /**
     * Format date for display
     */
    formatDate(dateStr) {
        if (!dateStr) return "";
        const date = new Date(dateStr);
        return date.toLocaleDateString("en-US", {
            month: "short",
            day: "numeric",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
        });
    }

    /**
     * Format datetime for display (more compact)
     */
    formatDateTime(dateStr) {
        if (!dateStr) return "Never";
        const date = new Date(dateStr);
        const now = new Date();
        const diffMs = now - date;
        const diffMinutes = Math.floor(diffMs / (1000 * 60));
        const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
        const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

        if (diffMinutes < 1) return "Just now";
        if (diffMinutes < 60) return `${diffMinutes}m ago`;
        if (diffHours < 24) return `${diffHours}h ago`;
        if (diffDays < 7) return `${diffDays}d ago`;
        
        return date.toLocaleDateString("en-US", {
            month: "short",
            day: "numeric",
        });
    }

    /**
     * Return a platform-specific label for the deployment wizard dialog title.
     */
    _getWizardTitle(actionType = 'install') {
        const platform = this.platformFilter;
        if (actionType === 'uninstall') {
            const titles = {
                windows: 'Uninstall Windows Application',
                linux:   'Uninstall Linux Application',
                macos:   'Uninstall macOS Application',
            };
            return titles[platform] || 'Uninstall Application';
        }
        const titles = {
            windows: 'Deploy Windows Application',
            linux:   'Deploy Linux Application',
            macos:   'Deploy macOS Application',
        };
        return titles[platform] || 'Deploy Application';
    }

    /**
     * Build an inline action object so the dialog title can be set dynamically.
     */
    _buildWizardAction(extraContext = {}) {
        const actionType = extraContext.default_action_type || 'install';
        const context = {};
        if (this.platformFilter) {
            context.default_platform = this.platformFilter;
        }
        return {
            type: 'ir.actions.act_window',
            name: this._getWizardTitle(actionType),
            res_model: 'asset_management.app_deployment_wizard',
            view_mode: 'form',
            views: [[false, 'form']],
            target: 'new',
            context: { ...context, ...extraContext },
        };
    }

    /**
     * Open deployment wizard
     */
    openDeploymentWizard() {
        this.action.doAction(this._buildWizardAction());
    }

    /**
     * Open asset details
     */
    openAsset(assetId) {
        this.action.doAction("asset_management.action_asset_asset", {
            values: {
                res_id: assetId,
                viewMode: "form",
            },
        });
    }

    /**
     * Deploy to asset from the assets list
     */
    deployToAsset(assetId) {
        this.action.doAction(this._buildWizardAction({ default_device_id: assetId }));
    }

    /**
     * Open installed applications list filtered by asset
     */
    openInstalledApps(assetId, assetName) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `Installed Applications — ${assetName}`,
            res_model: "asset.installed.application",
            view_mode: "tree",
            views: [[false, "list"]],
            domain: [["asset_id", "=", assetId]],
            context: { default_asset_id: assetId },
            target: "current",
        });
    }

    /**
     * Handle image load error - hide broken image to show fallback icon
     */
    onImageError(ev) {
        // Hide the broken image, the fallback icon will show through
        const img = ev.target;
        if (img && img.style) {
            img.style.display = 'none';
        }
    }

    /**
     * Refresh dashboard data manually
     */
    async refreshDashboard() {
        await this.loadDashboardData();
        this.notification.add("Dashboard refreshed", { type: "success" });
    }

    /**
     * Open the deployment list view filtered by the clicked stat card status.
     * @param {string} status  - 'pending' | 'in_progress' | 'success' | 'failed'
     */
    onStatCardClick(status) {
        const labels = {
            pending:     'Pending Deployments',
            in_progress: 'In-Progress Deployments',
            success:     'Succeeded Deployments',
            failed:      'Failed Deployments',
        };

        // Base domain: filter by status
        const domain = [['status', '=', status]];

        this.action.doAction({
            type: 'ir.actions.act_window',
            name: labels[status] || 'Deployments',
            res_model: 'asset_management.app_deployment',
            view_mode: 'kanban,tree,form',
            views: [[false, 'kanban'], [false, 'list'], [false, 'form']],
            domain: domain,
            target: 'current',
        });
    }

    /**
     * Toggle selection of a single asset
     */
    toggleAssetSelection(assetId) {
        const selected = this.state.selectedAssets;
        if (selected.has(assetId)) {
            selected.delete(assetId);
        } else {
            selected.add(assetId);
        }
        // Update select all state based on current filtered assets
        this.updateSelectAllState();
    }

    /**
     * Toggle select all for filtered assets
     */
    toggleSelectAll() {
        if (this.state.selectAll) {
            // Deselect all
            this.state.selectedAssets.clear();
            this.state.selectAll = false;
        } else {
            // Select all filtered assets
            this.state.filteredAssets.forEach(asset => {
                this.state.selectedAssets.add(asset.id);
            });
            this.state.selectAll = true;
        }
    }

    /**
     * Update select all checkbox state based on current selection
     */
    updateSelectAllState() {
        const selectedCount = this.state.selectedAssets.size;
        const filteredCount = this.state.filteredAssets.length;
        
        // Select all is checked only if all filtered assets are selected
        this.state.selectAll = filteredCount > 0 && selectedCount === filteredCount;
    }

    /**
     * Get count of selected assets
     */
    getSelectedCount() {
        return this.state.selectedAssets.size;
    }

    /**
     * Check if an asset is selected
     */
    isAssetSelected(assetId) {
        return this.state.selectedAssets.has(assetId);
    }

    /**
     * Deploy to multiple selected assets
     */
    deployToSelectedAssets() {
        const selectedCount = this.state.selectedAssets.size;

        if (selectedCount === 0) {
            this.notification.add("Please select at least one asset", { type: "warning" });
            return;
        }

        // Convert Set to Array for context
        const selectedAssetIds = Array.from(this.state.selectedAssets);

        // Open wizard with pre-selected device (first selected asset)
        this.action.doAction(this._buildWizardAction({
            default_device_id: selectedAssetIds[0],
            default_selected_asset_ids: selectedAssetIds,
        }));

        this.notification.add(`Ready to deploy to ${selectedCount} selected asset(s)`, { type: "success" });
    }

    /**
     * Uninstall from multiple selected assets
     */
    uninstallFromSelectedAssets() {
        const selectedCount = this.state.selectedAssets.size;

        if (selectedCount === 0) {
            this.notification.add("Please select at least one asset", { type: "warning" });
            return;
        }

        const selectedAssetIds = Array.from(this.state.selectedAssets);

        this.action.doAction(this._buildWizardAction({
            default_action_type: 'uninstall',
            default_device_id: selectedAssetIds[0],
            default_selected_asset_ids: selectedAssetIds,
        }));

        this.notification.add(`Ready to uninstall from ${selectedCount} selected asset(s)`, { type: "warning" });
    }

    /**
     * Clear all selections
     */
    clearSelection() {
        this.state.selectedAssets.clear();
        this.state.selectAll = false;
    }
}

// Register the dashboard component
registry.category("actions").add("asset_management.app_deployment_dashboard", AppDeploymentDashboard);
