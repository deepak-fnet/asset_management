# Core models (always load - Community compatible)
from . import asset_agent  # Enterprise Agent Identity (TASK 2)
from . import asset_agent_log
from . import asset_asset
from . import asset_camera
from . import asset_category
from . import asset_dashboard
from . import asset_extra
from . import asset_file_access
from . import asset_installed_application
from . import asset_live_monitoring
from . import asset_storage_volume

from . import hr_employee

# Advanced features (Community compatible)
from . import asset_bulk_operations
from . import asset_comparison
from . import asset_network_device
from . import camera_monitor
from . import network_device_interface
from . import snmp_monitor
from . import maintenance_request
from . import repair_management
from . import asset_lifecycle

# OS Update Management
from . import asset_windows_update
from . import asset_linux_update
from . import asset_macos_update

# Antivirus Management (merged)
from . import antivirus
from . import antivirus_ksc
from . import antivirus_deployment
from . import ksc_service

# Software Deployment Management
from . import asset_software_catalog
from . import asset_software_deployment

# Network Auto Discovery
from . import network_discovery

# App Deployment (Package Manager Based - NEW)
from . import app_deployment

# Legacy Software Deployment (kept for backward compatibility)
from . import asset_software_catalog
from . import asset_software_deployment


#ASSET REQUEST
from . import asset_request
from . import purchase_order
from . import asset_list
from . import spare_parts

# Remote Assistance for assets
from . import asset_remote_session
from . import asset_remote_history
from . import asset_asset_remote
from . import asset_update_policy
from . import asset_win11_eligibility
from . import spare_product
from . import spare_request

from . import asset_category_peripheral