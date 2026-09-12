from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from datetime import datetime, timedelta


class AssetLocation(models.Model):
    _name = 'asset.location'
    _description = 'Asset Location'
    _parent_name = "parent_id"
    _parent_store = True
    _rec_name = 'complete_name'
    _order = 'complete_name'

    name = fields.Char('Location Name', required=True)
    complete_name = fields.Char('Full Location Name', compute='_compute_complete_name', store=True)
    parent_id = fields.Many2one('asset.location', 'Parent Location', index=True, ondelete='cascade')
    child_ids = fields.One2many('asset.location', 'parent_id', 'Child Locations')
    parent_path = fields.Char(index=True)

    @api.depends('name', 'parent_id.complete_name')
    def _compute_complete_name(self):
        for location in self:
            if location.parent_id:
                location.complete_name = f"{location.parent_id.complete_name} / {location.name}"
            else:
                location.complete_name = location.name


class AssetLocationHistory(models.Model):
    _name = 'asset.location.history'
    _description = 'Asset Location History'
    _order = 'date desc'
    _rec_name = 'asset_id'

    asset_id = fields.Many2one('asset.asset', string='Asset', required=True, ondelete='cascade')
    location_id = fields.Many2one('asset.location', string='Location', required=False)
    date = fields.Datetime(string='Date', default=fields.Datetime.now, required=True, readonly=True)
    user_id = fields.Many2one('res.users', string='Changed By', default=lambda self: self.env.user, readonly=True)

    # IP-based location fields
    city = fields.Char(string='City')
    region = fields.Char(string='Region/State')
    country = fields.Char(string='Country')
    public_ip = fields.Char(string='Public IP')
    latitude = fields.Float(string='Latitude')
    longitude = fields.Float(string='Longitude')
    isp = fields.Char(string='ISP')
    source = fields.Char(string='Source', default='IP-based')
    reported_at = fields.Datetime(string='Reported At', default=fields.Datetime.now)

    # GPS reverse geocoding fields
    full_address = fields.Char(string='Full Address',
                               help='Human-readable address resolved from GPS coordinates')
    postcode = fields.Char(string='Postcode/ZIP',
                           help='Postal code resolved from GPS coordinates')

    display_coordinates = fields.Char(string='Coordinates', compute='_compute_display_fields')
    display_isp = fields.Char(string='ISP Info', compute='_compute_display_fields')
    date_display = fields.Char(string='Date (Display)', compute='_compute_date_display')

    @api.depends('latitude', 'longitude', 'isp')
    def _compute_display_fields(self):
        for rec in self:
            if rec.latitude == 0.0 and rec.longitude == 0.0:
                rec.display_coordinates = _("Coordinates unavailable (IP-based approximation)")
            else:
                rec.display_coordinates = f"{rec.latitude}, {rec.longitude}"

            rec.display_isp = rec.isp or _("ISP not provided by IP service")

    @api.depends('date')
    def _compute_date_display(self):
        for rec in self:
            if rec.date:
                # Convert to user timezone and format in 12-hour format
                local_dt = fields.Datetime.context_timestamp(self, rec.date)
                rec.date_display = local_dt.strftime('%m/%d/%Y %I:%M:%S %p')
            else:
                rec.date_display = ''


class AssetMaintenance(models.Model):
    _name = 'asset.maintenance'
    _description = 'Asset Maintenance'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string='Request Reference', required=True, copy=False, readonly=True,
                       default=lambda self: _('New'))
    asset_id = fields.Many2one('asset.asset', string='Asset', required=True, tracking=True)
    maintenance_type = fields.Selection([
        ('preventive', 'Preventive'),
        ('corrective', 'Corrective')
    ], string='Maintenance Type', default='preventive', tracking=True)
    request_date = fields.Date(string='Request Date', default=fields.Date.context_today)
    maintenance_date = fields.Date(string='Maintenance Date', tracking=True)
    cost = fields.Float(string='Cost', tracking=True)
    notes = fields.Text(string='Notes')
    state = fields.Selection([
        ('open', 'Open'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed')
    ], string='Status', default='open', tracking=True)

    # Populated automatically when this row was generated from a
    # repair.management completion (RepairManagement._sync_maintenance_history) -
    # not meant to be set by hand.
    repair_id = fields.Many2one(
        'repair.management', string='Repair', ondelete='set null')
    repair_mode = fields.Selection([
        ('in_house', 'In-House'),
        ('vendor', 'Vendor'),
    ], string='Repair Mode')
    vendor_id = fields.Many2one('asset.vendor', string='Repair Vendor')
    bill_file = fields.Binary(string='Bill / Invoice')
    bill_filename = fields.Char()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('asset.maintenance') or _('New')
        return super(AssetMaintenance, self).create(vals_list)


class AssetVendor(models.Model):
    _name = 'asset.vendor'
    _description = 'Asset Vendor'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Vendor Name', required=True, tracking=True)
    contact = fields.Char(string='Contact Number')
    email = fields.Char(string='Email')
    sla_details = fields.Text(string='SLA Details')
    asset_ids = fields.One2many('asset.asset', 'vendor_id', string='Assets')


class AssetAssignmentHistory(models.Model):
    _name = 'asset.assignment.history'
    _description = 'Asset Assignment History'
    _order = 'date desc'

    asset_id = fields.Many2one('asset.asset', string='Asset', required=True, ondelete='cascade')
    # No longer required: a location or department transfer does not involve
    # an employee at all, and forcing one meant those movements either could
    # not be logged here or had to invent a bogus employee.
    employee_id = fields.Many2one('hr.employee', string='Employee')
    date = fields.Datetime(string='Assignment Date', default=fields.Datetime.now, required=True, readonly=True)
    user_id = fields.Many2one('res.users', string='Assigned By', default=lambda self: self.env.user, readonly=True)
    action = fields.Selection([
        ('assign', 'Assigned'),
        ('unassign', 'Unassigned'),
        ('transfer', 'Transferred'),
    ], string='Action', required=True)
    description = fields.Char(
        string='Description',
        help="What actually changed, in plain words - "
             "\"Moved from Floor 1 to Floor 2\", "
             "\"Department changed from IT to Sales\".")

    @api.constrains('action', 'employee_id')
    def _check_employee_required(self):
        """Employee is still mandatory for assign/unassign.

        Dropping required=True on the field would otherwise let an assignment
        be logged with nobody assigned, which is meaningless. Only 'transfer'
        is allowed to have no employee.
        """
        for rec in self:
            if rec.action in ('assign', 'unassign') and not rec.employee_id:
                raise ValidationError(_(
                    "An employee is required when the action is "
                    "Assigned or Unassigned."))


class AssetAuditLog(models.Model):
    _name = 'asset.audit.log'
    _description = 'Asset Audit Log'
    _order = 'date desc'
    _rec_name = 'asset_id'

    asset_id = fields.Many2one('asset.asset', string='Asset', required=True, ondelete='cascade')
    date = fields.Datetime(string='Date', default=fields.Datetime.now, required=True, readonly=True)
    user_id = fields.Many2one('res.users', string='User', default=lambda self: self.env.user, readonly=True)
    log_type = fields.Selection([
        ('assignment', 'Asset Assignment'),
        ('status', 'Status Change'),
        ('location', 'Location Change'),
        ('hardware_change', 'Hardware Change'),
        ('software_change', 'Software Change'),
        ('security', 'Security Event'),
    ], string='Log Type')
    action = fields.Selection([
        ('uninstall_request', 'Uninstall Request'),
        ('uninstall_success', 'Uninstall Success'),
        ('uninstall_failed', 'Uninstall Failed'),
        ('windows_update_lock', 'Windows Update Lock'),
        ('folder_lock', 'Folder Lock'),
        ('file_access_block', 'File Access Block'),
        # Security events (log_type='security') - reported by the agent,
        # not raised from within Odoo itself like the ones above.
        ('user_login', 'User Login'),
        ('user_logout', 'User Logout'),
        ('failed_login', 'Failed Login Attempt'),
        ('username_changed', 'Username Changed'),
        ('password_changed', 'Password Changed'),
        ('user_created', 'User Account Created'),
        ('user_deleted', 'User Account Deleted'),
        ('sudo_granted', 'Admin/Sudo Privilege Granted'),
        ('file_lock_bypassed', 'Locked File/Folder Permissions Bypassed'),
    ], string='Action')
    old_value = fields.Char(string='Old Value')
    new_value = fields.Char(string='New Value')
    description = fields.Text(string='Description')
    timestamp = fields.Datetime(string='Timestamp', default=fields.Datetime.now)