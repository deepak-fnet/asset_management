from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import datetime

class AssetNetworkDevice(models.Model):
    _name = 'asset.network.device'
    _description = 'Network Device'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'

    # Basic Info
    name = fields.Char(string="Device Name", required=True, tracking=True)
    device_code = fields.Char(string="Device Code", required=True, tracking=True)
    asset_id = fields.Many2one('asset.asset', string="Related Asset", ondelete='cascade', tracking=True)
    device_type = fields.Selection([
        ('router', 'Router'),
        ('switch', 'Switch'),
        ('firewall', 'Firewall'),
        ('access_point', 'Access Point'),
        ('gateway', 'Gateway'),
        ('printer', 'Printer'),
        ('tldr', 'NVR / DVR (Video Recorder)'),
        ('endpoint', 'Computer / Phone (Unidentified)'),
        ('unknown', 'Unknown'),
    ], string="Device Type", required=True, tracking=True)

    # ── Discovery metadata ──────────────────────────────────────────────
    # These are populated only by network.discovery.service. They exist so an
    # admin can tell, from the list view alone, whether a "Router" row is a
    # real router or just a host that answered ping but not SNMP.
    discovery_state = fields.Selection(
        [('placeholder', 'Placeholder (Ping Only)'),
         ('snmp_confirmed', 'SNMP Confirmed')],
        string="Discovery State", tracking=True,
        help="Set automatically by network discovery. 'Placeholder' means "
             "only a ping response was seen; SNMP has not confirmed what "
             "this device actually is.")

    mac_address = fields.Char(string="MAC Address", tracking=True, index=True,
                              help="Read from the ARP table at scan time. "
                                   "Not available across subnets/VLANs, and "
                                   "many phones randomise this per network.")
    ttl_observed = fields.Integer(
        string="Observed TTL",
        help="Raw TTL from the ping reply. Used only to guess the OS family "
             "of ping-only hosts (~64 suggests Linux/macOS/mobile, ~128 "
             "suggests Windows, ~255 suggests network equipment). This is a "
             "heuristic, not identification - it is the same starting value "
             "on many different device kinds.")
    os_guess = fields.Selection(
        [('windows', 'Windows'),
         ('linux_mac', 'Linux / macOS'),
         ('mobile_or_unknown', 'Mobile / Unclear'),
         ('network_device', 'Network Equipment'),
         ('unknown', 'Unknown')],
        string="OS Guess (Heuristic)",
        help="Best-effort guess from TTL and open ports for hosts that did "
             "not answer SNMP. Treat as a lead to investigate, not a fact.")
    open_ports = fields.Char(
        string="Open Ports Detected",
        help="Comma-separated TCP ports found open during discovery "
             "(a small fixed probe list, not a full port scan).")
    is_unmanaged_endpoint = fields.Boolean(
        string="Unmanaged Endpoint", index=True, tracking=True,
        help="Looks like a computer or phone by TTL/port signature, but no "
             "asset or mobile device with a matching IP or MAC was found. "
             "This is the field to filter on to find laptops/phones on the "
             "network that do not have the agent installed.")
    category_id = fields.Many2one('asset.category', string="Category", tracking=True)
    location = fields.Char(string="Physical Location", tracking=True)

    # HR / Assignment
    assigned_employee_id = fields.Many2one('hr.employee', related='asset_id.assigned_employee_id', string="Assigned Employee", store=True, readonly=True)
    department_id = fields.Many2one('hr.department', related='asset_id.department_id', string="Department", store=True, readonly=True)
    assignment_date = fields.Date(related='asset_id.assignment_date', string="Assignment Date", store=True, readonly=True)
    manufacturer = fields.Selection([
        # Network gear brands
        ('cisco', 'Cisco'),
        ('juniper', 'Juniper'),
        ('hp', 'HP / Aruba'),
        ('dell', 'Dell'),
        ('mikrotik', 'MikroTik'),
        ('ubiquiti', 'Ubiquiti'),
        ('tp_link', 'TP-Link'),
        ('fortinet', 'Fortinet'),
        # Printer brands
        ('canon', 'Canon'),
        ('brother', 'Brother'),
        ('epson', 'Epson'),
        ('samsung', 'Samsung'),
        ('kyocera', 'Kyocera'),
        ('lexmark', 'Lexmark'),
        ('ricoh', 'Ricoh'),
        ('konica_minolta', 'Konica Minolta'),
        ('xerox', 'Xerox'),
        ('oki', 'OKI'),
        # NVR / DVR brands
        ('hikvision', 'Hikvision'),
        ('dahua', 'Dahua'),
        ('uniview', 'Uniview'),
        ('cp_plus', 'CP Plus'),
        ('axis', 'Axis'),
        # Fallback
        ('generic', 'Generic'),
    ], string="Manufacturer", tracking=True)

    # Connection
    ip_address = fields.Char(string="IP Address", required=True, tracking=True)
    snmp_port = fields.Integer(string="SNMP Port", default=161)
    snmp_version = fields.Selection([
        ('v1', 'v1'),
        ('v2c', 'v2c'),
        ('v3', 'v3')
    ], string="SNMP Version", default='v2c')
    snmp_community = fields.Char(string="SNMP Community String", default="public")
    snmp_username = fields.Char(string="SNMP v3 Username")
    snmp_auth_protocol = fields.Selection([
        ('MD5', 'MD5'),
        ('SHA', 'SHA')
    ], string="Auth Protocol")
    snmp_auth_password = fields.Char(string="Auth Password")
    snmp_priv_protocol = fields.Selection([
        ('DES', 'DES'),
        ('AES', 'AES')
    ], string="Privacy Protocol")
    snmp_priv_password = fields.Char(string="Privacy Password")

    # UI & Media
    image_1920 = fields.Image(string="Device Image", max_width=1920, max_height=1920)
    qr_code = fields.Binary(string="QR Code", compute="_compute_qr_code", store=True)

    # Location
    latitude = fields.Float(string="Latitude", digits=(10, 6), readonly=True, tracking=True)
    longitude = fields.Float(string="Longitude", digits=(10, 6), readonly=True, tracking=True)
    location_source = fields.Selection([
        ("gps", "GPS"),
        ("ip", "IP-based"),
        ("unavailable", "Unavailable"),
    ], string="Location Source", default="unavailable", readonly=True, tracking=True)
    last_location_update = fields.Datetime(string="Last Location Update", readonly=True, tracking=True)
    location_address = fields.Char(string="Location Address", readonly=True)
    location_city = fields.Char(string="City", readonly=True)
    location_state = fields.Char(string="State/Region", readonly=True)
    location_country = fields.Char(string="Country", readonly=True)
    location_area = fields.Char(string="Area/Neighborhood", readonly=True)

    has_location_coordinates = fields.Boolean(
        string="Has Location Coordinates",
        compute="_compute_has_location_coordinates"
    )
    map_url = fields.Char(string="Map URL", compute="_compute_map_url")

    # Status
    connection_status = fields.Selection([
        ('online', 'Online'),
        ('offline', 'Offline'),
        ('unreachable', 'Unreachable'),
        ('unknown', 'Unknown')
    ], string="Connection Status", default='unknown', tracking=True)
    last_check = fields.Datetime(string="Last Checked")
    last_online = fields.Datetime(string="Last Online")
    response_time = fields.Float(string="Response Time (ms)")
    uptime = fields.Char(string="System Uptime")
    uptime_seconds = fields.Integer(string="Uptime in Seconds")

    # Performance Metrics
    cpu_usage = fields.Float(string="CPU Usage %")
    memory_usage = fields.Float(string="Memory Usage %")
    memory_total = fields.Float(string="Total Memory (MB)")
    memory_used = fields.Float(string="Used Memory (MB)")
    ram_total = fields.Float(string="Total RAM (GB)")
    ram_used = fields.Float(string="Used RAM (GB)")

    # Interface Stats
    total_interfaces = fields.Integer(string="Total Interfaces")
    active_interfaces = fields.Integer(string="Active Interfaces")
    interface_ids = fields.One2many('network.device.interface', 'device_id', string="Network Interfaces")

    # General
    is_active = fields.Boolean(string="Active", default=True)
    notes = fields.Text(string="Notes")
    firmware_version = fields.Char(string="Firmware Version")
    serial_number = fields.Char(string="Serial Number")

    # Change Detection
    has_changes = fields.Boolean(string="Has Unreviewed Changes", default=False)
    alert_severity = fields.Selection([
        ("info", "Info"),
        ("warning", "Warning"),
        ("critical", "Critical"),
    ], string="Alert Severity", default="info")
    last_change_date = fields.Datetime(string="Last Change Detected", tracking=True)
    last_change_date_formatted = fields.Char(
        string="Last Change Date Formatted",
        compute="_compute_last_change_date_formatted"
    )

    # Procurement
    purchase_date = fields.Date(string="Purchase Date", tracking=True)
    purchase_cost = fields.Float(string="Purchase Cost", tracking=True)
    vendor_id = fields.Many2one('res.partner', string="Vendor", tracking=True)

    @api.constrains('device_code')
    def _check_unique_device_code(self):
        for record in self:
            if self.search_count([('device_code', '=', record.device_code), ('id', '!=', record.id)]) > 0:
                raise ValidationError('The Device Code must be unique!')

    def _compute_last_change_date_formatted(self):
        for record in self:
            if record.last_change_date:
                record.last_change_date_formatted = record.last_change_date.strftime("%d/%m/%Y %H:%M:%S")
            else:
                record.last_change_date_formatted = ""

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('device_code'):
                vals['device_code'] = self.env['ir.sequence'].next_by_code('asset.asset.network') or 'NET-00001'
        return super().create(vals_list)

    @api.depends("serial_number", "device_code")
    def _compute_qr_code(self):
        try:
            import qrcode
            import base64
            from io import BytesIO
        except ImportError:
            for device in self:
                device.qr_code = False
            return

        for device in self:
            try:
                if device.serial_number:
                    qr = qrcode.QRCode(version=1, box_size=10, border=5)
                    qr_data = f"Device: {device.device_code}\nSerial: {device.serial_number}"
                    qr.add_data(qr_data)
                    qr.make(fit=True)
                    img = qr.make_image(fill_color="black", back_color="white")
                    buffer = BytesIO()
                    img.save(buffer, format="PNG")
                    device.qr_code = base64.b64encode(buffer.getvalue())
                else:
                    device.qr_code = False
            except Exception:
                device.qr_code = False

    @api.depends('latitude', 'longitude')
    def _compute_has_location_coordinates(self):
        for device in self:
            device.has_location_coordinates = (device.latitude != 0.0 or device.longitude != 0.0)

    @api.depends('latitude', 'longitude')
    def _compute_map_url(self):
        for device in self:
            if device.latitude != 0.0 or device.longitude != 0.0:
                device.map_url = f"https://www.openstreetmap.org/?mlat={device.latitude}&mlon={device.longitude}#map=15/{device.latitude}/{device.longitude}"
            else:
                device.map_url = False

    @api.depends("device_code", "name")
    def _compute_display_name(self):
        # Odoo 19: name_get() was removed in favour of _compute_display_name().
        for record in self:
            record.display_name = f"[{record.device_code}] {record.name}"

    def check_device_status(self):
        """Ping and SNMP check, update all status fields"""
        monitor = self.env['snmp.monitor.service']
        for record in self:
            status = 'offline'
            response_time = 0.0

            start_time = fields.Datetime.now()
            is_alive = monitor.ping_device(record.ip_address)
            end_time = fields.Datetime.now()

            if is_alive:
                status = 'online'
                delta = (end_time - start_time).total_seconds() * 1000
                response_time = delta

                snmp_ok = monitor.test_snmp_connection(record)
                if snmp_ok:
                    sys_info = monitor.get_system_info(record)
                    if sys_info:
                        vals = {
                            'uptime': sys_info.get('sysUpTime'),
                            'notes': sys_info.get('sysDescr'),
                            'last_online': fields.Datetime.now(),
                        }
                        metrics = monitor.get_device_metrics(record)
                        vals.update(metrics)
                        record.write(vals)
                else:
                    status = 'unreachable'

            record.write({
                'last_check': fields.Datetime.now(),
                'connection_status': status,
                'response_time': response_time,
            })

    def test_snmp_connection(self):
        """Button method to test SNMP connectivity"""
        self.ensure_one()
        monitor = self.env['snmp.monitor.service']
        success = monitor.test_snmp_connection(self)

        title = _('SNMP Test Success') if success else _('SNMP Test Failed')
        message = _('Successfully connected to %s via SNMP.') % self.ip_address if success else \
                  _('Failed to connect to %s. Please check settings and connectivity.') % self.ip_address
        msg_type = 'success' if success else 'danger'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': msg_type,
                'sticky': False,
            }
        }

    def get_system_info(self):
        """Fetch device info via SNMP and update fields"""
        self.ensure_one()
        monitor = self.env['snmp.monitor.service']
        sys_info = monitor.get_system_info(self)

        if sys_info:
            self.write({
                'uptime': sys_info.get('sysUpTime'),
                'notes': sys_info.get('sysDescr'),
                'last_check': fields.Datetime.now(),
            })
            return True
        return False

    def refresh_interfaces(self):
        """Discover and update interface list"""
        self.ensure_one()
        monitor = self.env['snmp.monitor.service']
        if_list = monitor.get_interface_list(self)

        if not if_list:
            return False

        Interface = self.env['network.device.interface']
        for if_data in if_list:
            existing = Interface.search([
                ('device_id', '=', self.id),
                ('interface_index', '=', int(if_data.get('index', 0)))
            ], limit=1)

            vals = {
                'name': if_data.get('descr', 'Unknown'),
                'device_id': self.id,
                'interface_index': int(if_data.get('index', 0)),
                'interface_type': if_data.get('type'),
                'mtu': int(if_data.get('mtu', 0)) if if_data.get('mtu') else 0,
                'speed': if_data.get('speed'),
                'mac_address': if_data.get('phys_addr'),
                'admin_status': self._map_snmp_status(if_data.get('admin_status')),
                'oper_status': self._map_snmp_status(if_data.get('oper_status')),
                'bytes_in': float(if_data.get('bytes_in', 0)),
                'bytes_out': float(if_data.get('bytes_out', 0)),
                'packets_in': float(if_data.get('pkts_in', 0)),
                'packets_out': float(if_data.get('pkts_out', 0)),
                'errors_in': int(if_data.get('err_in', 0)) if if_data.get('err_in') else 0,
                'errors_out': int(if_data.get('err_out', 0)) if if_data.get('err_out') else 0,
            }

            if existing:
                vals.update({
                    'last_bytes_in': existing.bytes_in,
                    'last_bytes_out': existing.bytes_out,
                    'last_stats_update': existing.write_date or fields.Datetime.now(),
                })
                existing.write(vals)
            else:
                Interface.create(vals)

        self.total_interfaces = len(if_list)
        self.active_interfaces = len([i for i in if_list if i.get('oper_status') == '1'])
        return True

    def _map_snmp_status(self, snmp_val):
        """Map RFC1213 status to selection values"""
        mapping = {
            '1': 'up', '2': 'down', '3': 'testing',
            '4': 'unknown', '5': 'dormant',
            '6': 'notPresent', '7': 'lowerLayerDown'
        }
        return mapping.get(str(snmp_val), 'unknown')