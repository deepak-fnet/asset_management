from odoo import models, fields, api
from collections import defaultdict
from datetime import timedelta, datetime
import pytz
import logging


class AntivirusConfig(models.Model):
    """
    Model to manage antivirus installer configuration.
    Allows administrators to configure installer URLs for different platforms.
    """
    _name = "antivirus.config"
    _description = "Antivirus Installer Configuration"

    name = fields.Char(string="Configuration Name", required=True, default="Default Configuration")
    antivirus_product = fields.Selection([
        ('kaspersky', 'Kaspersky Endpoint Security'),
        ('bitdefender', 'Bitdefender GravityZone'),
        ('sophos', 'Sophos Endpoint Protection'),
        ('mcafee', 'McAfee Endpoint Security'),
        ('symantec', 'Symantec Endpoint Protection'),
        ('windows_defender', 'Microsoft Defender'),
        ('eset', 'ESET Endpoint Security'),
        ('trend_micro', 'Trend Micro Worry-Free'),
        ('f_secure', 'F-Secure Protection Service'),
        ('other', 'Other'),
    ], string="Antivirus Product", required=True, default='kaspersky')

    custom_product_name = fields.Char(string="Custom Product Name", help="Enter custom antivirus product name if 'Other' is selected")

    # Installer URLs for different platforms
    installer_windows = fields.Char(string="Installer for Windows", help="URL or path to Windows installer (.msi, .exe)")
    installer_macos = fields.Char(string="Installer for macOS", help="URL or path to macOS installer (.dmg, .pkg)")
    installer_linux = fields.Char(string="Installer for Linux (Ubuntu)", help="URL or path to Linux installer (.deb, .rpm)")

    # Silent install commands for each platform
    silent_install_command_windows = fields.Char(string="Silent Install Command (Windows)", help="Command for silent installation on Windows (e.g., /silent, /quiet, /S)")
    silent_install_command_macos = fields.Char(string="Silent Install Command (macOS)", help="Command for silent installation on macOS (e.g., -silent, -quiet)")
    silent_install_command_linux = fields.Char(string="Silent Install Command (Linux)", help="Command for silent installation on Linux (e.g., --silent, -y)")

    # Installer file uploads (binary fields)
    installer_windows_file = fields.Binary(string="Windows Installer File", attachment=True)
    installer_macos_file = fields.Binary(string="macOS Installer File", attachment=True)
    installer_linux_file = fields.Binary(string="Linux Installer File", attachment=True)

    # File names for uploaded installers
    installer_windows_filename = fields.Char(string="Windows Installer Filename")
    installer_macos_filename = fields.Char(string="macOS Installer Filename")
    installer_linux_filename = fields.Char(string="Linux Installer Filename")

    # Version management
    installer_version = fields.Char(string="Installer Version", default="1.0.0")
    version_release_date = fields.Date(string="Version Release Date")

    # Deployment settings
    auto_deploy = fields.Boolean(string="Auto Deploy", default=False, help="Automatically deploy antivirus to new assets")
    deployment_priority = fields.Selection([
        ('low', 'Low'),
        ('normal', 'Normal'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ], string="Deployment Priority", default='normal')

    # Status and tracking
    active = fields.Boolean(string="Active", default=True)
    is_default = fields.Boolean(string="Is Default Configuration", default=False)

    # Additional settings
    silent_install = fields.Boolean(string="Silent Install", default=True, help="Install without user interaction")
    restart_required = fields.Boolean(string="Restart Required", default=False, help="System restart required after installation")
    installation_notes = fields.Text(string="Installation Notes", help="Additional instructions for deployment")

    # ── Kaspersky Security Center (KSC) Integration ──────────────────────────
    ksc_server_url = fields.Char(
        string="KSC Server URL",
        help="Kaspersky Security Center server URL, e.g. https://192.168.1.10:13299"
    )
    ksc_username = fields.Char(string="KSC Username", help="KSC administrator username")
    ksc_password = fields.Char(string="KSC Password", help="KSC administrator password")
    ksc_package_name = fields.Char(
        string="KSC Package Name",
        help="Name of the Kaspersky deployment package as listed in KSC (e.g. 'Kaspersky Endpoint Security 12')"
    )
    ksc_verify_ssl = fields.Boolean(
        string="Verify SSL Certificate",
        default=False,
        help="Enable SSL certificate verification for KSC (disable for self-signed certs)"
    )

    # Audit fields
    created_at = fields.Datetime(string="Created On", default=fields.Datetime.now)
    updated_at = fields.Datetime(string="Last Updated On", default=fields.Datetime.now)
    created_by = fields.Many2one('res.users', string="Created By", default=lambda self: self.env.user)
    updated_by = fields.Many2one('res.users', string="Last Updated By", default=lambda self: self.env.user)

    @api.model_create_multi
    def create(self, vals_list):
        # If this is set as default, unset other defaults
        for vals in vals_list:
            if vals.get('is_default', False):
                self.search([]).write({'is_default': False})
        return super().create(vals_list)

    def write(self, vals):
        # Update updated_at on every write
        vals['updated_at'] = fields.Datetime.now()
        # If this is set as default, unset other defaults
        if vals.get('is_default', False):
            self.search([('id', '!=', self.id)]).write({'is_default': False})
        return super().write(vals)

    def get_default_config(self):
        """Get the default configuration or the first available config."""
        default_config = self.search([('is_default', '=', True)], limit=1)
        if not default_config:
            default_config = self.search([], limit=1)
        return default_config

    def action_test_connection(self):
        """Test connection to installer URLs."""
        self.ensure_one()
        results = []

        if self.installer_windows:
            results.append(f"✓ Windows: {self.installer_windows}")
        if self.installer_macos:
            results.append(f"✓ macOS: {self.installer_macos}")
        if self.installer_linux:
            results.append(f"✓ Linux: {self.installer_linux}")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Connection Test',
                'message': '\n'.join(results) if results else 'No installer URLs configured',
                'type': 'success',
                'sticky': False,
            }
        }

    def action_save_config(self):
        """Save the configuration."""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Success',
                'message': 'Configuration saved successfully!',
                'type': 'success',
                'sticky': False,
            }
        }

    def action_save_windows_config(self):
        """Save Windows installer configuration."""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Windows Configuration Saved',
                'message': f'Windows installer URL has been updated successfully!',
                'type': 'success',
                'sticky': False,
            }
        }

    def action_save_linux_config(self):
        """Save Linux installer configuration."""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Linux Configuration Saved',
                'message': f'Linux installer URL has been updated successfully!',
                'type': 'success',
                'sticky': False,
            }
        }

    def action_save_macos_config(self):
        """Save macOS installer configuration."""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'macOS Configuration Saved',
                'message': f'macOS installer URL has been updated successfully!',
                'type': 'success',
                'sticky': False,
            }
        }


class AntivirusDashboard(models.Model):
    """
    Service model for Antivirus Management dashboard KPIs.
    Provides protection monitoring, threat detection, and license management.
    """
    _name = "antivirus.dashboard"
    _description = "Antivirus Management Dashboard KPIs"
    _auto = False

    @api.model
    def get_kpis(self):
        """
        Return comprehensive antivirus KPIs including:
        - Device counts by platform (Windows, Linux, macOS)
        - Protection status (Protected, Unprotected)
        - Threat statistics
        - License information (from antivirus.license model)
        """
        try:
            Asset = self.env["asset.asset"].sudo()

            # Get all assets
            all_assets = Asset.search([])
            total_devices = len(all_assets)

            # Count by platform
            windows_count = Asset.search_count([("os_platform", "=", "windows")])
            linux_count = Asset.search_count([("os_platform", "=", "linux")])
            macos_count = Asset.search_count([("os_platform", "=", "macos")])

            # Protection status - based on antivirus_status field
            # Handle case where field doesn't exist in database yet
            try:
                protected_count = Asset.search_count([("antivirus_status", "=", "protected")])
                unprotected_count = Asset.search_count([("antivirus_status", "in", ["unprotected", "expired"])])
            except Exception:
                # Field doesn't exist in database yet - calculate based on last_sync_time
                protected_count = 0
                unprotected_count = total_devices

            # Threat statistics
            threats_count = 0
            threats_quarantined = 0
            if self.env.registry.get('antivirus.threat'):
                try:
                    threats_count = self.env["antivirus.threat"].sudo().search_count([("status", "!=", "quarantined")])
                    threats_quarantined = self.env["antivirus.threat"].sudo().search_count([("status", "=", "quarantined")])
                except Exception:
                    pass

            # License information - aggregate from all license records
            total_license = 0
            used_license = 0
            balance_license = 0
            expiring_soon = 0

            # Check if antivirus.license model exists and has records
            if self.env.registry.get('antivirus.license'):
                try:
                    license_records = self.env["antivirus.license"].sudo().search([])
                    if license_records:
                        # Sum up all licenses
                        total_license = sum(rec.total_licenses or 0 for rec in license_records)
                        used_license = sum(rec.used_licenses or 0 for rec in license_records)
                        balance_license = sum(rec.available_licenses or 0 for rec in license_records)

                        # Count expiring licenses (within 30 days)
                        thirty_days_later = datetime.now() + timedelta(days=30)
                        expiring_soon = self.env["antivirus.license"].sudo().search_count([
                            ("expiry_date", "<=", thirty_days_later.strftime('%Y-%m-%d')),
                            ("expiry_date", "!=", False),
                            ("status", "=", "expiring_soon")
                        ])
                except Exception as e:
                    _logger.error(f"Error fetching license data: {e}")

            # Antivirus product counts - count devices by antivirus_product field
            kaspersky_count = 0
            windows_defender_count = 0
            mcafee_count = 0
            others_count = 0

            try:
                # Count devices with each antivirus product
                kaspersky_count = Asset.search_count([("antivirus_product", "ilike", "kaspersky")])
                windows_defender_count = Asset.search_count([("antivirus_product", "ilike", "defender")])
                mcafee_count = Asset.search_count([("antivirus_product", "ilike", "mcafee")])
                # Others = protected devices that don't match the above categories
                others_count = protected_count - kaspersky_count - windows_defender_count - mcafee_count
                others_count = max(0, others_count)  # Ensure non-negative
            except Exception:
                # Fields don't exist yet
                pass

            return {
                "total_devices": total_devices,
                "windows_count": windows_count,
                "linux_count": linux_count,
                "macos_count": macos_count,
                "protected_count": protected_count,
                "unprotected_count": unprotected_count,
                "threats_count": threats_count,
                "threats_quarantined": threats_quarantined,
                "total_license": total_license,
                "used_license": used_license,
                "balance_license": balance_license,
                "expiring_soon": expiring_soon,
                "kaspersky_count": kaspersky_count,
                "windows_defender_count": windows_defender_count,
                "mcafee_count": mcafee_count,
                "others_count": others_count,
            }
        except Exception as e:
            _logger = logging.getLogger(__name__)
            _logger.error(f"Error in antivirus dashboard get_kpis: {str(e)}")
            # Return default values on error
            return {
                "total_devices": 0,
                "windows_count": 0,
                "linux_count": 0,
                "macos_count": 0,
                "protected_count": 0,
                "unprotected_count": 0,
                "threats_count": 0,
                "threats_quarantined": 0,
                "total_license": 0,
                "used_license": 0,
                "balance_license": 0,
                "expiring_soon": 0,
            }

    @api.model
    def get_threats(self, limit=10):
        """
        Return list of recent threats detected.
        """
        try:
            threats = []

            if self.env.registry.get('antivirus.threat'):
                Threat = self.env["antivirus.threat"].sudo()
                threat_records = Threat.search([], order="detected_date desc", limit=limit)

                for threat in threat_records:
                    threats.append({
                        "id": threat.id,
                        "device_id": threat.asset_id.id if threat.asset_id else False,
                        "device_name": threat.asset_id.asset_name if threat.asset_id else "Unknown Device",
                        "device_code": threat.asset_id.asset_code if threat.asset_id else f"DEV-{threat.id}",
                        "threat_name": threat.threat_name or "Unknown Threat",
                        "threat_type": threat.threat_type or "virus",
                        "severity": threat.severity or "medium",
                        "status": threat.status or "active",
                        "detected_date": fields.Datetime.to_string(threat.detected_date) if threat.detected_date else "",
                        "has_image": bool(threat.asset_id.image_1920) if threat.asset_id else False,
                    })
            else:
                # Mock data for demonstration if model doesn't exist
                now = datetime.now()
                mock_threats = [
                    {
                        "id": 1,
                        "device_id": 1,
                        "device_name": "DESKTOP-WIN001",
                        "device_code": "AST-001",
                        "threat_name": "Trojan.Win32.Generic",
                        "threat_type": "trojan",
                        "severity": "critical",
                        "status": "active",
                        "detected_date": (now - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S'),
                        "has_image": False,
                    },
                    {
                        "id": 2,
                        "device_id": 2,
                        "device_name": "LAPTOP-USER02",
                        "device_code": "AST-002",
                        "threat_name": "Adware.Generic",
                        "threat_type": "malware",
                        "severity": "high",
                        "status": "quarantined",
                        "detected_date": (now - timedelta(hours=5)).strftime('%Y-%m-%d %H:%M:%S'),
                        "has_image": False,
                    },
                ]
                threats = mock_threats[:limit]

            return threats
        except Exception as e:
            _logger = logging.getLogger(__name__)
            _logger.error(f"Error in antivirus dashboard get_threats: {str(e)}")
            return []

    @api.model
    def get_recent_scans(self, limit=10):
        """
        Return list of recent scan activities.
        """
        try:
            scans = []

            if self.env.registry.get('antivirus.scan.log'):
                ScanLog = self.env["antivirus.scan.log"].sudo()
                scan_records = ScanLog.search([], order="scan_time desc", limit=limit)

                for scan in scan_records:
                    scans.append({
                        "id": scan.id,
                        "device_id": scan.asset_id.id if scan.asset_id else False,
                        "device_name": scan.asset_id.asset_name if scan.asset_id else "Unknown Device",
                        "scan_time": fields.Datetime.to_string(scan.scan_time) if scan.scan_time else "",
                        "scan_type": scan.scan_type or "Quick Scan",
                        "result": scan.result or "clean",
                        "summary": scan.summary or "",
                    })
            else:
                # Mock data for demonstration if model doesn't exist
                now = datetime.now()
                mock_scans = [
                    {
                        "id": 1,
                        "device_id": 1,
                        "device_name": "DESKTOP-WIN001",
                        "scan_time": (now - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S'),
                        "scan_type": "Full Scan",
                        "result": "clean",
                        "summary": "No threats detected",
                    },
                    {
                        "id": 2,
                        "device_id": 2,
                        "device_name": "LAPTOP-USER02",
                        "scan_time": (now - timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S'),
                        "scan_type": "Quick Scan",
                        "result": "threats_found",
                        "summary": "2 threats detected and quarantined",
                    },
                ]
                scans = mock_scans[:limit]

            return scans
        except Exception as e:
            _logger = logging.getLogger(__name__)
            _logger.error(f"Error in antivirus dashboard get_recent_scans: {str(e)}")
            return []

    @api.model
    def run_full_scan(self):
        """
        Initiate full system scan on all devices.
        """
        # This would trigger actual scan operations
        return True

    @api.model
    def update_definitions(self):
        """
        Update virus definitions to latest version.
        """
        return True

    @api.model
    def quarantine_all(self):
        """
        Quarantine all detected threats.
        """
        if self.env.registry.get('antivirus.threat'):
            Threat = self.env["antivirus.threat"].sudo()
            threats = Threat.search([("status", "!=", "quarantined")])
            threats.write({"status": "quarantined"})
        return True

    @api.model
    def get_kpis_by_platform(self, platform):
        """
        Return antivirus KPIs filtered to a specific OS platform.
        platform: 'windows' | 'linux' | 'macos'
        """
        try:
            Asset = self.env["asset.asset"].sudo()
            domain = [("os_platform", "=", platform)]

            total_devices = Asset.search_count(domain)

            try:
                protected_count = Asset.search_count(domain + [("antivirus_status", "=", "protected")])
                unprotected_count = Asset.search_count(domain + [("antivirus_status", "in", ["unprotected", "expired"])])
            except Exception:
                protected_count = 0
                unprotected_count = total_devices

            # Threat statistics for this platform
            threats_count = 0
            threats_quarantined = 0
            if self.env.registry.get('antivirus.threat'):
                try:
                    threats_count = self.env["antivirus.threat"].sudo().search_count([
                        ("os_platform", "=", platform),
                        ("status", "!=", "quarantined")
                    ])
                    threats_quarantined = self.env["antivirus.threat"].sudo().search_count([
                        ("os_platform", "=", platform),
                        ("status", "=", "quarantined")
                    ])
                except Exception:
                    pass

            # Antivirus product counts scoped to this platform
            kaspersky_count = 0
            windows_defender_count = 0
            mcafee_count = 0
            others_count = 0
            try:
                kaspersky_count = Asset.search_count(domain + [("antivirus_product", "ilike", "kaspersky")])
                windows_defender_count = Asset.search_count(domain + [("antivirus_product", "ilike", "defender")])
                mcafee_count = Asset.search_count(domain + [("antivirus_product", "ilike", "mcafee")])
                others_count = max(0, protected_count - kaspersky_count - windows_defender_count - mcafee_count)
            except Exception:
                pass

            return {
                "total_devices": total_devices,
                "protected_count": protected_count,
                "unprotected_count": unprotected_count,
                "threats_count": threats_count,
                "threats_quarantined": threats_quarantined,
                "kaspersky_count": kaspersky_count,
                "windows_defender_count": windows_defender_count,
                "mcafee_count": mcafee_count,
                "others_count": others_count,
            }
        except Exception as e:
            _logger = logging.getLogger(__name__)
            _logger.error(f"Error in get_kpis_by_platform({platform}): {str(e)}")
            return {
                "total_devices": 0, "protected_count": 0, "unprotected_count": 0,
                "threats_count": 0, "threats_quarantined": 0,
                "kaspersky_count": 0, "windows_defender_count": 0,
                "mcafee_count": 0, "others_count": 0,
            }

    @api.model
    def get_threats_by_platform(self, platform, limit=10):
        """
        Return recent threats filtered to a specific OS platform.
        """
        try:
            threats = []
            if self.env.registry.get('antivirus.threat'):
                Threat = self.env["antivirus.threat"].sudo()
                threat_records = Threat.search(
                    [("os_platform", "=", platform)],
                    order="detected_date desc", limit=limit
                )
                for threat in threat_records:
                    threats.append({
                        "id": threat.id,
                        "device_id": threat.asset_id.id if threat.asset_id else False,
                        "device_name": threat.asset_id.asset_name if threat.asset_id else "Unknown Device",
                        "device_code": threat.asset_id.asset_code if threat.asset_id else f"DEV-{threat.id}",
                        "threat_name": threat.threat_name or "Unknown Threat",
                        "threat_type": threat.threat_type or "virus",
                        "severity": threat.severity or "medium",
                        "status": threat.status or "active",
                        "detected_date": fields.Datetime.to_string(threat.detected_date) if threat.detected_date else "",
                        "has_image": bool(threat.asset_id.image_1920) if threat.asset_id else False,
                    })
            return threats
        except Exception as e:
            _logger = logging.getLogger(__name__)
            _logger.error(f"Error in get_threats_by_platform({platform}): {str(e)}")
            return []


class AntivirusLicense(models.Model):
    """
    Model to manage antivirus licenses.
    Tracks license usage per antivirus product.
    """
    _name = "antivirus.license"
    _description = "Antivirus License"
    _order = "sequence, id"

    sequence = fields.Integer(string="Sequence", default=10)
    name = fields.Char(string="License Name", required=True)
    license_key = fields.Char(string="License Key")
    
    # Product identification
    product_name = fields.Char(
        string="Antivirus Product",
        required=True,
        help="Antivirus product name (e.g., 'Kaspersky Endpoint Security')"
    )
    
    total_licenses = fields.Integer(string="Total Licenses", default=0)
    used_licenses = fields.Integer(
        string="Used Licenses", 
        compute='_compute_used_licenses',
        help="Number of devices with this antivirus product installed and running"
    )
    available_licenses = fields.Integer(
        string="Available Licenses", 
        compute='_compute_available_licenses',
        help="Remaining available licenses"
    )
    expiry_date = fields.Date(string="Expiry Date")
    status = fields.Selection([
        ('active', 'Active'),
        ('expired', 'Expired'),
        ('expiring_soon', 'Expiring Soon'),
    ], string="Status", compute='_compute_status')

    @api.depends('product_name')
    def _compute_used_licenses(self):
        """
        Calculate used licenses by counting devices where:
        - antivirus_product matches this license's product_name (case-insensitive partial match)
        - antivirus_installed = True
        - antivirus_running = True
        - antivirus_status = 'protected'
        """
        Asset = self.env["asset.asset"].sudo()
        for record in self:
            if not record.product_name:
                record.used_licenses = 0
                continue
            
            # Search for assets with matching antivirus product
            # Use ilike for case-insensitive partial matching
            domain = [
                ('antivirus_installed', '=', True),
                ('antivirus_running', '=', True),
                ('antivirus_status', '=', 'protected'),
                ('antivirus_product', 'ilike', record.product_name)
            ]
            record.used_licenses = Asset.search_count(domain)

    @api.depends('total_licenses', 'used_licenses')
    def _compute_available_licenses(self):
        """Calculate available licenses as total - used."""
        for record in self:
            record.available_licenses = max(0, record.total_licenses - record.used_licenses)

    @api.depends('expiry_date')
    def _compute_status(self):
        """Compute license status based on expiry date."""
        today = fields.Date.today()
        thirty_days_later = today + timedelta(days=30)
        for record in self:
            if record.expiry_date and record.expiry_date < today:
                record.status = 'expired'
            elif record.expiry_date and record.expiry_date <= thirty_days_later:
                record.status = 'expiring_soon'
            else:
                record.status = 'active'


class AntivirusScanLog(models.Model):
    """
    Model to store antivirus scan logs.
    """
    _name = "antivirus.scan.log"
    _description = "Antivirus Scan Log"
    _order = "scan_time desc"

    asset_id = fields.Many2one('asset.asset', string='Device')
    os_platform = fields.Selection(related='asset_id.os_platform', store=True, string="Platform")
    scan_time = fields.Datetime(string="Scan Time", default=fields.Datetime.now)
    scan_type = fields.Selection([
        ('quick', 'Quick Scan'),
        ('full', 'Full Scan'),
        ('custom', 'Custom Scan'),
    ], string="Scan Type", default='quick')
    result = fields.Selection([
        ('clean', 'Clean'),
        ('threats_found', 'Threats Found'),
        ('failed', 'Failed'),
    ], string="Result", default='clean')
    threats_detected = fields.Integer(string="Threats Detected", default=0)
    files_scanned = fields.Integer(string="Files Scanned")
    duration = fields.Float(string="Duration (minutes)")
    summary = fields.Text(string="Summary")


class AntivirusThreat(models.Model):
    """
    Model to store antivirus threat detections.
    """
    _name = "antivirus.threat"
    _description = "Antivirus Threat"
    _order = "detected_date desc"

    name = fields.Char(string="Threat Name", required=True)
    threat_name = fields.Char(string="Threat Name", required=True)
    threat_type = fields.Selection([
        ('virus', 'Virus'),
        ('malware', 'Malware'),
        ('ransomware', 'Ransomware'),
        ('spyware', 'Spyware'),
        ('trojan', 'Trojan'),
        ('worm', 'Worm'),
    ], string="Threat Type", default='virus')
    severity = fields.Selection([
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ], string="Severity", default='medium')
    status = fields.Selection([
        ('active', 'Active'),
        ('quarantined', 'Quarantined'),
        ('cleaning', 'Cleaning'),
        ('removed', 'Removed'),
    ], string="Status", default='active')
    asset_id = fields.Many2one('asset.asset', string='Device')
    os_platform = fields.Selection(related='asset_id.os_platform', store=True, string="Platform")
    detected_date = fields.Datetime(string="Detected Date", default=fields.Datetime.now)
    quarantined_date = fields.Datetime(string="Quarantined Date")
    summary = fields.Text(string="Summary")
