{
    "name": "Asset Management Pro",
    "version": "19.0.1.0.0",
    "summary": "Enterprise Asset Management with AI-Powered Agent Monitoring",
    "description": """
Professional Enterprise Asset Management System

✨ CORE FEATURES (Community Edition Compatible):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📡 Agent Monitoring
- Automated laptop monitoring via Python agent
- Real-time hardware/software change detection
- Agent health monitoring and alerts
- Auto-sync every 10 minutes

📊 Advanced Dashboard
- Real-time KPI updates with auto-refresh
- Interactive charts (Chart.js powered)
- Change alerts and recent activity feed
- Agent status visualization

🔧 Asset Management
- Complete lifecycle management
- QR code generation
- Warranty tracking with expiry alerts
- Bulk operations (assign, update, export)
- Asset comparison tool (side-by-side specs)

🚀 ENTERPRISE FEATURES (Auto-enabled):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
👥 HR Integration
- Employee asset assignment
- Contract integration

📧 Notifications
- Email alerts for assignments
- Warranty expiry warnings
- Change notifications

🔒 File Access Control
- Monitor Desktop/Documents/Downloads
- Block specific files/folders
- Track access violations
- Real-time enforcement

🎯 KEY HIGHLIGHTS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ Works on Odoo Community Edition
✓ Auto-enables Enterprise features when available
✓ Professional UI/UX with modern design
✓ Mobile responsive
✓ Export to Excel/CSV
✓ Bulk operations support
✓ Advanced filtering and search
✓ Real-time updates (30s refresh)
✓ Change history logging
""",
    "category": "Operations/Asset Management",
    "author": "Your Company",
    "website": "https://www.yourcompany.com",
    "license": "LGPL-3",

    "application": True,
    "installable": True,
    "auto_install": False,
    "sequence": 10,

    # CORE DEPENDENCIES - Community Edition compatible
    "depends": [
        "base",
        "web",
        "mail",
        "purchase",
        "hr",  # Community version available
        "stock",
        "purchase_stock",
        "maintenance",
    ],

    # DATA FILES - Core + Advanced Features
    "data": [
        # ==========================================
        # SECURITY (MUST BE FIRST)
        # ==========================================
        "security/asset_security_groups.xml",
        "security/ir.model.access.csv",
        "security/asset_remote_security.xml",

        # ==========================================
        # CORE DATA
        # ==========================================
        "data/asset_config_parameters.xml",
        "data/asset_sequence.xml",
        "data/asset_remote_data.xml",
        "data/asset_code_server_action.xml",
        "data/asset_maintenance_sequence.xml",
        "data/repair_management_sequence.xml",
        "data/asset_camera_sequence.xml",
        "data/asset_lifecycle_sequence.xml",
        "data/asset_agent_cron.xml",
        "data/asset_category_data.xml",
        "data/camera_cron.xml",
        "data/network_device_cron.xml",
        "data/network_discovery_cron.xml",
        "data/asset_request.xml",
        "data/asset_update_policy_cron.xml",
        "data/asset_update_policy_demo.xml",

        # ==========================================
        # CORE VIEWS (ACTIONS MUST BE BEFORE MENUS)
        # ==========================================
        "views/asset_network_device_views.xml",
        "views/asset_network_discovery_views.xml",
        "views/asset_asset_views.xml",
        "views/asset_asset_form_remote.xml",
        "views/asset_camera_views.xml",
        "views/asset_category_views.xml",
        "views/asset_peripheral_views.xml",
        "views/asset_extra_views.xml",
        "views/hr_employee_views.xml",
        "views/asset_audit_log_views.xml",
        "views/asset_asset_extended_views.xml",
        "views/asset_installed_application_views.xml",
        "views/asset_storage_volume_views.xml",
        "views/asset_agent_log_views.xml",
        "views/asset_live_monitoring_views.xml",

        "views/asset_dashboard_action.xml",
        "views/system_overview_action.xml",
        "views/cctv_dashboard_action.xml",
        "views/device_monitoring_action.xml",
        "views/network_device_dashboard_action.xml",

        # ==========================================
        # ADVANCED FEATURES (BEFORE MENUS)
        # ==========================================
        "data/asset_mail_templates.xml",
        "views/asset_bulk_operations_views.xml",
        "views/asset_comparison_views.xml",
        "views/asset_windows_update_views.xml",
        "views/asset_linux_update_views.xml",
        "views/asset_macos_update_views.xml",
        "views/asset_file_access_views.xml",
        "views/antivirus.xml",
        "views/antivirus_ksc_view.xml",
        "views/asset_update_policy_views.xml",
    "views/asset_win11_views.xml",

        # ==========================================
        # SOFTWARE DEPLOYMENT (BOTH OLD AND NEW)
        # ==========================================
        "views/asset_software_catalog_views.xml",
        "views/asset_software_deployment_views.xml",
        "views/asset_software_dashboard_action.xml",
        "views/app_deployment_views.xml",

        # ==========================================
        # WIZARDS
        # ==========================================
        "views/all_assets_wizard_views.xml",
        "views/offline_assets_wizard_views.xml",
        "views/online_assets_wizard_views.xml",
        "views/alerts_wizard_views.xml",
        "wizard/deploy_software_wizard_views.xml",

        # ==========================================
        # REPAIR MANAGEMENT
        # ==========================================
        "views/repair_management_views.xml",
        "views/asset_button_access_views.xml",
        "views/asset_lifecycle_views.xml",
        "wizard/joining_availability_wizard_views.xml",
        "wizard/lifecycle_wizard_views.xml",

        # ==========================================
        # MENUS (MUST BE LAST)
        "views/asset_menu.xml",
        "views/spare_request_view.xml",
        "views/asset_remote_session_views.xml",

        # ==========================================
        # ASSET REQUEST
        # ==========================================
        "views/asset_request_views.xml",
        # "views/purchase_order_custom_views.xml",
        "views/asset_list_views.xml",
        "views/asset_request_menus.xml",
    "views/asset_patch_menus.xml",
    ],

    # ASSETS - JavaScript/CSS (Odoo 17 Compatible)
    "assets": {
        "web.assets_backend": [
            # Chart.js library
            "asset_management/static/lib/chartjs/chart.umd.min.js",

            # Leaflet.js library for maps
            "asset_management/static/lib/leaflet/leaflet.css",
            "asset_management/static/lib/leaflet/leaflet.js",

            # Dashboard components
            "asset_management/static/src/xml/asset_dashboard.xml",
            "asset_management/static/src/js/asset_dashboard.js",

            # System Overview (Home) components
            "asset_management/static/src/xml/system_overview.xml",
            "asset_management/static/src/js/system_overview.js",

            # Asset List Dashboard (New)
            "asset_management/static/src/xml/asset_list_dashboard.xml",
            "asset_management/static/src/js/asset_list_dashboard.js",

            # Asset Map Widget (OWL Component)
            "asset_management/static/src/xml/asset_map_widget.xml",
            "asset_management/static/src/js/asset_map_widget.js",

            # macOS Dashboard components (Dedicated)
            "asset_management/static/src/xml/macos_dashboard.xml",
            "asset_management/static/src/js/macos_dashboard.js",

            # Device Monitoring components
            "asset_management/static/src/xml/device_monitoring_dashboard.xml",
            "asset_management/static/src/js/device_monitoring_dashboard.js",

            # CCTV Dashboard components
            "asset_management/static/src/xml/cctv_dashboard.xml",
            "asset_management/static/src/js/cctv_dashboard.js",

            # Network Dashboard (Overview) components
            "asset_management/static/src/xml/network_dashboard.xml",
            "asset_management/static/src/js/network_dashboard.js",

            # Network Live Monitoring components
            "asset_management/static/src/xml/network_device_dashboard.xml",
            "asset_management/static/src/js/network_device_dashboard.js",

            # Windows Update Widget components
            "asset_management/static/src/xml/windows_update_widget.xml",
            "asset_management/static/src/js/windows_update_widget.js",

            # Linux Update Widget components
            "asset_management/static/src/xml/linux_update_widget.xml",
            "asset_management/static/src/js/linux_update_widget.js",

            # macOS Update Widget components
            "asset_management/static/src/xml/macos_update_widget.xml",
            "asset_management/static/src/js/macos_update_widget.js",

            # Antivirus Dashboard components (merged)
            "asset_management/static/src/xml/antivirus.xml",
            "asset_management/static/src/js/antivirus.js",
            "asset_management/static/src/js/browse_file_widget.js",

            # Windows Antivirus Dashboard (New - Windows-style UI)
            "asset_management/static/src/xml/windows_antivirus_dashboard.xml",
            "asset_management/static/src/js/windows_antivirus_dashboard.js",
            "asset_management/static/src/css/windows_antivirus_dashboard.css",

            # Linux Antivirus Dashboard
            "asset_management/static/src/xml/linux_antivirus_dashboard.xml",
            "asset_management/static/src/js/linux_antivirus_dashboard.js",
            "asset_management/static/src/css/linux_antivirus_dashboard.css",

            # macOS Antivirus Dashboard
            "asset_management/static/src/xml/macos_antivirus_dashboard.xml",
            "asset_management/static/src/js/macos_antivirus_dashboard.js",
            "asset_management/static/src/css/macos_antivirus_dashboard.css",

            # File Access Widget (OWL Component)
            "asset_management/static/src/css/file_access_widget.css",
            "asset_management/static/src/xml/file_access_widget.xml",
            "asset_management/static/src/js/file_access_widget.js",

            # App Deployment (Package Manager Based - NEW)
            "asset_management/static/src/css/app_deployment.css",
            "asset_management/static/src/xml/app_deployment.xml",
            "asset_management/static/src/js/app_deployment.js",

            # Software Deployment Dashboard (LEGACY)
            "asset_management/static/src/css/software_dashboard.css",
            "asset_management/static/src/xml/software_dashboard.xml",
            "asset_management/static/src/js/software_dashboard.js",

            # Stylesheets
            "asset_management/static/src/css/asset_map.css",
            "asset_management/static/src/css/asset_dashboard.css",
            "asset_management/static/src/css/system_overview.css",
            "asset_management/static/src/css/asset_list_dashboard.css",
            "asset_management/static/src/css/asset_category.css",
            "asset_management/static/src/css/asset_camera_kanban.css",
            "asset_management/static/src/css/device_monitoring_dashboard.css",
            "asset_management/static/src/css/cctv_dashboard.css",
            "asset_management/static/src/css/network_dashboard.css",
            "asset_management/static/src/css/network_device_dashboard.css",
            "asset_management/static/src/css/macos_dashboard.css",
            "asset_management/static/src/css/asset_cards_enhanced.css",
            "asset_management/static/src/css/windows_update_widget.css",
            "asset_management/static/src/css/antivirus.css",
        ],
    },

    "external_dependencies": {
        "python": [
            "qrcode",
            "xlsxwriter",
            "requests",
        ],
    },

    "post_init_hook": "post_init_hook",

    "demo": [],

    "images": [
        "static/description/banner.png",
        "static/description/dashboard.png",
        "static/description/assets.png",
    ],

    "support": "support@yourcompany.com",
    "price": 199.00,
    "currency": "USD",
}