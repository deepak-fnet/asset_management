from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import json
import logging

_logger = logging.getLogger(__name__)


def normalize_coordinate(value):
    """
    Safely normalize a coordinate value to float.
    Handles strings, None, empty strings, and invalid values.

    Parameters:
    -----------
    value : any
        The coordinate value (can be string, float, int, None, etc.)

    Returns:
    --------
    float or None
        The normalized float value, or None if invalid/empty
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value != 0 else 0.0
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
    return None


def has_valid_coordinates(lat, lon):
    """
    Check if coordinates are valid (non-None and non-zero).

    Parameters:
    -----------
    lat : float or None
        Latitude value (already normalized)
    lon : float or None
        Longitude value (already normalized)

    Returns:
    --------
    bool
        True if both coordinates are valid and non-zero
    """
    if lat is None or lon is None:
        return False
    # Both must be non-zero (0,0 is in the ocean - not a valid IP geolocation)
    return lat != 0.0 or lon != 0.0


def _version_change_direction(old_version, new_version):
    """"upgrade"/"downgrade"/"changed" for a software history description.

    Best-effort: package versions are not always dot-separated numbers
    (e.g. "9ubuntu6.5", "1:2.39.3-9ubuntu6.5") - falls back to a neutral
    "changed" whenever the two cannot be meaningfully compared, rather than
    guessing wrong.
    """
    def _key(v):
        return tuple(int(p) for p in v.replace('-', '.').split('.')
                     if p.isdigit())
    try:
        old_key, new_key = _key(old_version), _key(new_version)
        if old_key and new_key:
            if new_key > old_key:
                return "upgrade"
            if new_key < old_key:
                return "downgrade"
    except (ValueError, AttributeError):
        pass
    return "changed"


class AssetAsset(models.Model):
    _name = "asset.asset"
    _description = "Asset"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"
    _rec_name = "asset_name"

    # =====================
    # BASIC ASSET INFO
    # =====================

    asset_name = fields.Char(
        string="Asset Name",
        required=True,
        tracking=True
    )

    asset_code = fields.Char(
        string="Asset Code",
        readonly=True,
        copy=False,
        index=True
    )

    image_1920 = fields.Image(
        string="Asset Image",
        max_width=1920,
        max_height=1920
    )

    category_id = fields.Many2one(
        "asset.category",
        string="Category",
        ondelete="restrict",
        tracking=True
    )

    assigned_employee_id = fields.Many2one(
        "hr.employee",
        string="Assigned Employee",
        tracking=True
    )

    is_general_asset = fields.Boolean()

    # =====================
    # PROCUREMENT INFORMATION
    # =====================

    acquisition_type = fields.Selection(
        [
            ("purchased", "Purchased"),
            ("virtual", "Virtual"),
            ("cloud", "Cloud"),
            ("rented", "Rented"),
            ("internal", "Internal"),
        ],
        string="Acquisition Type",
        tracking=True,
        help="How the asset was acquired"
    )

    purchase_date = fields.Date(
        string="Purchase Date",
        tracking=True
    )

    in_service_date = fields.Date(
        string="In Service Date",
        tracking=True,
        help="Date when the asset was put into service. Defaults to purchase date."
    )

    purchase_cost = fields.Monetary(
        string="Purchase Cost",
        tracking=True,
        currency_field="currency_id",
        help="Original purchase cost of the asset"
    )

    # Keep legacy field for backward compatibility
    purchase_value = fields.Monetary(
        string="Purchase Value",
        tracking=True
    )

    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id
    )

    # Vendor from res.partner (separate from asset.vendor for AMC)
    procurement_vendor_id = fields.Many2one(
        "res.partner",
        string="Purchase Vendor",
        tracking=True,
        help="Vendor from whom the asset was purchased"
    )

    invoice_ref = fields.Char(
        string="Invoice Reference",
        tracking=True,
        help="Reference number of the purchase invoice"
    )

    # =====================
    # WORKFLOW STATE
    # =====================

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("submit", "Submit"),
            ("assigned", "Assigned"),
            ("maintenance", "Maintenance"),
            ("scrapped", "Scrapped"),
        ],
        default="draft",
        tracking=True
    )

    # =====================
    # HEALTH SCORE SYSTEM
    # =====================

    health_score = fields.Integer(
        string="Health Score",
        compute="_compute_health_score",
        store=True,
        help="Overall asset health (0-100)"
    )

    health_status = fields.Selection(
        [
            ("excellent", "Excellent"),
            ("good", "Good"),
            ("fair", "Fair"),
            ("poor", "Poor"),
            ("critical", "Critical"),
        ],
        string="Health Status",
        compute="_compute_health_score",
        store=True
    )

    # =====================
    # HR INTEGRATION
    # =====================

    department_id = fields.Many2one(
        related="assigned_employee_id.department_id",
        string="Department",
        store=True,
        readonly=True
    )

    job_id = fields.Many2one(
        related="assigned_employee_id.job_id",
        string="Job Position",
        store=True,
        readonly=True
    )

    assignment_date = fields.Date(
        string="Assignment Date",
        tracking=True,
        help="Date when asset was assigned to employee"
    )

    expected_return_date = fields.Date(
        string="Expected Return Date",
        tracking=True,
        help="Expected date for asset return"
    )

    # =====================
    # WARRANTY & MAINTENANCE
    # =====================

    vendor_id = fields.Many2one(
        "asset.vendor",
        string="Vendor / AMC Provider",
        tracking=True
    )

    warranty_period_months = fields.Integer(
        string="Warranty Period (Months)",
        tracking=True,
        help="Warranty duration in months from purchase date"
    )

    # Computed warranty start: equals purchase_date
    warranty_start_date = fields.Date(
        string="Warranty Start",
        compute="_compute_warranty_dates",
        store=True,
        readonly=False,
        tracking=True,
        help="Warranty start date. Computed from purchase date, but can be manually overridden."
    )

    # Computed warranty end: purchase_date + warranty_period_months
    warranty_end_date = fields.Date(
        string="Warranty End",
        compute="_compute_warranty_dates",
        store=True,
        readonly=False,
        tracking=True,
        help="Warranty end date. Computed from purchase date + warranty period, but can be manually overridden."
    )

    amc_expiry_date = fields.Date(
        string="AMC Expiry Date",
        tracking=True
    )

    warranty_status = fields.Selection(
        [
            ("active", "Active"),
            ("expired", "Expired"),
            ("no_warranty", "No Warranty"),
            ("not_applicable", "Not Applicable"),
        ],
        string="Warranty Status",
        compute="_compute_warranty_status",
        store=True,
        help="Computed warranty status based on dates and acquisition type"
    )

    # Helper field to check if asset is virtual/cloud
    is_virtual_asset = fields.Boolean(
        string="Is Virtual Asset",
        compute="_compute_is_virtual_asset",
        store=True,
        help="True if acquisition type is virtual or cloud"
    )

    next_maintenance_date = fields.Date(
        string="Next Maintenance",
        tracking=True
    )
    maintenance_equipment_id = fields.Many2one(
        'maintenance.equipment',
        string='Maintenance Equipment',
        ondelete='set null'
    )
    maintenance_request_ids = fields.One2many(
        'maintenance.request',
        'asset_id',
        string='Maintenance History',
        readonly=True
    )

    last_maintenance_date = fields.Date(
        string="Last Maintenance",
        tracking=True
    )

    maintenance_type = fields.Selection(
        [
            ("preventive", "Preventive"),
            ("corrective", "Corrective"),
        ],
        string="Maintenance Type",
        tracking=True
    )

    maintenance_status = fields.Selection(
        [
            ("pending", "Pending"),
            ("in_progress", "In Progress"),
            ("completed", "Completed"),
        ],
        string="Maintenance Status",
        tracking=True
    )

    maintenance_reason = fields.Char(
        string="Maintenance Reason",
        tracking=True
    )

    amc_status = fields.Selection(
        [
            ("yes", "Yes"),
            ("no", "No"),
        ],
        string="AMC Status",
        default="no",
        tracking=True
    )

    maintenance_ids = fields.One2many(
        "asset.maintenance",
        "asset_id",
        string="Maintenance History"
    )

    repair_management_ids = fields.One2many(
        "repair.management",
        "asset_id",
        string="Repair Requests"
    )
    repair_count = fields.Integer(
        string="Repair Count", compute="_compute_repair_count",
    )

    def _compute_repair_count(self):
        for asset in self:
            asset.repair_count = len(asset.repair_management_ids)

    def action_view_repairs(self):
        self.ensure_one()
        return {
            'name': _('Repair Requests'),
            'type': 'ir.actions.act_window',
            'res_model': 'repair.management',
            'view_mode': 'list,form',
            'domain': [('asset_id', '=', self.id)],
            'context': {'default_asset_id': self.id},
        }

    def action_create_os_upgrade_request(self):
        """Open a new OS upgrade request, pre-filled from what the agent found.

        Saves retyping the target release (and getting the codename wrong -
        the agent acts on target_codename, so a typo there means the upgrade
        silently never matches anything).
        """
        self.ensure_one()
        if not self.os_upgrade_available_codename:
            raise UserError(_(
                "No next release has been reported for this asset yet. The "
                "agent fills this in on its next release-upgrade check."))
        return {
            'name': _('OS Upgrade Request'),
            'type': 'ir.actions.act_window',
            'res_model': 'asset.os.upgrade.request',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_asset_id': self.id,
                'default_target_version': self.os_upgrade_available_version or '',
                'default_target_codename': self.os_upgrade_available_codename,
            },
        }

    def action_create_win11_upgrade_request(self):
        """Open a new OS upgrade request pre-filled for Windows 10 -> 11.

        Unlike the Linux path, there is no "next release" to detect - it is
        always just "11". What actually gates this is win11_status, which
        needs the agent's TPM/Secure Boot/UEFI scan (win11_check.py) to ever
        reach 'eligible' - action_approve() re-checks this before letting
        the request move to Approved, so a stale/unscanned asset cannot be
        approved by mistake even if this button was clicked anyway.
        """
        self.ensure_one()
        if self.win11_status != 'eligible':
            raise UserError(_(
                "This asset is not confirmed eligible for Windows 11 (status: "
                "%s). Deploy the agent's Windows 11 eligibility check "
                "(TPM/Secure Boot/UEFI) first - without it this can never "
                "be approved."
            ) % dict(self._fields['win11_status'].selection).get(
                self.win11_status, self.win11_status))
        return {
            'name': _('Windows 11 Upgrade Request'),
            'type': 'ir.actions.act_window',
            'res_model': 'asset.os.upgrade.request',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_asset_id': self.id,
                'default_target_version': 'Windows 11',
                'default_target_codename': '11',
            },
        }

    # ── Lifecycle process counts & smart buttons ──────────────────────────

    assign_process_count = fields.Integer(
        string="Assign Processes",
        compute="_compute_lifecycle_counts",
    )

    def _compute_lifecycle_counts(self):
        for asset in self:
            asset.assign_process_count = self.env['asset.assign.process'].search_count([
                ('asset_id', '=', asset.id),
            ])

    def action_view_assign_processes(self):
        self.ensure_one()
        return {
            'name': _('Assign / Replace Processes'),
            'type': 'ir.actions.act_window',
            'res_model': 'asset.assign.process',
            'view_mode': 'list,form',
            'domain': [('asset_id', '=', self.id)],
            'context': {'default_asset_id': self.id},
        }

    def action_open_joining_process(self):
        """Navigate to joining processes for assets (by assigned employee)."""
        self.ensure_one()
        return {
            'name': _('Joining Process'),
            'type': 'ir.actions.act_window',
            'res_model': 'asset.joining.process',
            'view_mode': 'list,form',
            'domain': [('assigned_asset_ids', '=', self.id)],
        }

    def action_open_exit_process(self):
        """Navigate to exit process form for the current assigned employee."""
        self.ensure_one()
        if not self.assigned_employee_id:
            raise UserError(_("No employee is currently assigned to this asset."))
        return {
            'name': _('Create Exit Process'),
            'type': 'ir.actions.act_window',
            'res_model': 'asset.exit.process',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_employee_id': self.assigned_employee_id.id,
            },
        }

    # =====================
    # LOCATION & MOVEMENT
    # =====================

    location_id = fields.Many2one(
        "asset.location",
        string="Current Location",
        tracking=True
    )

    location_history_ids = fields.One2many(
        "asset.location.history",
        "asset_id",
        string="Location History"
    )

    # =====================
    # DOCUMENT FIELDS
    # =====================

    invoice_file = fields.Binary(string='Invoice Document')
    invoice_filename = fields.Char(string='Invoice Filename')
    warranty_file = fields.Binary(string='Warranty Document')
    warranty_filename = fields.Char(string='Warranty Filename')

    assignment_history_ids = fields.One2many(
        "asset.assignment.history",
        "asset_id",
        string="Assignment History"
    )

    # Excludes hardware_change/software_change - those are high-volume,
    # low-significance-per-row (every app install/update/remove on every
    # sync writes one) and already have their own dedicated tabs below.
    # Left in here too, this tab drowns the handful of things that actually
    # matter (status/assignment/location/security) in that noise - this was
    # sitting at ~1600 rows for a single asset before this filter.
    audit_log_ids = fields.One2many(
        "asset.audit.log", "asset_id", string="Audit Logs",
        domain=[("log_type", "not in", ("hardware_change", "software_change"))])

    # Separate fields (not the same audit_log_ids reused twice with a
    # view-level domain per tab) - a domain= on an embedded x2many <field>
    # does not reliably filter which already-linked rows are DISPLAYED when
    # the same field name appears more than once in one form (it showed the
    # full unfiltered log on both the Hardware Changes and Software Changes
    # tabs). Baking the domain into the field definition itself filters at
    # the ORM level instead, which is not subject to that display quirk.
    hardware_change_log_ids = fields.One2many(
        "asset.audit.log", "asset_id", string="Hardware Changes",
        domain=[("log_type", "=", "hardware_change")])
    software_change_log_ids = fields.One2many(
        "asset.audit.log", "asset_id", string="Software Changes",
        domain=[("log_type", "=", "software_change")])
    security_log_ids = fields.One2many(
        "asset.audit.log", "asset_id", string="Security Events",
        domain=[("log_type", "=", "security")])

    # =====================
    # QR CODE
    # =====================

    qr_code = fields.Binary(
        string="QR Code",
        compute="_compute_qr_code",
        store=True
    )

    # =====================
    # LOCATION (Agent-provided coordinates ONLY)
    # Latitude & longitude are the ONLY source of location truth.
    # NO reverse geocoding, NO IP re-lookup, NO guessing region/state/country.
    # Agent sends: latitude, longitude, location_source
    # =====================

    latitude = fields.Float(
        string="Latitude",
        digits=(10, 6),
        readonly=True,
        tracking=True,
        help="Latitude coordinate provided by device agent (stored AS-IS)"
    )
    longitude = fields.Float(
        string="Longitude",
        digits=(10, 6),
        readonly=True,
        tracking=True,
        help="Longitude coordinate provided by device agent (stored AS-IS)"
    )
    location_source = fields.Selection(
        [
            ("gps", "GPS"),
            ("ip", "IP-based"),
            ("unavailable", "Unavailable"),
        ],
        string="Location Source",
        default="unavailable",
        readonly=True,
        tracking=True,
        help="Source of location data: 'gps' (precise), 'ip' (approximate), 'unavailable' (no location)"
    )
    last_location_update = fields.Datetime(
        string="Last Location Update",
        readonly=True,
        tracking=True,
        help="Timestamp of last location update from agent"
    )

    # =====================
    # REVERSE GEOCODING FIELDS (Nominatim/OpenStreetMap)
    # =====================
    location_address = fields.Char(
        string="Location Address",
        readonly=True,
        help="Full address from reverse geocoding"
    )
    location_city = fields.Char(
        string="City",
        readonly=True,
        help="City/town from reverse geocoding"
    )
    location_state = fields.Char(
        string="State/Region",
        readonly=True,
        help="State or region from reverse geocoding"
    )
    location_country = fields.Char(
        string="Country",
        readonly=True,
        help="Country from reverse geocoding"
    )
    location_area = fields.Char(
        string="Area/Neighborhood",
        readonly=True,
        help="Suburb, neighborhood or area from reverse geocoding"
    )

    has_location_coordinates = fields.Boolean(
        string="Has Location Coordinates",
        compute="_compute_has_location_coordinates",
        store=False,
        help="True if valid coordinates exist (regardless of source)"
    )

    map_url = fields.Char(
        string="Map URL",
        compute="_compute_map_url",
        store=False,
        help="OpenStreetMap URL for visualizing coordinates"
    )

    @api.depends('latitude', 'longitude')
    def _compute_has_location_coordinates(self):
        """Check if asset has valid location coordinates."""
        for asset in self:
            # Has coordinates if at least one is non-zero
            asset.has_location_coordinates = (
                    asset.latitude != 0.0 or asset.longitude != 0.0
            )

    @api.depends('latitude', 'longitude')
    def _compute_map_url(self):
        """Generate OpenStreetMap URL from coordinates."""
        for asset in self:
            if asset.latitude != 0.0 or asset.longitude != 0.0:
                asset.map_url = f"https://www.openstreetmap.org/?mlat={asset.latitude}&mlon={asset.longitude}#map=15/{asset.latitude}/{asset.longitude}"
            else:
                asset.map_url = False

    # =====================
    # AGENT SYNC FIELDS
    # =====================

    network_device_ids = fields.One2many(
        "asset.network.device",
        "asset_id",
        string="Network Devices"
    )

    network_device_count = fields.Integer(
        string="Network Device Count",
        compute="_compute_network_device_count"
    )

    is_network_device = fields.Boolean(
        string="Is Network Device",
        compute="_compute_is_network_device",
        store=True
    )

    serial_number = fields.Char(
        string="Serial Number",
        required=True,
        index=True,
        copy=False,
        tracking=True,
        help="Unique hardware serial number from laptop agent"
    )

    hostname = fields.Char(
        string="Hostname",
        tracking=True
    )

    device_name = fields.Char(
        string="Device Name",
        tracking=True,
        help="Device model name from agent (e.g., Acer ALG AL15G-52)"
    )

    processor = fields.Char(
        string="Processor",
        tracking=True
    )

    graphics_card_raw = fields.Char(
        string="Graphics Card (Raw)",
        help="Raw data from agent",
        copy=False
    )

    graphics_card = fields.Char(
        string="Graphics Card",
        compute="_compute_graphics_card",
        inverse="_inverse_graphics_card",
        store=True,
        tracking=True
    )

    @api.depends("graphics_card_raw")
    def _compute_graphics_card(self):
        for asset in self:
            asset.graphics_card = asset.graphics_card_raw or "Not Available"

    def _inverse_graphics_card(self):
        for asset in self:
            asset.graphics_card_raw = asset.graphics_card if asset.graphics_card != "Not Available" else False

    os_type = fields.Char(
        string="OS Type",
        tracking=True
    )

    ram_size = fields.Float(
        string="RAM Size (GB)",
        tracking=True
    )

    rom_size = fields.Float(
        string="ROM Size (GB)",
        tracking=True
    )

    asset_type = fields.Selection(
        [
            ("laptop", "Laptop"),
            ("desktop", "Desktop"),
            ("server", "Server"),
            ("camera", "Camera"),
        ],
        string="Asset Type",
        tracking=True
    )

    condition = fields.Selection(
        [
            ("good", "Good"),
            ("fair", "Fair"),
            ("poor", "Poor"),
        ],
        string="Condition",
        tracking=True
    )

    mac_address = fields.Char(
        string="MAC Address",
        tracking=True
    )

    invoice_number = fields.Char(
        string="Invoice Number",
        tracking=True
    )

    amc_file = fields.Binary(string='AMC Document')
    amc_filename = fields.Char(string='AMC Filename')
    manual_file = fields.Binary(string='Manual Document')
    manual_filename = fields.Char(string='Manual Filename')

    os_name = fields.Char(
        string="OS Name",
        tracking=True
    )

    # Structured distro version, separate from the free-text os_name (which
    # can be anything the agent's platform library returns, e.g. "Linux").
    # Populated from /etc/os-release's VERSION_ID/VERSION_CODENAME on Linux -
    # this is what lets a Linux OS Upgrade request compare "what it's on" vs
    # "what it should move to" without parsing os_name's free text.
    os_version = fields.Char(
        string="OS Version",
        tracking=True,
        help="Structured distro version, e.g. '24.04.4 LTS (Noble Numbat)' "
             "on Ubuntu. Distinct from OS Name - reported separately by the "
             "agent from /etc/os-release (Linux) or the platform's own "
             "version API.",
    )
    os_codename = fields.Char(
        string="OS Codename",
        tracking=True,
        help="e.g. 'noble' for Ubuntu 24.04. Used to match the target "
             "release in an OS Upgrade request.",
    )
    os_upgrade_request_ids = fields.One2many(
        'asset.os.upgrade.request', 'asset_id', string='OS Upgrade Requests',
    )

    # ── Release-upgrade availability, reported by the agent ───────────────
    # Two distinct facts, deliberately kept as separate fields because they
    # answer different questions and routinely disagree:
    #
    #   os_upgrade_available      - does `do-release-upgrade -c` offer an
    #                               upgrade RIGHT NOW? This is the only
    #                               thing that decides whether running an
    #                               upgrade would actually do anything.
    #   os_upgrade_available_*    - the next release that EXISTS for this
    #                               machine per Ubuntu's meta-release data,
    #                               whether or not it is being offered yet.
    #
    # Ubuntu does not open the LTS-to-LTS path on release day (24.04 ->
    # 26.04 only opens around 26.04.1), so for months the honest answer is
    # "26.04.1 LTS exists, but is not offered yet". Reporting only the
    # boolean would hide that; reporting only the version would imply the
    # upgrade can be run today. Both are shown.
    os_upgrade_available = fields.Boolean(
        string="OS Upgrade Offered",
        readonly=True, tracking=True,
        help="True only when the agent's `do-release-upgrade -c` check "
             "actually offers a new release right now. A newer release "
             "existing is not the same thing - see OS Upgrade Check Output.",
    )
    os_upgrade_available_version = fields.Char(
        string="Next Release Version",
        readonly=True, tracking=True,
        help="Next distro release known to this machine, e.g. "
             "'26.04.1 LTS'. Populated even when the upgrade is not being "
             "offered yet, so the fleet's next hop is visible in advance.",
    )
    os_upgrade_available_codename = fields.Char(
        string="Next Release Codename",
        readonly=True, tracking=True,
        help="e.g. 'resolute'. This is what an OS Upgrade request's Target "
             "Codename must be set to.",
    )
    os_upgrade_check_output = fields.Text(
        string="OS Upgrade Check Output",
        readonly=True,
        help="Raw output of the agent's release-upgrade check - explains "
             "WHY an upgrade is or is not being offered.",
    )
    os_upgrade_checked_date = fields.Datetime(
        string="Upgrade Last Checked",
        readonly=True,
        help="When the agent last ran the release-upgrade check.",
    )

    platform = fields.Selection(
        [
            ("windows", "Windows"),
            ("linux", "Linux"),
            ("macos", "macOS"),
            ("unknown", "Unknown")
        ],
        string="Platform",
        tracking=True,
        index=True,
        help="Detected or normalized platform from agent"
    )

    os_platform = fields.Selection(
        [
            ("windows", "Windows"),
            ("linux", "Linux"),
            ("macos", "macOS"),
            ("unknown", "Unknown")
        ],
        string="OS Platform",
        compute="_compute_os_platform",
        store=True,
        index=True
    )

    @api.depends("os_name", "platform")
    def _compute_os_platform(self):
        linux_variants = ["ubuntu", "debian", "linux", "redhat", "centos", "fedora", "mint", "arch", "manjaro",
                          "opensuse", "kali", "alpine", "suse", "rhel", "oracle"]
        for asset in self:
            # First priority: compute from os_name
            if asset.os_name:
                os_name_lower = asset.os_name.lower()
                if "windows" in os_name_lower:
                    asset.os_platform = "windows"
                elif any(variant in os_name_lower for variant in linux_variants):
                    asset.os_platform = "linux"
                elif "mac" in os_name_lower or "darwin" in os_name_lower:
                    asset.os_platform = "macos"
                else:
                    # Fallback to platform field if os_name doesn't match known patterns
                    asset.os_platform = asset.platform or "unknown"
            else:
                # No os_name - use platform field as fallback
                asset.os_platform = asset.platform or "unknown"

    battery_capacity = fields.Float(
        string="Battery Percentage (%)",
        tracking=True,
        required=False
    )

    battery_mah = fields.Float(
        string="Battery Capacity (mAh)",
        tracking=True
    )

    # =====================
    # LIVE METRICS (COMPUTED FROM CACHE)
    # =====================
    cpu_usage = fields.Float(
        string="CPU Usage (%)",
        compute="_compute_live_metrics",
        store=False,
        digits=(5, 2),
        help="Real-time CPU usage from live monitoring cache"
    )
    memory_usage = fields.Float(
        string="Memory Usage (GB)",
        compute="_compute_live_metrics",
        store=False,
        digits=(10, 2),
        help="Real-time memory usage from live monitoring cache"
    )
    storage_usage = fields.Float(
        string="Storage Usage (GB)",
        compute="_compute_live_metrics",
        store=False,
        digits=(10, 2),
        help="Real-time storage usage from live monitoring cache"
    )
    battery_level = fields.Float(
        string="Battery Level (%)",
        compute="_compute_live_metrics",
        store=False,
        digits=(5, 2),
        help="Real-time battery level from live monitoring cache"
    )
    network_bandwidth = fields.Float(
        string="Network Bandwidth (Mbps)",
        compute="_compute_live_metrics",
        store=False,
        digits=(10, 2),
        help="Real-time network bandwidth from live monitoring cache"
    )
    battery_health = fields.Float(
        string="Battery Health (%)",
        compute="_compute_live_metrics",
        store=False,
        digits=(5, 2),
        help="Real-time battery health from live monitoring cache"
    )

    cpu_usage_percent = fields.Float(
        string="CPU Usage Percent",
        compute="_compute_live_metrics",
        store=False,
        digits=(5, 2),
        help="Real-time CPU usage percentage from live monitoring"
    )
    ram_usage_percent = fields.Float(
        string="RAM Usage Percent",
        compute="_compute_live_metrics",
        store=False,
        digits=(5, 2),
        help="Real-time RAM usage percentage from live monitoring"
    )
    disk_usage_percent = fields.Float(
        string="Disk Usage Percent",
        compute="_compute_live_metrics",
        store=False,
        digits=(5, 2),
        help="Real-time disk usage percentage from live monitoring"
    )
    network_upload_mbps = fields.Float(
        string="Network Upload (Mbps)",
        compute="_compute_live_metrics",
        store=False,
        digits=(10, 2),
        help="Real-time network upload speed from live monitoring"
    )
    network_download_mbps = fields.Float(
        string="Network Download (Mbps)",
        compute="_compute_live_metrics",
        store=False,
        digits=(10, 2),
        help="Real-time network download speed from live monitoring"
    )
    battery_percentage = fields.Float(
        string="Battery Percentage",
        compute="_compute_live_metrics",
        store=False,
        digits=(5, 2),
        required=False,
        help="Real-time battery percentage from live monitoring"
    )
    heartbeat = fields.Datetime(
        string="Heartbeat",
        compute="_compute_live_metrics",
        store=False,
        help="Last heartbeat timestamp from live monitoring"
    )
    is_online = fields.Boolean(
        string="Is Online",
        compute="_compute_live_metrics",
        store=False,
        help="Device online status based on recent heartbeat"
    )

    cpu_trend = fields.Float(string="CPU Trend", compute="_compute_metric_trends", store=True)
    memory_trend = fields.Float(string="Memory Trend", compute="_compute_metric_trends", store=True)
    storage_trend = fields.Float(string="Storage Trend", compute="_compute_metric_trends", store=True)
    battery_trend = fields.Float(string="Battery Trend", compute="_compute_metric_trends", store=True)
    network_trend = fields.Float(string="Network Trend", compute="_compute_metric_trends", store=True)
    battery_health_trend = fields.Float(string="Battery Health Trend", compute="_compute_metric_trends", store=True)

    disk_type = fields.Char(
        string="Disk Type",
        tracking=True
    )

    # =====================
    # STORAGE VOLUMES
    # =====================

    storage_volume_ids = fields.One2many(
        "asset.storage.volume",
        "asset_id",
        string="Storage & File Control"
    )

    storage_volume_count = fields.Integer(
        string="Volume Count",
        compute="_compute_storage_volume_count"
    )

    total_storage_size = fields.Float(
        string="Total Storage (GB)",
        compute="_compute_total_storage",
        store=True,
        help="Sum of all storage volumes"
    )

    installed_apps = fields.Text(
        string="Sync Application Data",
        tracking=True
    )

    installed_application_ids = fields.One2many(
        "asset.installed.application",
        "asset_id",
        string="Installed Applications"
    )

    installed_application_count = fields.Integer(
        string="Application Count",
        compute="_compute_installed_application_count"
    )

    # =====================
    # CAMERA MANAGEMENT
    # =====================

    camera_ids = fields.One2many(
        "asset.camera",
        "asset_id",
        string="Cameras"
    )

    camera_count = fields.Integer(
        string="Camera Count",
        compute="_compute_camera_count"
    )

    ip_address = fields.Char(
        string="IP Address",
        tracking=True,
        help="IP address of the asset device"
    )

    monitoring_protocol = fields.Selection(
        [
            ("agent", "Agent"),
            ("snmp", "SNMP"),
            ("camera", "Camera"),
            ("http", "HTTP"),
            ("manual", "Manual"),
        ],
        string="Monitoring Protocol",
        help="Protocol used to monitor this asset"
    )

    is_camera = fields.Boolean(
        string="Is Camera Device",
        compute="_compute_is_camera",
        store=True,
        help="True if asset is a camera device"
    )

    # =====================
    # ✅ ENHANCED AGENT STATUS (UPDATED)
    # =====================

    # ✅ NEW FIELD: Last sync time for tracking agent status
    last_sync_time = fields.Datetime(
        string="Last Sync Time",
        readonly=True,
        tracking=True,
        help="Timestamp when agent last synced data"
    )

    agent_status = fields.Selection(
        [
            ("never", "Never Synced"),
            ("online", "Online"),
            ("idle", "Idle"),
            ("offline", "Offline"),
        ],
        default="never",
        compute="_compute_agent_status",
        search="_search_agent_status",
        store=False,
        help="Current agent connection status (computed from heartbeat)"
    )

    last_agent_sync = fields.Datetime(
        string="Last Agent Sync (Legacy)",
        tracking=True,
        help="Legacy field - replaced by last_sync_time"
    )

    last_seen_ip = fields.Char(
        string="Last Seen IP",
        help="Last IP address from agent sync"
    )

    agent_log_ids = fields.One2many(
        "asset.agent.log",
        "asset_id",
        string="Agent Logs"
    )

    agent_log_count = fields.Integer(
        string="Log Count",
        compute="_compute_agent_log_count"
    )

    # =====================
    # ANTIVIRUS STATUS
    # =====================

    antivirus_status = fields.Selection(
        [
            ('protected', 'Protected'),
            ('unprotected', 'Unprotected'),
            ('pending', 'Pending Deployment'),
            ('expired', 'Expired'),
            ('n/a', 'N/A'),
        ],
        string='Antivirus Status',
        default='unprotected',
        tracking=True,
        help='Current antivirus protection status of the device'
    )

    # Antivirus detailed information (from agent)
    antivirus_installed = fields.Boolean(
        string='Antivirus Installed',
        default=False,
        tracking=True,
        help='True if antivirus is installed on the device'
    )

    antivirus_product = fields.Char(
        string='Antivirus Product',
        tracking=True,
        help='Name of the antivirus product (e.g., Kaspersky Endpoint Security)'
    )

    antivirus_version = fields.Char(
        string='Antivirus Version',
        tracking=True,
        help='Version of the installed antivirus software'
    )

    antivirus_running = fields.Boolean(
        string='Antivirus Running',
        default=False,
        tracking=True,
        help='True if antivirus service is currently running'
    )

    # =====================
    # SMART CHANGE DETECTION
    # =====================

    has_changes = fields.Boolean(
        string="Has Unreviewed Changes",
        default=False,
        help="Set to True when agent detects hardware/software changes"
    )

    alert_severity = fields.Selection(
        [
            ("info", "Info"),
            ("warning", "Warning"),
            ("critical", "Critical"),
        ],
        string="Alert Severity",
        default="info",
        help="Severity of detected changes"
    )

    last_change_date = fields.Datetime(
        string="Last Change Detected",
        tracking=True
    )

    last_change_date_formatted = fields.Char(
        string="Last Change Date Formatted",
        compute="_compute_last_change_date_formatted"
    )

    def _compute_last_change_date_formatted(self):
        for record in self:
            if record.last_change_date:
                # Format as DD/MM/YYYY HH:mm:ss
                record.last_change_date_formatted = record.last_change_date.strftime("%d/%m/%Y %H:%M:%S")
            else:
                record.last_change_date_formatted = ""

    change_summary = fields.Text(
        string="Change Summary",
        help="Summary of detected changes"
    )

    alert_count = fields.Integer(
        string="Alert Count",
        default=0,
        help="Number of unresolved alerts"
    )

    # =====================
    # CONSTRAINTS
    # =====================

    @api.constrains('serial_number')
    def _check_serial_number_unique(self):
        for record in self:
            if self.search_count([('serial_number', '=', record.serial_number), ('id', '!=', record.id)]) > 0:
                raise ValidationError(_('Serial number must be unique!'))

    # =====================
    # COMPUTE HEALTH SCORE
    # =====================

    @api.depends("battery_capacity", "last_maintenance_date", "alert_count",
                 "purchase_date", "agent_status")
    def _compute_health_score(self):
        """Calculate health score based on multiple factors"""
        for asset in self:
            try:
                score = 100

                # Factor 1: Battery Health (30 points)
                if asset.battery_capacity:
                    if asset.battery_capacity < 40:
                        score -= 30
                    elif asset.battery_capacity < 60:
                        score -= 20
                    elif asset.battery_capacity < 80:
                        score -= 10

                # Factor 2: Age (20 points)
                if asset.purchase_date:
                    age_days = (fields.Date.today() - asset.purchase_date).days
                    age_years = age_days / 365.25
                    if age_years > 4:
                        score -= 20
                    elif age_years > 3:
                        score -= 15
                    elif age_years > 2:
                        score -= 10
                    elif age_years > 1:
                        score -= 5

                # Factor 3: Maintenance (25 points)
                if asset.last_maintenance_date:
                    days_since = (fields.Date.today() - asset.last_maintenance_date).days
                    if days_since > 365:
                        score -= 25
                    elif days_since > 180:
                        score -= 15
                    elif days_since > 90:
                        score -= 10
                else:
                    score -= 20

                # Factor 4: Alerts (15 points)
                if asset.alert_count > 10:
                    score -= 15
                elif asset.alert_count > 5:
                    score -= 10
                elif asset.alert_count > 2:
                    score -= 5

                # Factor 5: Agent Status (10 points)
                if asset.agent_status == "offline":
                    score -= 10
                elif asset.agent_status == "idle":
                    score -= 5
                elif asset.agent_status == "never":
                    score -= 8

                asset.health_score = max(0, min(100, score))

                # Set status based on score
                if asset.health_score >= 80:
                    asset.health_status = "excellent"
                elif asset.health_score >= 60:
                    asset.health_status = "good"
                elif asset.health_score >= 40:
                    asset.health_status = "fair"
                elif asset.health_score >= 20:
                    asset.health_status = "poor"
                else:
                    asset.health_status = "critical"

            except Exception as e:
                _logger.warning(f"Error computing health score for {asset.asset_code}: {e}")
                asset.health_score = 0
                asset.health_status = "critical"

    # =====================
    # ✅ COMPUTE AGENT STATUS (UPDATED WITH FASTER THRESHOLDS)
    # =====================

    def _compute_agent_status(self):
        """
        Compute agent status based on last_sync_time and heartbeat.
        Uses configurable timeout (default: 180 seconds / 3 minutes).

        Logic:
        - NEVER: no last_sync_time recorded (never synced)
        - ONLINE: last_sync_time within timeout
        - OFFLINE: last_sync_time older than timeout but exists
        """
        heartbeat_timeout = int(
            self.env['ir.config_parameter'].sudo().get_param(
                'asset_management.agent_heartbeat_timeout',
                default='180'
            )
        )

        for asset in self:
            try:
                # Never synced if no last_sync_time
                if not asset.last_sync_time:
                    asset.agent_status = "never"
                    continue

                now_utc = fields.Datetime.now()
                time_diff = now_utc - asset.last_sync_time

                # Agent is ONLINE if synced within timeout
                if time_diff < timedelta(seconds=heartbeat_timeout):
                    asset.agent_status = "online"
                else:
                    asset.agent_status = "offline"

            except Exception as e:
                _logger.warning(f"Error computing agent status for {asset.asset_code}: {e}")
                asset.agent_status = "never"

    def _search_agent_status(self, operator, value):
        """
        Search function for agent_status computed field.
        Matches the logic in _compute_agent_status and get_kpis.
        """
        heartbeat_timeout = int(
            self.env['ir.config_parameter'].sudo().get_param(
                'asset_management.agent_heartbeat_timeout',
                default='180'
            )
        )
        cutoff_time = fields.Datetime.now() - timedelta(seconds=heartbeat_timeout)

        if operator == '=':
            if value == 'online':
                return [('last_sync_time', '!=', False), ('last_sync_time', '>=', cutoff_time)]
            elif value == 'offline':
                # For dashboard consistency, offline includes never synced
                return ['|', ('last_sync_time', '=', False), ('last_sync_time', '<', cutoff_time)]
            elif value == 'never':
                return [('last_sync_time', '=', False)]
        elif operator == '!=':
            if value == 'online':
                return ['|', ('last_sync_time', '=', False), ('last_sync_time', '<', cutoff_time)]
            elif value == 'offline':
                return [('last_sync_time', '!=', False), ('last_sync_time', '>=', cutoff_time)]
            elif value == 'never':
                return [('last_sync_time', '!=', False)]

        # Fallback for other operators
        return []

    @api.depends("agent_log_ids")
    def _compute_agent_log_count(self):
        for asset in self:
            try:
                asset.agent_log_count = len(asset.agent_log_ids)
            except Exception as e:
                _logger.warning(f"Error computing log count: {e}")
                asset.agent_log_count = 0

    @api.depends("installed_application_ids")
    def _compute_installed_application_count(self):
        for asset in self:
            try:
                asset.installed_application_count = len(asset.installed_application_ids)
            except Exception as e:
                _logger.warning(f"Error computing installed application count: {e}")
                asset.installed_application_count = 0

    @api.depends("camera_ids")
    def _compute_camera_count(self):
        """Compute the number of cameras linked to this asset"""
        for asset in self:
            try:
                asset.camera_count = len(asset.camera_ids)
            except Exception as e:
                _logger.warning(f"Error computing camera count: {e}")
                asset.camera_count = 0

    @api.depends("category_id", "category_id.name", "asset_name", "camera_ids")
    def _compute_is_camera(self):
        """Determine if asset is a camera device based on category, name, or linked cameras"""
        for asset in self:
            try:
                is_cam = False
                # Check linked cameras
                if asset.camera_ids:
                    is_cam = True

                # Check category
                if not is_cam and asset.category_id and asset.category_id.name:
                    cat_name = asset.category_id.name.lower()
                    if any(kw in cat_name for kw in ['cctv', 'camera', 'cam']):
                        is_cam = True

                # Check name
                if not is_cam and asset.asset_name:
                    asset_name = asset.asset_name.lower()
                    if any(kw in asset_name for kw in ['cctv', 'camera']):
                        is_cam = True

                asset.is_camera = is_cam
            except Exception as e:
                _logger.warning(f"Error computing is_camera: {e}")
                asset.is_camera = False

    @api.depends('network_device_ids')
    def _compute_network_device_count(self):
        for asset in self:
            asset.network_device_count = len(asset.network_device_ids)

    @api.depends('category_id', 'category_id.name', 'network_device_ids')
    def _compute_is_network_device(self):
        """Determine if asset is a network device based on category or linked devices"""
        network_keywords = ['router', 'switch', 'network', 'firewall', 'access point', 'gateway', 'nvr', 'dvr']
        for asset in self:
            is_network = False
            # Check linked network devices
            if asset.network_device_ids:
                is_network = True

            # Check category
            if not is_network and asset.category_id and asset.category_id.name:
                cat_name = asset.category_id.name.lower()
                if any(keyword in cat_name for keyword in network_keywords):
                    is_network = True
            asset.is_network_device = is_network

    @api.depends("storage_volume_ids")
    def _compute_storage_volume_count(self):
        for asset in self:
            try:
                asset.storage_volume_count = len(asset.storage_volume_ids)
            except Exception as e:
                _logger.warning(f"Error computing volume count: {e}")
                asset.storage_volume_count = 0

    @api.depends("storage_volume_ids.total_size")
    def _compute_total_storage(self):
        for asset in self:
            try:
                asset.total_storage_size = sum(asset.storage_volume_ids.mapped('total_size'))
            except Exception as e:
                _logger.warning(f"Error computing total storage: {e}")
                asset.total_storage_size = 0.0

    # =====================
    # PROCUREMENT & WARRANTY COMPUTED FIELDS
    # =====================

    @api.depends("acquisition_type")
    def _compute_is_virtual_asset(self):
        """Determine if asset is virtual (no physical warranty applies)"""
        for asset in self:
            asset.is_virtual_asset = asset.acquisition_type in ('virtual', 'cloud')

    @api.depends("purchase_date", "warranty_period_months")
    def _compute_warranty_dates(self):
        """
        Compute warranty start and end dates from purchase date and warranty period.
        - warranty_start = purchase_date
        - warranty_end = purchase_date + warranty_period_months

        These are stored and can be manually overridden if needed.
        """
        for asset in self:
            try:
                # Only auto-compute if we have the required data
                if asset.purchase_date:
                    # Warranty start defaults to purchase date
                    if not asset.warranty_start_date:
                        asset.warranty_start_date = asset.purchase_date

                    # Warranty end computed from purchase_date + months
                    if asset.warranty_period_months and asset.warranty_period_months > 0:
                        asset.warranty_end_date = asset.purchase_date + relativedelta(
                            months=asset.warranty_period_months)
                    elif not asset.warranty_end_date:
                        # No warranty period specified, leave end date empty
                        asset.warranty_end_date = False
                else:
                    # No purchase date - keep existing values or set to False
                    if not asset.warranty_start_date:
                        asset.warranty_start_date = False
                    if not asset.warranty_end_date:
                        asset.warranty_end_date = False
            except Exception as e:
                _logger.warning(f"Error computing warranty dates for {asset.asset_code}: {e}")

    @api.depends("warranty_end_date", "acquisition_type", "warranty_period_months")
    def _compute_warranty_status(self):
        """
        Compute warranty status based on:
        - Virtual/cloud assets → 'not_applicable'
        - No warranty period or dates → 'no_warranty'
        - warranty_end_date < today → 'expired'
        - warranty_end_date >= today → 'active'
        """
        today = fields.Date.today()
        for asset in self:
            try:
                # Virtual/cloud assets have no physical warranty
                if asset.acquisition_type in ('virtual', 'cloud'):
                    asset.warranty_status = "not_applicable"
                # No warranty information provided
                elif not asset.warranty_end_date and not asset.warranty_period_months:
                    asset.warranty_status = "no_warranty"
                # Warranty expired
                elif asset.warranty_end_date and asset.warranty_end_date < today:
                    asset.warranty_status = "expired"
                # Warranty active
                elif asset.warranty_end_date and asset.warranty_end_date >= today:
                    asset.warranty_status = "active"
                else:
                    asset.warranty_status = "no_warranty"
            except Exception as e:
                _logger.warning(f"Error computing warranty status: {e}")
                asset.warranty_status = "no_warranty"

    def _compute_live_metrics(self):
        LiveMonitoring = self.env["asset.live.monitoring"].sudo()

        heartbeat_timeout = int(
            self.env['ir.config_parameter'].sudo().get_param(
                'asset_management.agent_heartbeat_timeout',
                default='180'
            )
        )

        for asset in self:
            if not asset.serial_number:
                self._reset_metrics_to_zero(asset)
                continue

            live_record = LiveMonitoring.search(
                [('serial_number', '=', asset.serial_number)],
                order='heartbeat desc',
                limit=1
            )

            if not live_record or not live_record.heartbeat:
                self._reset_metrics_to_zero(asset)
                continue

            now = fields.Datetime.now()
            time_diff = now - live_record.heartbeat
            is_online = time_diff < timedelta(seconds=heartbeat_timeout)

            asset.heartbeat = live_record.heartbeat
            asset.is_online = is_online

            if is_online:
                asset.cpu_usage = live_record.cpu_usage
                asset.memory_usage = live_record.memory_usage
                asset.storage_usage = live_record.storage_usage
                asset.battery_level = live_record.battery_level
                asset.network_bandwidth = live_record.network_bandwidth
                asset.battery_health = live_record.battery_health

                asset.cpu_usage_percent = live_record.cpu_usage_percent
                asset.ram_usage_percent = live_record.ram_usage_percent
                asset.disk_usage_percent = live_record.disk_usage_percent
                asset.network_upload_mbps = live_record.network_upload_mbps
                asset.network_download_mbps = live_record.network_download_mbps
                asset.battery_percentage = live_record.battery_percentage
            else:
                asset.cpu_usage = 0.0
                asset.memory_usage = 0.0
                asset.storage_usage = 0.0
                asset.battery_level = 0.0
                asset.network_bandwidth = 0.0
                asset.battery_health = 0.0
                asset.cpu_usage_percent = 0.0
                asset.ram_usage_percent = 0.0
                asset.disk_usage_percent = 0.0
                asset.network_upload_mbps = 0.0
                asset.network_download_mbps = 0.0
                asset.battery_percentage = 0.0

    def _reset_metrics_to_zero(self, asset):
        asset.cpu_usage = 0.0
        asset.memory_usage = 0.0
        asset.storage_usage = 0.0
        asset.battery_level = 0.0
        asset.network_bandwidth = 0.0
        asset.battery_health = 0.0
        asset.cpu_usage_percent = 0.0
        asset.ram_usage_percent = 0.0
        asset.disk_usage_percent = 0.0
        asset.network_upload_mbps = 0.0
        asset.network_download_mbps = 0.0
        asset.battery_percentage = 0.0
        asset.heartbeat = False
        asset.is_online = False

    @api.depends('agent_log_ids.sync_time')
    def _compute_metric_trends(self):
        for asset in self:
            logs = asset.agent_log_ids.sorted('sync_time', reverse=True)[:2]
            if len(logs) >= 2:
                try:
                    current = json.loads(logs[0].snapshot_data or '{}')
                    previous = json.loads(logs[1].snapshot_data or '{}')
                    asset.cpu_trend = current.get('cpu_usage', 0) - previous.get('cpu_usage', 0)
                    asset.memory_trend = current.get('memory_usage', 0) - previous.get('memory_usage', 0)
                    asset.storage_trend = current.get('storage_usage', 0) - previous.get('storage_usage', 0)
                    asset.battery_trend = current.get('battery_level', 0) - previous.get('battery_level', 0)
                    asset.network_trend = current.get('network_bandwidth', 0) - previous.get('network_bandwidth', 0)
                    asset.battery_health_trend = current.get('battery_health', 0) - previous.get('battery_health', 0)
                except:
                    asset.cpu_trend = asset.memory_trend = asset.storage_trend = 0
                    asset.battery_trend = asset.network_trend = asset.battery_health_trend = 0
            else:
                asset.cpu_trend = asset.memory_trend = asset.storage_trend = 0
                asset.battery_trend = asset.network_trend = asset.battery_health_trend = 0

    @api.depends("serial_number", "asset_code")
    def _compute_qr_code(self):
        try:
            import qrcode
            import base64
            from io import BytesIO
        except ImportError:
            _logger.warning("qrcode library not installed")
            for asset in self:
                asset.qr_code = False
            return

        for asset in self:
            try:
                if asset.serial_number:
                    qr = qrcode.QRCode(version=1, box_size=10, border=5)
                    qr_data = f"Asset: {asset.asset_code}\nSerial: {asset.serial_number}"
                    qr.add_data(qr_data)
                    qr.make(fit=True)
                    img = qr.make_image(fill_color="black", back_color="white")
                    buffer = BytesIO()
                    img.save(buffer, format="PNG")
                    asset.qr_code = base64.b64encode(buffer.getvalue())
                else:
                    asset.qr_code = False
            except Exception as e:
                _logger.warning(f"Error generating QR code: {e}")
                asset.qr_code = False

    # =====================
    # ASSET CODE GENERATION
    # =====================

    def _get_asset_sequence_code(self, vals):
        """
        Determine the sequence code based on asset type/platform.

        Returns:
            str: Sequence code for the asset type
        """
        # Check if it's a camera/CCTV asset
        if vals.get('is_camera'):
            return 'asset.asset.cctv'

        # Check if it's a network device
        if vals.get('is_network_device'):
            return 'asset.asset.network'

        # Check platform for regular assets
        os_platform = vals.get('os_platform', '')

        if os_platform == 'windows':
            return 'asset.asset.windows'
        elif os_platform == 'linux':
            return 'asset.asset.linux'
        elif os_platform == 'macos':
            return 'asset.asset.macos'

        # Fallback to generic sequence
        return 'asset.asset.sequence'

    # =====================
    # CREATE & WRITE
    # =====================

    @api.model
    def _get_asset_sequence_code(self, vals):
        """
        Return the ir.sequence code to use for asset_code generation.

        Priority order:
          1. Camera (CCTV)  → asset.asset.cctv   → CCTV-XXXXX
          2. Windows        → asset.asset.windows → WIN-XXXXX
          3. Linux          → asset.asset.linux   → LNX-XXXXX
          4. macOS          → asset.asset.macos   → MAC-XXXXX
          5. Fallback       → asset.asset.sequence → AST-XXXXX
        """
        # CCTV cameras take highest priority
        if vals.get('asset_type') == 'camera':
            return 'asset.asset.cctv'

        # Check `platform` first, then fall back to `os_name` string detection
        platform = vals.get('platform', '') or ''
        os_name = (vals.get('os_name') or '').lower()

        linux_variants = [
            'ubuntu', 'debian', 'linux', 'redhat', 'centos', 'fedora',
            'mint', 'arch', 'manjaro', 'opensuse', 'kali', 'alpine',
            'suse', 'rhel', 'oracle',
        ]

        def _resolve_platform(p, os):
            if p == 'windows' or 'windows' in os:
                return 'windows'
            if p == 'linux' or any(v in os for v in linux_variants):
                return 'linux'
            if p == 'macos' or 'mac' in os or 'darwin' in os:
                return 'macos'
            return None

        resolved = _resolve_platform(platform, os_name)
        if resolved == 'windows':
            return 'asset.asset.windows'
        elif resolved == 'linux':
            return 'asset.asset.linux'
        elif resolved == 'macos':
            return 'asset.asset.macos'

        # Generic fallback
        return 'asset.asset.sequence'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("asset_code"):
                # Determine the sequence code based on asset type/platform
                sequence_code = self._get_asset_sequence_code(vals)
                vals["asset_code"] = self.env["ir.sequence"].next_by_code(sequence_code)

            if not vals.get("asset_name") and vals.get("hostname"):
                vals["asset_name"] = vals["hostname"]

            if vals.get("assigned_employee_id") and not vals.get("assignment_date"):
                vals["assignment_date"] = fields.Date.today()

            # Default in_service_date to purchase_date if not provided
            if vals.get("purchase_date") and not vals.get("in_service_date"):
                vals["in_service_date"] = vals["purchase_date"]

        assets = super().create(vals_list)

        for asset, vals in zip(assets, vals_list):
            # Auto-link/create maintenance equipment
            asset._handle_maintenance_equipment(vals)

            # Initial logs
            if asset.assigned_employee_id:
                self.env['asset.assignment.history'].create({
                    'asset_id': asset.id,
                    'employee_id': asset.assigned_employee_id.id,
                    'action': 'assign',
                })
                self.env['asset.audit.log'].create({
                    'asset_id': asset.id,
                    'log_type': 'assignment',
                    'new_value': asset.assigned_employee_id.name,
                    'description': f"Asset assigned to {asset.assigned_employee_id.name} on creation"
                })

            if asset.location_id:
                self.env['asset.location.history'].create({
                    'asset_id': asset.id,
                    'location_id': asset.location_id.id,
                })
                self.env['asset.audit.log'].create({
                    'asset_id': asset.id,
                    'log_type': 'location',
                    'new_value': asset.location_id.complete_name,
                    'description': f"Asset location set to {asset.location_id.complete_name} on creation"
                })

            self.env['asset.audit.log'].create({
                'asset_id': asset.id,
                'log_type': 'status',
                'new_value': asset.state,
                'description': f"Asset status set to {asset.state} on creation"
            })

        return assets

    def action_reassign_all_asset_codes(self):
        """
        Reassign asset_code for ALL asset.asset records based on their type.
        Uses both `platform` field and `os_name` string to determine the right prefix.
        Each type maintains its own independent counter.
        Can be triggered from: Asset list → Action → Reassign Asset Codes by Type
        """
        linux_variants = [
            'ubuntu', 'debian', 'linux', 'redhat', 'centos', 'fedora',
            'mint', 'arch', 'manjaro', 'opensuse', 'kali', 'alpine',
            'suse', 'rhel', 'oracle',
        ]

        def _classify(asset):
            """Return the ir.sequence code for a single asset."""
            if asset.asset_type == 'camera':
                return 'asset.asset.cctv'
            # Combine stored platform + os_name text for best-effort detection
            plat = asset.platform or ''
            os = (asset.os_name or '').lower()
            if plat == 'windows' or 'windows' in os:
                return 'asset.asset.windows'
            if plat == 'linux' or any(v in os for v in linux_variants):
                return 'asset.asset.linux'
            if plat == 'macos' or 'mac' in os or 'darwin' in os:
                return 'asset.asset.macos'
            return 'asset.asset.sequence'

        all_assets = self.search([])
        reassigned = 0
        for asset in all_assets:
            seq_code = _classify(asset)
            new_code = self.env['ir.sequence'].next_by_code(seq_code)
            if new_code:
                asset.write({'asset_code': new_code})
                reassigned += 1

        _logger.info("Reassigned asset codes for %d asset(s).", reassigned)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Asset Codes Reassigned',
                'message': f'{reassigned} asset(s) reassigned → WIN / LNX / MAC / CCTV / AST prefix.',
                'type': 'success',
                'sticky': False,
            }
        }

    def _handle_maintenance_equipment(self, vals):
        """Automatically link or create maintenance.equipment based on serial_no or name."""
        Equipment = self.env['maintenance.equipment']
        for asset in self:
            name = vals.get('asset_name') or asset.asset_name
            serial = vals.get('serial_number') or asset.serial_number
            if not name and not serial:
                continue

            # Search by serial_no FIRST (the column with the UNIQUE constraint),
            # then fall back to name. This prevents duplicate-key errors when an
            # orphan maintenance.equipment row exists from a previous failed sync.
            equipment = Equipment.browse()
            if serial:
                equipment = Equipment.search([('serial_no', '=', serial)], limit=1)
            if not equipment and name:
                equipment = Equipment.search([('name', '=', name)], limit=1)

            if not equipment:
                # Create only if neither search found a match
                equipment = Equipment.create({
                    'name': name or f'Asset-{serial}',
                    'serial_no': serial,
                    'model': vals.get('processor') or asset.processor,
                })

            # Link asset to equipment
            asset.write({'maintenance_equipment_id': equipment.id})

    def write(self, vals):
        for asset in self:
            if 'assigned_employee_id' in vals:
                old_employee = asset.assigned_employee_id
                new_employee_id = vals.get('assigned_employee_id')
                if new_employee_id != (old_employee.id if old_employee else False):
                    if old_employee:
                        self.env['asset.assignment.history'].create({
                            'asset_id': asset.id,
                            'employee_id': old_employee.id,
                            'action': 'unassign',
                        })
                    if new_employee_id:
                        new_employee = self.env['hr.employee'].browse(new_employee_id)
                        self.env['asset.assignment.history'].create({
                            'asset_id': asset.id,
                            'employee_id': new_employee_id,
                            'action': 'assign',
                        })
                        self.env['asset.audit.log'].create({
                            'asset_id': asset.id,
                            'log_type': 'assignment',
                            'old_value': old_employee.name if old_employee else 'None',
                            'new_value': new_employee.name,
                            'description': f"Asset assignment changed from {old_employee.name if old_employee else 'None'} to {new_employee.name}"
                        })

            if 'location_id' in vals:
                old_location = asset.location_id
                new_location_id = vals.get('location_id')
                if new_location_id != (old_location.id if old_location else False):
                    if new_location_id:
                        new_location = self.env['asset.location'].browse(new_location_id)
                        self.env['asset.location.history'].create({
                            'asset_id': asset.id,
                            'location_id': new_location_id,
                        })
                        self.env['asset.audit.log'].create({
                            'asset_id': asset.id,
                            'log_type': 'location',
                            'old_value': old_location.complete_name if old_location else 'None',
                            'new_value': new_location.complete_name,
                            'description': f"Asset location changed from {old_location.complete_name if old_location else 'None'} to {new_location.complete_name}"
                        })

            if 'state' in vals:
                old_state = asset.state
                new_state = vals.get('state')
                if new_state != old_state:
                    self.env['asset.audit.log'].create({
                        'asset_id': asset.id,
                        'log_type': 'status',
                        'old_value': old_state,
                        'new_value': new_state,
                        'description': f"Asset status changed from {old_state} to {new_state}"
                    })

        res = super().write(vals)
        if 'asset_name' in vals and not vals.get('maintenance_equipment_id'):
            self._handle_maintenance_equipment(vals)
        return res

    # =====================
    # WORKFLOW ACTIONS
    # =====================

    def action_assign(self):
        """
        Assign asset to employee with validation rules.

        Requirements for assignment:
        - Asset must be in 'draft' state
        - assigned_employee_id must be set
        - acquisition_type must be set (for new assets)
        - purchase_date must be set (for new assets)
        - category_id must be set

        Note: Backward compatibility - existing assets without purchase_date
        can still be assigned if they were created before this feature.
        """
        for asset in self:
            if asset.state not in ("draft", "submit"):
                raise UserError(_("Only Draft or Submitted assets can be assigned."))
            if not asset.assigned_employee_id:
                raise UserError(_("Please select an employee before assigning."))

            # Validation for procurement fields (only if asset has any procurement info)
            # This ensures backward compatibility - existing assets without procurement data
            # can still be assigned, but new assets must have proper procurement info
            missing_fields = []

            # Check if this appears to be a new asset with procurement tracking
            # (has any procurement field set, or was created after feature rollout)
            has_procurement_intent = (
                    asset.acquisition_type or
                    asset.purchase_cost or
                    asset.procurement_vendor_id or
                    asset.invoice_ref
            )

            if has_procurement_intent or asset.purchase_date:
                # If user has started filling procurement info, require key fields
                if not asset.acquisition_type:
                    missing_fields.append(_("Acquisition Type"))
                if not asset.purchase_date:
                    missing_fields.append(_("Purchase Date"))

            # Category is always required for proper asset classification
            if not asset.category_id:
                missing_fields.append(_("Asset Category"))

            if missing_fields:
                raise UserError(_(
                    "Cannot assign asset. Please fill in the following required fields:\n• %s"
                ) % "\n• ".join(missing_fields))

            asset.write({
                "state": "assigned",
                "assignment_date": fields.Date.today(),
            })
            asset._send_assignment_email()

    def action_deploy_application(self):
        """Open the App Deployment wizard, pre-filled with this asset."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Deploy Application'),
            'res_model': 'asset_management.app_deployment_wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_device_id': self.id,
            },
        }

    def action_maintenance(self):
        for asset in self:
            if asset.state != "assigned":
                raise UserError(_("Only Assigned assets can go to Maintenance."))
        return {
            'name': _('Create Repair Request'),
            'type': 'ir.actions.act_window',
            'res_model': 'repair.management',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_asset_id': self.id,
            },
        }

    def action_scrap(self):
        for asset in self:
            asset.state = "scrapped"

    def action_reset_to_draft(self):
        for asset in self:
            asset.state = "draft"

    def action_consume_spare(self):
        """
        Consumes a spare part using Odoo Inventory (stock.picking).
        Follows Odoo best practices for internal transfers.
        """
        for asset in self:
            if not asset.spare_product_id:
                raise UserError(_("Please select a spare product to consume."))
            if asset.spare_qty <= 0:
                raise UserError(_("Spare quantity must be greater than zero."))

            # 1. Find the Internal Picking Type
            picking_type = self.env['stock.picking.type'].search([
                ('code', '=', 'internal'),
                ('company_id', '=', self.env.company.id)
            ], limit=1)

            if not picking_type:
                # Fallback to ref if search fails
                picking_type = self.env.ref('stock.picking_type_internal', raise_if_not_found=False)

            if not picking_type:
                raise UserError(_("Internal Picking Type not found. Please ensure Inventory is configured."))

            # Verify stock availability (optional but recommended for storable products)
            if asset.spare_product_id.type == 'product':
                available_qty = asset.spare_product_id.with_context(
                    location=picking_type.default_location_src_id.id
                ).qty_available
                if available_qty < asset.spare_qty:
                    raise UserError(_(
                        "Not enough stock for %s in %s. Available: %s %s"
                    ) % (
                                        asset.spare_product_id.name,
                                        picking_type.default_location_src_id.display_name,
                                        available_qty,
                                        asset.spare_product_id.uom_id.name
                                    ))

            # 2. Create Stock Picking
            picking_vals = {
                'picking_type_id': picking_type.id,
                'location_id': picking_type.default_location_src_id.id,
                'location_dest_id': picking_type.default_location_dest_id.id,
                'origin': f"Asset Spare: {asset.asset_name} ({asset.asset_code})",
                'move_type': 'direct',
                'company_id': self.env.company.id,
            }
            picking = self.env['stock.picking'].create(picking_vals)

            # 3. Create Stock Move
            move_vals = {
                'name': f"Spare Consumption for {asset.asset_name}",
                'product_id': asset.spare_product_id.id,
                'product_uom_qty': asset.spare_qty,
                'product_uom': asset.spare_product_id.uom_id.id,
                'picking_id': picking.id,
                'location_id': picking.location_id.id,
                'location_dest_id': picking.location_dest_id.id,
                'company_id': self.env.company.id,
            }
            move = self.env['stock.move'].create(move_vals)

            # 4. Confirm, Assign, and Validate Picking
            picking.action_confirm()
            picking.action_assign()

            # Simple validation for the picking
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
                move.picked = True

            picking.button_validate()

            # 5. Store values for logging before reset
            consumed_qty = asset.spare_qty
            consumed_product_name = asset.spare_product_id.display_name
            consumed_product_uom = asset.spare_product_id.uom_id.name

            # 6. Create Spare Consumption History Record
            self.env["asset.spare.consumption"].create({
                "asset_id": asset.id,
                "product_id": asset.spare_product_id.id,
                "quantity": consumed_qty,
                "replacement_date": asset.replacement_date,
                "picking_id": picking.id,
                "consumed_by": self.env.user.id,
                "state": "consumed",
            })

            # 7. Reset Consumption Fields
            asset.write({
                'spare_product_id': False,
                'spare_qty': 1.0,
                'replacement_date': fields.Date.today(),
            })

            # 8. Log in chatter (using stored values)
            asset.message_post(body=_(
                "Consumed %s %s of %s for this asset. Picking: %s"
            ) % (
                                        consumed_qty,
                                        consumed_product_uom,
                                        consumed_product_name,
                                        picking.name
                                    ))

            # Success notification
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Spare Consumed'),
                    'message': _('Successfully consumed %s %s for %s') % (
                        consumed_qty, consumed_product_name, asset.asset_name
                    ),
                    'sticky': False,
                    'type': 'success',
                }
            }

    def _send_assignment_email(self):
        """Send email notification to employee"""
        self.ensure_one()
        if not self.assigned_employee_id or not self.assigned_employee_id.work_email:
            return

        template = self.env.ref("asset_management.email_template_asset_assignment", raise_if_not_found=False)
        if template:
            try:
                template.send_mail(self.id, force_send=True)
            except Exception as e:
                _logger.warning(f"Could not send assignment email: {e}")

    # =====================
    # ✅ AGENT SYNC METHODS (UPDATED)
    # =====================

    @api.model
    def sync_from_agent(self, payload):
        """
        Process agent sync payload and update asset record.
        Called from asset_agent_api.py controller.
        Creates a new asset if one doesn't exist with the given serial number.
        """
        try:
            serial_number = payload.get("serial_number")

            # Find existing asset
            asset = self.search([("serial_number", "=", serial_number)], limit=1)

            sync_time = fields.Datetime.now()

            # If asset doesn't exist, create a new one
            if not asset:
                _logger.info(f"🆕 Creating new asset for serial_number: {serial_number}")

                # Determine asset name from payload (prefer device_name, fallback to hostname)
                asset_name = payload.get("device_name") or payload.get("hostname") or f"Asset-{serial_number[:8]}"

                # Auto-detect asset type from chassis info the agent sent
                detected_type = payload.get("chassis_type")  # 'laptop', 'desktop', 'server', or 'unknown'
                # Belt-and-suspenders: also infer from battery if chassis is unknown
                if detected_type in (None, "", "unknown"):
                    if payload.get("battery_capacity"):
                        detected_type = "laptop"
                        print("------------------", detected_type, '--------------------------------------------')
                    else:
                        detected_type = "desktop"
                        print("------------------", detected_type, '--------------------------------------------')

                asset = self.create({
                    "asset_name": asset_name,
                    "serial_number": serial_number,
                    "state": "draft",
                    # "category_id": detected_type,  # ← auto-mapped from agent
                    "os_name": payload.get("os_name"),
                    "os_version": payload.get("os_version"),
                    "os_codename": payload.get("os_codename"),
                    "platform": payload.get("platform", "unknown"),
                    "monitoring_protocol": 'agent',
                })
                _logger.info(f"✅ Created new asset: {asset.asset_name} (ID: {asset.id})")

            # Snapshot BEFORE update_vals overwrites them - this is the only
            # chance to compare old vs new. Limited to the fields the agent
            # itself reports as a single current value (RAM/storage/CPU) -
            # installed apps and storage volumes are lists with their own
            # diffing further down, not simple scalar overwrites.
            old_hardware = {
                'ram_size': asset.ram_size,
                'rom_size': asset.rom_size,
                'processor': asset.processor,
            }

            update_vals = {
                "hostname": payload.get("hostname"),
                "device_name": payload.get("device_name"),
                "processor": payload.get("processor"),
                "graphics_card_raw": payload.get("graphics_card_raw"),
                "os_type": payload.get("os_type"),
                "os_name": payload.get("os_name"),
                "os_version": payload.get("os_version"),
                "os_codename": payload.get("os_codename"),
                "ram_size": payload.get("ram_size"),
                "rom_size": payload.get("rom_size"),
                "battery_capacity": payload.get("battery_percentage"),
                "battery_mah": payload.get("battery_capacity"),
                "disk_type": payload.get("disk_type"),
                "last_seen_ip": payload.get("ip_address"),
                "ip_address": payload.get("ip_address"),
                "mac_address": payload.get("mac_address", ""),
                "last_sync_time": sync_time,
                "last_agent_sync": sync_time,
                "platform": payload.get("platform", "unknown"),
                # Antivirus information from agent (TASK 2: Odoo Backend Changes)
                "antivirus_installed": payload.get("antivirus_installed", False),
                "antivirus_product": payload.get("antivirus_product", ""),
                "antivirus_version": payload.get("antivirus_version", ""),
                "antivirus_running": payload.get("antivirus_running", False),
            }

            # Release-upgrade availability. Set only when the agent actually
            # reported it: update_vals is written verbatim (no None-stripping),
            # so listing these unconditionally would let an older agent that
            # doesn't know about them blank out what a newer agent found.
            if "os_upgrade_available" in payload:
                update_vals["os_upgrade_available"] = bool(payload.get("os_upgrade_available"))
            for _key in ("os_upgrade_available_version",
                         "os_upgrade_available_codename",
                         "os_upgrade_check_output"):
                if _key in payload:
                    update_vals[_key] = payload.get(_key) or False
            if payload.get("os_upgrade_checked_date"):
                update_vals["os_upgrade_checked_date"] = payload.get("os_upgrade_checked_date")

            # Compute antivirus_status based on antivirus_installed and antivirus_running
            if payload.get("antivirus_installed") and payload.get("antivirus_running"):
                update_vals["antivirus_status"] = "protected"
            elif payload.get("antivirus_installed") and not payload.get("antivirus_running"):
                update_vals["antivirus_status"] = "unprotected"
            elif not payload.get("antivirus_installed"):
                update_vals["antivirus_status"] = "unprotected"

            # 📍 Handle Location (Agent-provided coordinates ONLY)
            # TRUST agent data AS-IS. NO reverse geocoding, NO IP re-lookup, NO guessing.
            # Agent sends: location_latitude/latitude, location_longitude/longitude, location_source
            # Support both naming conventions for backward compatibility
            location_latitude = normalize_coordinate(
                payload.get("location_latitude") or payload.get("latitude")
            )
            location_longitude = normalize_coordinate(
                payload.get("location_longitude") or payload.get("longitude")
            )
            location_source_raw = str(payload.get("location_source", "")).lower().strip()

            # Normalize location_source to valid selection
            # Accept "windows", "gps", "ip" as valid sources (all indicate device has location)
            if location_source_raw in ("gps", "windows", "device"):
                location_source = "gps"  # Treat windows/device location as GPS-equivalent
            elif location_source_raw == "ip":
                location_source = "ip"
            else:
                location_source = "unavailable"

            _logger.info(
                f"📍 [Location] Raw: lat={payload.get('location_latitude') or payload.get('latitude')}, lon={payload.get('location_longitude') or payload.get('longitude')}, source={payload.get('location_source')} | Normalized: lat={location_latitude}, lon={location_longitude}, source={location_source}")

            # Store coordinates AS-IS if valid (regardless of source)
            if has_valid_coordinates(location_latitude, location_longitude):
                # Has the device actually moved? IP-derived coordinates are
                # identical on every sync for a stationary machine, so writing
                # them again just dirties the row and invites a concurrent
                # update conflict for no gain. ~0.002 degrees is roughly 200m -
                # below that this is GPS/IP jitter, not movement.
                _coords_moved = (
                    abs((asset.latitude or 0.0) - float(location_latitude)) > 0.002
                    or abs((asset.longitude or 0.0) - float(location_longitude)) > 0.002
                )
                if _coords_moved or not asset.last_location_update:
                    update_vals.update({
                        "latitude": float(location_latitude),
                        "longitude": float(location_longitude),
                        "location_source": location_source,
                        "last_location_update": sync_time,
                    })

                # 📍 REVERSE GEOCODING - deliberately NOT done here.
                #
                # This used to call reverse_geocode() inline, which is a
                # blocking HTTP request to Nominatim with a 10s timeout. Three
                # things made that the single biggest source of load:
                #
                #   1. It ran on EVERY sync, for coordinates that had not
                #      changed - the same lat/lon geocoded over and over.
                #   2. Nominatim rate-limits to ~1 request/second and was
                #      returning HTTP 429 constantly, so the call was slow AND
                #      useless.
                #   3. Worst: it held the database transaction open for the
                #      whole network round trip. Every agent sync kept row
                #      locks on asset_asset and asset_live_monitoring while
                #      waiting on an external server, which is what produced
                #      "could not serialize access due to concurrent update".
                #      Odoo then retried each failed request 5 times, and every
                #      retry made the same HTTP call again - so load grew under
                #      failure instead of shrinking.
                #
                # The address is now filled by _cron_reverse_geocode_missing_
                # addresses(), which already exists and is the right place for
                # it: outside the request, rate-limited, and only for records
                # that actually need it.
                #
                # Clearing the cached address when the device has genuinely
                # moved is what tells that cron there is work to do.
                if _coords_moved:
                    update_vals.update({
                        "location_address": "",
                        "location_city": "",
                        "location_state": "",
                        "location_country": "",
                        "location_area": "",
                    })
            else:
                # No valid coordinates - mark as unavailable
                update_vals.update({
                    "location_source": "unavailable",
                })
                _logger.info(f"📍 [Location] No valid coordinates for {serial_number}, marked unavailable")

            snapshot_data = json.dumps({
                "cpu_usage": payload.get("cpu_usage", 0),
                "memory_usage": payload.get("memory_usage", 0),
                "storage_usage": payload.get("storage_usage", 0),
                "battery_level": payload.get("battery_level", payload.get("battery_percentage", 0)),
                "network_bandwidth": payload.get("network_bandwidth", 0),
                "battery_health": payload.get("battery_health", 100),
            })
            AgentLog = self.env["asset.agent.log"].sudo()
            AgentLog.create({
                "asset_id": asset.id,
                "sync_time": fields.Datetime.now(),
                "log_type": "updated",
                "changes_detected": False,
                "status": "success",
                "ip_address": payload.get("ip_address"),
                "snapshot_data": snapshot_data,
            })

            # Log trimming moved OUT of the sync path - see
            # cron_trim_agent_logs() below.
            #
            # unlink() calls env.flush_all(), which pushes every pending write
            # in the transaction to the database immediately - including the
            # asset_asset row this method is in the middle of updating. That
            # turned a routine cleanup into an early, wide lock on the busiest
            # table in the system, and was the statement the serialization
            # failures actually died on (asset_asset.py:2244 in the logs).
            #
            # Trimming logs is housekeeping. It does not need to happen on the
            # request path at all, let alone on every single heartbeat from
            # every agent.

            # 🔴 CRITICAL: Write update values to asset record
            asset.write(update_vals)
            _logger.info(f"✅ Updated asset {asset.asset_name} with hardware specs")

            # Hardware change history - RAM/storage/CPU reported as a single
            # current value each sync, diffed against the snapshot taken
            # before update_vals was written. Skipped on the asset's very
            # first sync (old value blank) - that is the asset's spec being
            # recorded for the first time, not a change to it.
            HARDWARE_LABELS = {
                'ram_size': 'RAM (GB)',
                'rom_size': 'Storage (GB)',
                'processor': 'Processor',
            }
            for field_name, label in HARDWARE_LABELS.items():
                old_value = old_hardware[field_name]
                new_value = asset[field_name]
                if not old_value or old_value == new_value:
                    continue
                if not new_value:
                    description = f"{label} removed (was {old_value})"
                else:
                    description = f"{label} changed: {old_value} → {new_value}"
                asset._log_asset_change(
                    'hardware_change', description, old_value, new_value)

            # 🔴 CRITICAL: Process installed applications
            installed_apps_json = payload.get("installed_apps", "[]")

            if installed_apps_json and installed_apps_json != "[]":
                try:
                    apps_list = json.loads(installed_apps_json)
                    _logger.info(f"📦 Processing {len(apps_list)} applications...")

                    # ✅ SMART CHANGE DETECTION FOR APPLICATIONS
                    old_apps = self.env['asset.installed.application'].search([
                        ('asset_id', '=', asset.id)
                    ])
                    # The agent's first-ever sync for an asset has no prior
                    # baseline to diff against, so every package it reports
                    # lands in `installed` - that is the machine's existing
                    # inventory being recorded for the first time, not 100+
                    # packages actually getting installed in one sync. Only
                    # log software history from the second sync onward, once
                    # there is a real "before" to compare to.
                    is_first_software_sync = not old_apps

                    # Store old apps: {name: version}
                    old_apps_map = {app.name: app.version or "" for app in old_apps}

                    # Store new apps: {name: version}
                    new_apps_map = {app.get('name'): app.get('version') or "" for app in apps_list if app.get('name')}

                    installed = []
                    removed = []
                    updated = []

                    # Detect removals and version updates
                    updated_pairs = []  # (name, old_version, new_version) - the
                    # display strings in `updated` lose the structured old/new
                    # values, which the per-item history log needs.
                    for name, version in old_apps_map.items():
                        if name not in new_apps_map:
                            removed.append(name)
                        elif new_apps_map[name] != version:
                            updated.append(f"{name} ({version} → {new_apps_map[name]})")
                            updated_pairs.append((name, version, new_apps_map[name]))

                    # Detect new installations
                    for name in new_apps_map:
                        if name not in old_apps_map:
                            installed.append(name)

                    # Trigger alerts if changes found
                    if installed or removed or updated:
                        summary = []
                        if installed:
                            summary.append(
                                f"Installed: {', '.join(installed[:5])}{'...' if len(installed) > 5 else ''}")
                        if removed:
                            summary.append(f"Removed: {', '.join(removed[:5])}{'...' if len(removed) > 5 else ''}")
                        if updated:
                            summary.append(f"Updated: {', '.join(updated[:5])}{'...' if len(updated) > 5 else ''}")

                        total_changes = len(installed) + len(removed) + len(updated)
                        asset.write({
                            "has_changes": True,
                            "alert_count": asset.alert_count + 1,
                            "last_change_date": fields.Datetime.now(),
                            "alert_severity": "warning" if total_changes > 10 else "info",
                            "change_summary": " | ".join(summary)
                        })
                        _logger.info(f"⚠ Changes detected for {asset.asset_name}: {total_changes} apps")

                        # Software change history - one row per app event,
                        # e.g. "Installed asset.agent", "chrome changes
                        # detected upgrade 120.0 to 121.0", "Removed
                        # firefox". Skipped on the first sync (see
                        # is_first_software_sync above) - that is baseline
                        # inventory, not a change.
                        if not is_first_software_sync:
                            for name in installed:
                                asset._log_asset_change(
                                    'software_change', f"Installed {name}",
                                    False, new_apps_map.get(name))
                            for name in removed:
                                asset._log_asset_change(
                                    'software_change', f"Removed {name}",
                                    old_apps_map.get(name), False)
                            for name, old_version, new_version in updated_pairs:
                                direction = _version_change_direction(
                                    old_version, new_version)
                                asset._log_asset_change(
                                    'software_change',
                                    f"{name} changes detected {direction} "
                                    f"{old_version} to {new_version}",
                                    old_version, new_version)

                        # Delete old app records for this asset
                        # --- Incremental sync (use the diffs computed above) ---
                        # 1. UNLINK only apps that were actually removed
                        if removed:
                            to_remove = old_apps.filtered(lambda a: a.name in removed)
                            to_remove.unlink()
                            _logger.info(f"🗑️ Removed {len(to_remove)} app records (no longer installed)")

                        # 2. Build a quick lookup of incoming app dicts by name
                        incoming_by_name = {a.get('name'): a for a in apps_list if a.get('name')}

                        # Helper: normalize installed_date strings
                        def _format_installed_date(raw):
                            raw = str(raw or '').strip()
                            if not raw or raw.lower() == 'none':
                                return raw
                            if len(raw) == 8 and raw.isdigit():
                                # YYYYMMDD -> DD/MM/YYYY
                                return f"{raw[6:8]}/{raw[4:6]}/{raw[0:4]}"
                            if '-' in raw and len(raw) >= 10:
                                parts = raw.split(' ')[0].split('-')
                                if len(parts) == 3:
                                    # YYYY-MM-DD -> DD/MM/YYYY
                                    return f"{parts[2]}/{parts[1]}/{parts[0]}"
                            return raw

                        # 3. WRITE updates only to apps whose version changed
                        updated_count = 0
                        for old_app in old_apps:
                            if old_app.name in incoming_by_name and old_app.name not in removed:
                                app = incoming_by_name[old_app.name]
                                new_version = (app.get('version') or '')[:100]
                                if (old_app.version or '') != new_version:
                                    try:
                                        old_app.write({
                                            'version': new_version,
                                            'publisher': (app.get('publisher', '') or '')[:255],
                                            'installed_date': _format_installed_date(app.get('installed_date')),
                                            'size': app.get('size', 0),
                                        })
                                        updated_count += 1
                                    except Exception as e:
                                        _logger.warning(f"⚠️ Failed to update app {old_app.name}: {e}")

                        # 4. CREATE only the newly installed apps
                        created_count = 0
                        if installed:
                            new_vals = []
                            for name in installed:
                                app = incoming_by_name.get(name)
                                if not app:
                                    continue
                                new_vals.append({
                                    'asset_id': asset.id,
                                    'name': name[:255],
                                    'publisher': (app.get('publisher', '') or '')[:255],
                                    'version': (app.get('version', '') or '')[:100],
                                    'installed_date': _format_installed_date(app.get('installed_date')),
                                    'size': app.get('size', 0),
                                })
                            if new_vals:
                                try:
                                    created = self.env['asset.installed.application'].create(new_vals)
                                    created_count = len(created)
                                except Exception as e:
                                    _logger.warning(f"⚠️ Bulk create failed, falling back to per-record: {e}")
                                    # Fallback: per-row so a single bad app doesn't kill the batch
                                    for vals in new_vals:
                                        try:
                                            with self.env.cr.savepoint():
                                                self.env['asset.installed.application'].create(vals)
                                                created_count += 1
                                        except Exception as ae:
                                            _logger.warning(f"⚠️ Skipped bad app {vals['name']}: {ae}")

                        _logger.info(
                            f"📊 App sync done: +{created_count} new, ~{updated_count} updated, "
                            f"-{len(removed)} removed (had {len(old_apps)} before)"
                        )

                except json.JSONDecodeError as e:
                    _logger.error(f"❌ JSON decode error for installed_apps: {e}")
                except Exception as e:
                    _logger.error(f"❌ Error processing installed apps: {e}")
                    import traceback
                    _logger.error(traceback.format_exc())
            else:
                _logger.info(f"ℹ️ No installed apps data received")

            # 🔴 PROCESS STORAGE VOLUMES
            storage_volumes_raw = payload.get("storage_volumes", "[]")
            volumes_list = []

            # Parse storage volume data safely from JSON string
            if isinstance(storage_volumes_raw, str):
                try:
                    volumes_list = json.loads(storage_volumes_raw)
                except json.JSONDecodeError as e:
                    _logger.error(f"❌ JSON decode error for storage_volumes: {e}")
            elif isinstance(storage_volumes_raw, list):
                volumes_list = storage_volumes_raw

            if volumes_list:
                try:
                    _logger.info(f"💿 Processing {len(volumes_list)} storage volumes...")

                    # Update in place instead of delete-all-then-recreate.
                    #
                    # The old code unlinked every volume row for the asset and
                    # rebuilt them on EVERY sync - typically 30 minutes apart
                    # per agent, for data that almost never changes (a disk
                    # does not usually change size). Two costs:
                    #
                    #   * unlink() calls env.flush_all(), forcing every pending
                    #     write in the transaction out early and taking locks
                    #     on asset_asset while this method is still updating it
                    #     - the same mechanism behind the serialization
                    #     failures traced to the agent-log unlink.
                    #   * It churned primary keys, so every volume row was
                    #     recreated with a new id and any reference to it broke.
                    #
                    # Matching on drive_letter keeps the same rows and writes
                    # only what actually differs.
                    VolumeModel = self.env['asset.storage.volume']
                    existing_volumes = {
                        v.drive_letter: v
                        for v in VolumeModel.search([('asset_id', '=', asset.id)])
                    }
                    seen_drives = set()

                    created_count = 0
                    for vol in volumes_list:
                        try:
                            with self.env.cr.savepoint():
                                drive = vol.get('drive') or vol.get('drive_letter') or 'Unknown'
                                capacity_gb = vol.get('capacity_gb') or vol.get('total_size', 0.0)
                                used_gb = vol.get('used_gb') or vol.get('used_space', 0.0)
                                available_gb = vol.get('available_gb') or vol.get('free_space', 0.0)
                                drive_type_raw = vol.get('type') or vol.get('drive_label') or 'Data'
                                drive_label = drive_type_raw.lower()
                                valid_labels = dict(VolumeModel._fields['drive_label'].selection)
                                if drive_label not in valid_labels:
                                    drive_label = 'unknown'

                                seen_drives.add(drive)
                                vol_vals = {
                                    'total_size': capacity_gb,
                                    'free_space': available_gb,
                                    'used_space': used_gb,
                                    'drive_label': drive_label,
                                }
                                current = existing_volumes.get(drive)
                                if current:
                                    # Only write genuinely changed values -
                                    # writing identical ones still dirties the
                                    # row and still takes a lock.
                                    changed = {
                                        k: v for k, v in vol_vals.items()
                                        if current[k] != v
                                    }
                                    if changed:
                                        current.write(changed)
                                else:
                                    VolumeModel.create(dict(
                                        vol_vals,
                                        asset_id=asset.id,
                                        drive_letter=drive,
                                    ))
                            created_count += 1
                        except Exception as vol_error:
                            _logger.warning(
                                f"⚠️ Failed to create volume record: {vol.get('drive') or vol.get('drive_letter')} - {vol_error}")
                            continue

                    # Drop only volumes the agent no longer reports - a disk
                    # genuinely removed. Everything still present was updated
                    # in place above, so this normally deletes nothing and
                    # never touches the transaction.
                    stale = [
                        v for drive, v in existing_volumes.items()
                        if drive not in seen_drives
                    ]
                    if stale:
                        for v in stale:
                            asset._log_asset_change(
                                'hardware_change',
                                f"Storage drive removed: {v.drive_letter} "
                                f"({v.total_size} GB)",
                                v.total_size, False)
                        VolumeModel.browse([v.id for v in stale]).unlink()
                        _logger.info("🗑️ Removed %s volume(s) no longer present",
                                     len(stale))

                    # Ensure total logical storage field is populated
                    asset._compute_total_storage()

                except Exception as e:
                    _logger.error(f"❌ Error processing storage volumes: {e}")
                    import traceback
                    _logger.error(traceback.format_exc())
            else:
                _logger.info(f"ℹ️ No storage volumes data received")

            return {
                "success": True,
                "message": f"Asset {asset.asset_name} synced successfully",
                "asset_id": asset.id,
                "apps_synced": len(json.loads(installed_apps_json)) if (
                            installed_apps_json and installed_apps_json != "[]") else 0
            }

        except Exception as e:
            _logger.error(f"❌ sync_from_agent error: {e}")
            import traceback
            _logger.error(traceback.format_exc())
            return {
                "success": False,
                "message": str(e)
            }

    def _log_asset_change(self, log_type, description, old_value=None, new_value=None):
        """One row of hardware/software change history for this asset.

        Reuses asset.audit.log rather than a new model - it already carries
        exactly the fields this needs (asset_id, old_value, new_value,
        description, timestamp), just previously scoped to remote-management
        actions (uninstall/lock) rather than inventory changes.
        """
        self.ensure_one()
        self.env['asset.audit.log'].sudo().create({
            'asset_id': self.id,
            'log_type': log_type,
            'old_value': str(old_value) if old_value not in (None, False) else False,
            'new_value': str(new_value) if new_value not in (None, False) else False,
            'description': description,
        })

    def _log_security_event(self, action, description, old_value=None, new_value=None):
        """OS-level security event reported by the agent (login/logout,
        account/password/sudo changes, a locked path's permissions getting
        overridden) - log_type='security' keeps these out of the generic
        Audit Log's hardware/software noise while still using the same
        table, same as _log_asset_change() above does for inventory
        changes. See controllers/asset_agent_api.py's /api/asset/security
        route for where this gets called from."""
        self.ensure_one()
        self.env['asset.audit.log'].sudo().create({
            'asset_id': self.id,
            'log_type': 'security',
            'action': action,
            'old_value': str(old_value) if old_value not in (None, False) else False,
            'new_value': str(new_value) if new_value not in (None, False) else False,
            'description': description,
        })

    def action_view_agent_logs(self):
        """Open agent logs for this asset"""
        self.ensure_one()
        return {
            "name": _("Agent Logs"),
            "type": "ir.actions.act_window",
            "res_model": "asset.agent.log",
            "view_mode": "list,form",
            "domain": [("asset_id", "=", self.id)],
            "context": {"default_asset_id": self.id}
        }

    def action_mark_changes_reviewed(self):
        """Mark detected changes as reviewed and reset the alert counter."""
        for asset in self:
            asset.write({
                "has_changes": False,
                "change_summary": False,
                "alert_severity": "info",
                "alert_count": 0,
                "last_change_date": False,
            })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Changes Reviewed'),
                'message': _('All changes have been marked as reviewed.'),
                'type': 'success',
                'sticky': False,
            }
        }

    # =====================
    # BULK ACTIONS
    # =====================

    @api.model
    def action_bulk_approve_changes(self, asset_ids):
        """Bulk approve changes for multiple assets"""
        assets = self.browse(asset_ids)
        assets.write({
            "has_changes": False,
            "change_summary": False,
            "alert_severity": "info",
        })
        return True

    def action_schedule_maintenance(self):
        """Quick action to schedule maintenance"""
        self.ensure_one()
        return {
            "name": _("Schedule Maintenance"),
            "type": "ir.actions.act_window",
            "res_model": "asset.bulk.operations",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_operation_type": "maintenance",
                "default_asset_ids": [(6, 0, [self.id])],
            }
        }

    # =====================
    # ✅ CRON METHODS (UPDATED)
    # =====================

    @api.model
    def cron_check_agent_sync_status(self):
        """
        Scheduled action to check agent sync status with heartbeat timeout.
        Updates agent_status and recomputes live metrics for all assets.
        """
        _logger.info("========== CRON: Checking Agent Sync Status ==========")

        heartbeat_timeout = int(
            self.env['ir.config_parameter'].sudo().get_param(
                'asset_management.agent_heartbeat_timeout',
                default='180'
            )
        )

        now = fields.Datetime.now()
        online_threshold = now - timedelta(seconds=heartbeat_timeout)

        to_offline = self.search([
            ('agent_status', '=', 'online'),
            ('last_sync_time', '<', online_threshold),
            ('last_sync_time', '!=', False)
        ])
        if to_offline:
            to_offline.write({'agent_status': 'offline'})
            _logger.info(f"Set {len(to_offline)} assets to 'offline' (not synced in {heartbeat_timeout}s)")

        to_online = self.search([
            ('agent_status', 'in', ['offline', 'idle']),
            ('last_sync_time', '>=', online_threshold),
            ('last_sync_time', '!=', False)
        ])
        if to_online:
            to_online.write({'agent_status': 'online'})
            _logger.info(f"Set {len(to_online)} assets to 'online' (synced within {heartbeat_timeout}s)")

        all_assets = self.search([('serial_number', '!=', False)])
        if all_assets:
            all_assets._compute_live_metrics()
            _logger.info(f"Recomputed live metrics for {len(all_assets)} assets")

        _logger.info("========== CRON: Completed ==========")
        return True

    def action_view_storage_volumes(self):
        """View storage volumes for this asset"""
        self.ensure_one()
        return {
            "name": _("Storage & File Control"),
            "type": "ir.actions.act_window",
            "res_model": "asset.storage.volume",
            "view_mode": "list,form",
            "domain": [("asset_id", "=", self.id)],
            "context": {"default_asset_id": self.id}
        }

    @api.model
    def action_fix_platform_detection(self):
        """
        Fix platform field for existing assets based on os_name.
        Run this once to fix historical data where platform = 'unknown' or not set.
        """
        assets = self.search([
            '|',
            ('platform', '=', 'unknown'),
            ('platform', '=', False)
        ])

        fixed_count = 0
        for asset in assets:
            os_name = (asset.os_name or '').lower()

            new_platform = 'unknown'
            if 'macos' in os_name or 'mac os' in os_name or 'darwin' in os_name:
                new_platform = 'macos'
            elif 'windows' in os_name:
                new_platform = 'windows'
            elif 'ubuntu' in os_name or 'linux' in os_name or 'debian' in os_name:
                new_platform = 'linux'

            if new_platform != 'unknown':
                asset.write({'platform': new_platform})
                fixed_count += 1

        _logger.info(f"Fixed platform field for {fixed_count} assets")
        return True

    @api.model
    def cron_check_warranty_expiry(self):
        """Check for expiring warranties and send alerts"""
        _logger.info("========== CRON: Checking Warranty Expiry ==========")

        today = fields.Date.today()
        expiring_date = today + timedelta(days=30)

        expiring_assets = self.search([
            ("warranty_end_date", "<=", expiring_date),
            ("warranty_end_date", ">=", today),
            ("warranty_status", "=", "expiring"),
        ])

        for asset in expiring_assets:
            days_left = (asset.warranty_end_date - today).days
            asset.message_post(
                body=f"⚠️ Warranty expiring in {days_left} days!",
                subject="Warranty Expiry Alert",
                message_type="notification",
            )

        _logger.info(f"Found {len(expiring_assets)} assets with expiring warranty")
        _logger.info("========== CRON: Completed ==========")
        return True

    @api.model
    def cron_check_maintenance_due(self):
        """Check for assets needing maintenance"""
        _logger.info("========== CRON: Checking Maintenance Due ==========")
        today = fields.Date.today()

        # Assets never maintained
        never_maintained = self.search([
            ("last_maintenance_date", "=", False),
            ("state", "in", ["assigned", "draft", "submit"]),
        ])

        # Assets overdue for maintenance (> 6 months)
        overdue = self.search([
            ("last_maintenance_date", "!=", False),
            ("last_maintenance_date", "<=", today - timedelta(days=180)),
            ("state", "in", ["assigned", "draft", "submit"]),
        ])

        all_due = never_maintained | overdue

        for asset in all_due:
            asset.message_post(
                body=f"🔧 Maintenance check recommended for {asset.asset_name}",
                subject="Maintenance Due",
                message_type="notification",
            )

        _logger.info(f"Found {len(all_due)} assets needing maintenance")
        _logger.info("========== CRON: Completed ==========")

        return True

    @api.model
    @api.model
    def cron_trim_agent_logs(self, keep_per_asset=10):
        """Keep only the newest N sync logs per asset.

        Replaces the inline unlink() that used to run inside sync_from_agent()
        on every heartbeat. Doing it here means one bulk delete on a schedule
        instead of thousands of small ones competing with live writes.
        """
        AgentLog = self.env["asset.agent.log"].sudo()
        # One grouped query instead of one search per asset.
        grouped = AgentLog._read_group(
            [], ["asset_id"], ["__count"])
        to_delete = AgentLog.browse()
        for asset, count in grouped:
            if count <= keep_per_asset:
                continue
            stale = AgentLog.search(
                [("asset_id", "=", asset.id)],
                order="sync_time desc, id desc",
                offset=keep_per_asset,
            )
            to_delete |= stale
        if to_delete:
            _logger.info("[Housekeeping] Trimming %s old agent log(s)",
                         len(to_delete))
            to_delete.unlink()
        return True

    def cron_trim_audit_logs(self, days=180):
        """Delete asset.audit.log rows older than `days`.

        Same reasoning as cron_trim_agent_logs() above (one scheduled bulk
        delete instead of the table growing unbounded), but age-based
        rather than count-based: an audit/security trail is usually kept
        for "the last N months", not "the last N rows" - a quiet asset
        should not have its history cut short just because a noisy one
        pushed the row count up.
        """
        cutoff = fields.Datetime.now() - timedelta(days=days)
        AuditLog = self.env["asset.audit.log"].sudo()
        stale = AuditLog.search([("date", "<", cutoff)])
        if stale:
            _logger.info("[Housekeeping] Trimming %s audit log row(s) older "
                        "than %s days", len(stale), days)
            stale.unlink()
        return True

    def _cron_reverse_geocode_missing_addresses(self):
        """
        Cron job to geocode assets that have coordinates but no address.
        Runs once daily, processes 100 records at a time to respect API limits.
        Uses Nominatim (OpenStreetMap) - free, no API key required.
        Respects rate limit of 1 request per second.
        """
        import time

        _logger.info("========== CRON: Reverse Geocoding Missing Addresses ==========")

        try:
            from odoo.addons.asset_management.controllers.asset_agent_api import reverse_geocode
        except ImportError:
            _logger.error("Could not import reverse_geocode function")
            return False

        # Find assets with coordinates but no address (limit 100 per run)
        assets = self.search([
            ('latitude', '!=', 0.0),
            ('longitude', '!=', 0.0),
            '|',
            ('location_address', '=', False),
            ('location_address', '=', '')
        ], limit=100)

        _logger.info(f"📍 [Geocoding] Found {len(assets)} assets with missing addresses")

        geocoded_count = 0
        failed_count = 0

        for asset in assets:
            try:
                _logger.info(
                    f"📍 [Geocoding] Processing asset {asset.asset_code}: lat={asset.latitude}, lon={asset.longitude}")

                location_info = reverse_geocode(asset.latitude, asset.longitude)

                if location_info:
                    asset.write({
                        'location_address': location_info.get('address', ''),
                        'location_city': location_info.get('city', ''),
                        'location_state': location_info.get('state', ''),
                        'location_country': location_info.get('country', ''),
                        'location_area': location_info.get('area', '')
                    })
                    geocoded_count += 1
                    _logger.info(
                        f"📍 [Geocoding] Successfully geocoded {asset.asset_code}: {location_info.get('city')}, {location_info.get('country')}")
                else:
                    failed_count += 1
                    _logger.warning(f"📍 [Geocoding] No results for {asset.asset_code}")

                # Respect Nominatim rate limit (1 req/sec) - wait 1.5s to be safe
                time.sleep(1.5)

            except Exception as e:
                failed_count += 1
                _logger.error(f"📍 [Geocoding] Error geocoding asset {asset.asset_code}: {e}")
                continue

        _logger.info(f"========== CRON: Completed - Geocoded: {geocoded_count}, Failed: {failed_count} ==========")
        return True

    # =====================
    # ANTIVIRUS DEPLOYMENT
    # =====================

    def action_deploy_antivirus(self):
        """
        Deploy antivirus to selected assets.
        Called from the Antivirus Assets list view (Windows / Linux / macOS tabs).
        Marks selected assets as 'pending' and creates antivirus.deployment records.
        """
        if not self:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'No Assets Selected',
                    'message': 'Please select at least one asset before deploying antivirus.',
                    'type': 'warning',
                    'sticky': False,
                }
            }

        # Get default antivirus config
        config = self.env['antivirus.config'].search(
            [('is_default', '=', True)], limit=1
        )
        if not config:
            config = self.env['antivirus.config'].search([], limit=1)

        Deployment = self.env['antivirus.deployment']
        deployed_count = 0

        for asset in self:
            # Skip if already has active deployment
            existing = Deployment.search([
                ('asset_id', '=', asset.id),
                ('status', 'in', ['pending', 'downloading', 'installing'])
            ], limit=1)
            if existing:
                continue

            # Create deployment record
            Deployment.create({
                'asset_id': asset.id,
                'config_id': config.id if config else False,
                'status': 'pending',
            })
            deployed_count += 1

        # Mark all as pending in asset record
        self.write({'antivirus_status': 'pending'})

        platform = self._context.get('antivirus_platform', False)
        platform_label = {
            'windows': 'Windows', 'linux': 'Linux', 'macos': 'macOS',
        }.get(platform, 'selected')

        _logger.info(
            f"[Antivirus Deploy] Created {deployed_count} deployment records for "
            f"{len(self)} {platform_label} assets"
        )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': '✅ Antivirus Deployment Started',
                'message': (
                    f'Antivirus deployment queued for {deployed_count} {platform_label} asset(s). '
                    f'Agent will download and install automatically.'
                ),
                'type': 'success',
                'sticky': False,
            }
        }

    def action_remove_antivirus(self):
        """
        Remove antivirus protection status from selected assets.
        Sets status back to 'unprotected'.
        """
        asset_count = len(self)
        self.write({'antivirus_status': 'unprotected'})

        _logger.info(f"[Antivirus Remove] Protection status cleared for {asset_count} assets")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': '🛡️ Antivirus Removed',
                'message': f'Antivirus protection status cleared for {asset_count} asset(s).',
                'type': 'info',
                'sticky': False,
            }
        }

    def action_deploy_all_windows(self):
        """
        Deploy antivirus to all unprotected Windows assets.
        Called from the Windows Antivirus form view.
        """
        Asset = self.env['asset.asset']
        unprotected_assets = Asset.search([
            ('os_platform', '=', 'windows'),
            ('antivirus_status', 'in', ['unprotected', 'expired', 'n/a'])
        ])

        if not unprotected_assets:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'No Assets to Protect',
                    'message': 'All Windows assets are already protected or pending.',
                    'type': 'info',
                    'sticky': False,
                }
            }

        # Get default antivirus config
        config = self.env['antivirus.config'].search(
            [('is_default', '=', True)], limit=1
        )
        if not config:
            config = self.env['antivirus.config'].search([], limit=1)

        Deployment = self.env['antivirus.deployment']
        deployed_count = 0

        for asset in unprotected_assets:
            # Skip if already has active deployment
            existing = Deployment.search([
                ('asset_id', '=', asset.id),
                ('status', 'in', ['pending', 'downloading', 'installing'])
            ], limit=1)
            if existing:
                continue

            # Create deployment record
            Deployment.create({
                'asset_id': asset.id,
                'config_id': config.id if config else False,
                'status': 'pending',
            })
            deployed_count += 1

        # Mark all as pending in asset record
        unprotected_assets.write({'antivirus_status': 'pending'})

        _logger.info(
            f"[Antivirus Deploy All] Created {deployed_count} deployment records for {deployed_count} Windows assets")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': '✅ Windows Antivirus Deployment Started',
                'message': f'Antivirus deployment queued for {deployed_count} Windows asset(s).',
                'type': 'success',
                'sticky': False,
            }
        }

    def action_deploy_all_linux(self):
        """
        Deploy antivirus to all unprotected Linux assets.
        Called from the Linux Antivirus form view.
        """
        Asset = self.env['asset.asset']
        unprotected_assets = Asset.search([
            ('os_platform', '=', 'linux'),
            ('antivirus_status', 'in', ['unprotected', 'expired', 'n/a'])
        ])

        if not unprotected_assets:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'No Assets to Protect',
                    'message': 'All Linux assets are already protected or pending.',
                    'type': 'info',
                    'sticky': False,
                }
            }

        # Get default antivirus config
        config = self.env['antivirus.config'].search(
            [('is_default', '=', True)], limit=1
        )
        if not config:
            config = self.env['antivirus.config'].search([], limit=1)

        Deployment = self.env['antivirus.deployment']
        deployed_count = 0

        for asset in unprotected_assets:
            # Skip if already has active deployment
            existing = Deployment.search([
                ('asset_id', '=', asset.id),
                ('status', 'in', ['pending', 'downloading', 'installing'])
            ], limit=1)
            if existing:
                continue

            # Create deployment record
            Deployment.create({
                'asset_id': asset.id,
                'config_id': config.id if config else False,
                'status': 'pending',
            })
            deployed_count += 1

        # Mark all as pending in asset record
        unprotected_assets.write({'antivirus_status': 'pending'})

        _logger.info(
            f"[Antivirus Deploy All] Created {deployed_count} deployment records for {deployed_count} Linux assets")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': '✅ Linux Antivirus Deployment Started',
                'message': f'Antivirus deployment queued for {deployed_count} Linux asset(s).',
                'type': 'success',
                'sticky': False,
            }
        }

    def action_deploy_all_macos(self):
        """
        Deploy antivirus to all unprotected macOS assets.
        Called from the macOS Antivirus form view.
        """
        Asset = self.env['asset.asset']
        unprotected_assets = Asset.search([
            ('os_platform', '=', 'macos'),
            ('antivirus_status', 'in', ['unprotected', 'expired', 'n/a'])
        ])

        if not unprotected_assets:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'No Assets to Protect',
                    'message': 'All macOS assets are already protected or pending.',
                    'type': 'info',
                    'sticky': False,
                }
            }

        # Get default antivirus config
        config = self.env['antivirus.config'].search(
            [('is_default', '=', True)], limit=1
        )
        if not config:
            config = self.env['antivirus.config'].search([], limit=1)

        Deployment = self.env['antivirus.deployment']
        deployed_count = 0

        for asset in unprotected_assets:
            # Skip if already has active deployment
            existing = Deployment.search([
                ('asset_id', '=', asset.id),
                ('status', 'in', ['pending', 'downloading', 'installing'])
            ], limit=1)
            if existing:
                continue

            # Create deployment record
            Deployment.create({
                'asset_id': asset.id,
                'config_id': config.id if config else False,
                'status': 'pending',
            })
            deployed_count += 1

        # Mark all as pending in asset record
        unprotected_assets.write({'antivirus_status': 'pending'})

        _logger.info(
            f"[Antivirus Deploy All] Created {deployed_count} deployment records for {deployed_count} macOS assets")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': '✅ macOS Antivirus Deployment Started',
                'message': f'Antivirus deployment queued for {deployed_count} macOS asset(s).',
                'type': 'success',
                'sticky': False,
            }
        }