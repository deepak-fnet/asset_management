/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, onWillStart } from "@odoo/owl";

class SoftwareDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        
        this.state = useState({
            stats: {
                total_software: 0,
                total_deployments: 0,
                successful: 0,
                failed: 0,
                pending: 0,
                success_rate: 0
            },
            recent_deployments: [],
            top_software: [],
            loading: true
        });
        
        onWillStart(async () => {
            await this.loadDashboardData();
        });
    }
    
    async loadDashboardData() {
        try {
            // Load statistics
            const softwareCount = await this.orm.searchCount("asset.software.catalog", []);
            const deployments = await this.orm.searchRead("asset.software.deployment", [], [
                "id", "status", "software_id", "device_name", "deployed_date", "completed_date"
            ], 10, "deployed_date desc");

            // Calculate stats
            const successful = deployments.filter(d => d.status === "installed").length;
            const failed = deployments.filter(d => d.status === "failed").length;
            const pending = deployments.filter(d => d.status === "pending").length;
            const inProgress = deployments.filter(d => ["downloading", "installing"].includes(d.status)).length;

            // Get top software - fetch all and calculate stats client-side
            const allSoftware = await this.orm.searchRead("asset.software.catalog", [], [
                "id", "name", "version"
            ], 0, "name asc");

            // Calculate deployment counts for each software
            const topSoftware = await Promise.all(allSoftware.map(async (software) => {
                const deps = await this.orm.searchRead("asset.software.deployment", [
                    ["software_id", "=", software.id]
                ], ["status"]);
                
                const deploymentCount = deps.length;
                const successCount = deps.filter(d => d.status === "installed").length;
                const successRate = deploymentCount > 0 
                    ? Math.round((successCount / deploymentCount) * 100) 
                    : 0;
                
                return {
                    ...software,
                    deployment_count: deploymentCount,
                    success_rate: successRate
                };
            }));

            // Sort by deployment count and take top 5
            topSoftware.sort((a, b) => b.deployment_count - a.deployment_count);
            const top5Software = topSoftware.slice(0, 5);

            this.state.stats = {
                total_software: softwareCount,
                total_deployments: deployments.length,
                successful,
                failed,
                pending: pending + inProgress,
                success_rate: deployments.length > 0 ? Math.round((successful / deployments.length) * 100) : 0
            };

            this.state.recent_deployments = deployments.map(d => ({
                ...d,
                status_class: this.getStatusClass(d.status),
                status_icon: this.getStatusIcon(d.status),
                deployed_date: d.deployed_date ? new Date(d.deployed_date).toLocaleDateString() : "N/A"
            }));

            this.state.top_software = top5Software;
        } catch (error) {
            console.error("Error loading dashboard data:", error);
            this.notification.add("Error loading dashboard data", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }
    
    getStatusClass(status) {
        const classes = {
            "installed": "badge bg-success",
            "failed": "badge bg-danger",
            "pending": "badge bg-warning",
            "downloading": "badge bg-info",
            "installing": "badge bg-info"
        };
        return classes[status] || "badge bg-secondary";
    }
    
    getStatusIcon(status) {
        const icons = {
            "installed": "fa-check",
            "failed": "fa-times",
            "pending": "fa-clock-o",
            "downloading": "fa-download",
            "installing": "fa-cog"
        };
        return icons[status] || "fa-question";
    }
    
    async refreshDashboard() {
        this.state.loading = true;
        await this.loadDashboardData();
    }
    
    openSoftwareLibrary() {
        this.action.doAction("asset_management.action_asset_software_catalog_enhanced");
    }
    
    openDeployments() {
        this.action.doAction("asset_management.action_asset_software_deployment_enhanced");
    }
    
    openDeploymentWizard() {
        this.action.doAction("asset_management.action_deploy_software_wizard");
    }
}

SoftwareDashboard.template = "asset_management.SoftwareDashboard";
SoftwareDashboard.props = {};

registry.category("actions").add("asset_software_dashboard", SoftwareDashboard);
