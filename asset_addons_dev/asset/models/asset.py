from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import base64
import qrcode
from io import BytesIO
from datetime import date
from PIL import ImageDraw, ImageFont
import logging

_logger = logging.getLogger(__name__)
class AssetAddition(models.Model):
    _name = "asset.addition"
    _order = "id desc"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = 'asset_name'
    _description = "Asset"

    name = fields.Char('name', copy=False,)
    tag_number = fields.Char('Tag ID', copy=False,)
    asset_name = fields.Char("Asset")
    ip_address = fields.Char("Ip Address")
    serial_number = fields.Char("Serial Number")
    value = fields.Float("Value")
    acquisition_date = fields.Date(string="Acquisition Date")
    plant_id = fields.Many2one('plant.master', string="Plant")
    department_id = fields.Many2one('hr.department', string="Department")
    physical_location_id = fields.Many2one('physical.location', string="Physical Location")
    serial_number_1 = fields.Char("Serial number")
    serial_number_2 = fields.Char("Serial number")
    serial_number_3 = fields.Char("Serial number")
    is_amc = fields.Boolean(string="Amc")
    amc_file = fields.Binary("AMC Upload File")
    amc_file_filename = fields.Char("AMC File Name")
    licence_file = fields.Binary("Licence Upload File")
    licence_file_filename = fields.Char("Licence File Name")
    warranty_file = fields.Binary("Warranty Upload File")
    warranty_file_filename = fields.Char("Warranty File Name")
    is_warranty = fields.Boolean("Warranty")
    is_licence = fields.Boolean("Licence")
    amc_start_date = fields.Date("AMC Start date")
    amc_end_date = fields.Date("AMC End date")
    amc_vendor_name_id = fields.Many2one('res.partner', "AMC Vendor name")
    amc_nature = fields.Char("AMC Nature")
    licence_start_date = fields.Date("Licence Start date")
    licence_end_date = fields.Date("Licence End date")
    licence_vendor_name_id = fields.Many2one('res.partner', "Licence Vendor name")
    licence_nature = fields.Char("Licence Nature")
    warranty_start_date = fields.Date("Warranty Start date")
    warranty_end_date = fields.Date("Warranty End date")
    warranty_vendor_name_id = fields.Many2one('res.partner', "Warranty Vendor name")
    warranty_nature = fields.Char("Warranty Nature")
    nature_of_licence = fields.Char("Nature Of Licence")
    nature_of_warranty = fields.Char("Nature Of Warranty")
    state = fields.Selection(
        [('draft', 'Draft'),
         ('submit', 'Submit'),
         ('dept_approved', 'Department Approved'),
         ('incharge_approved', 'FI Approved'),
         ('cfo_approved', 'CFO Approved'),
         ('done', 'Done'),
         ('removed', 'Removed'),
         ('cancel', 'Cancel')], default='draft', string="Status", copy=False, tracking=True)
    qr_code = fields.Binary("QR Code", copy=False, )
    asset_image = fields.Binary("Asset Image", copy=False, )
    asset_image_name = fields.Char("Asset Image", copy=False, )
    category_id = fields.Many2one('asset.category', string="Category", index=True)
    sub_category_id = fields.Many2one(
        'asset.sub.category', string="Sub Category", index=True,
        domain="[('category_id', '=', category_id)]")
    # Legacy free-text classification, superseded by category_id. Kept so existing
    # data is not lost; the 19.0.1.1.0 migration converts it into asset.category.
    machine_type = fields.Char('Machine Type (legacy)')

    # --- employee assignment (see asset.assignment) ---
    current_assignment_id = fields.Many2one(
        'asset.assignment', "Current Assignment",
        compute='_compute_current_assignment', store=True)
    employee_id = fields.Many2one(
        'hr.employee', "Assigned To",
        related='current_assignment_id.employee_id', store=True, readonly=True)
    assigned_since = fields.Date(
        "Assigned Since", related='current_assignment_id.assign_date',
        store=True, readonly=True)
    assignment_ids = fields.One2many('asset.assignment', 'asset_id', "Assignment History")
    assignment_count = fields.Integer("Assignments", compute='_compute_assignment_count')

    # --- removal / scrap ---
    removal_id = fields.Many2one('asset.removal', "Removal", readonly=True, copy=False)
    removal_date = fields.Date("Removed On", readonly=True, copy=False)

    history_ids = fields.One2many('history.asset', 'asset_id', "History")
    make = fields.Char("Make")
    model = fields.Char("Model")
    controller = fields.Char('Controller')
    controller_model = fields.Char('Controller Model')
    network = fields.Selection([('connected', 'Connected'), ('not connected', 'Not Connected')],
                               default='not connected', string="Network")
    remote_possibility = fields.Selection([('yes', 'Yes'), ('no', 'No')], default='no', string="Remote Possibilities")
    process = fields.Selection([('automated', 'Automated'), ('manual', 'Manual'), ('semi automated', 'Semi-Automated')],
                               default='semi automated', string="Process")
    robo_enabled = fields.Selection([('enabled', 'Enabled'), ('not enabled', 'Not Enabled')], string="Robo")
    machines_criticality = fields.Selection([('yes', 'Yes'), ('no', 'No')], string="Machines Criticality")
    laptop_age = fields.Char(
        string="Asset Age",
        compute="_compute_laptop_age",
        store=True
    )

    laptop_age_total_months = fields.Integer(
        string="Asset Age (Months)",
        compute="_compute_laptop_age",
        store=True
    )

    @api.depends('assignment_ids.state')
    def _compute_current_assignment(self):
        for rec in self:
            rec.current_assignment_id = rec.assignment_ids.filtered(
                lambda a: a.state == 'assigned')[:1]

    def _compute_assignment_count(self):
        grouped = self.env['asset.assignment']._read_group(
            [('asset_id', 'in', self.ids)], ['asset_id'], ['__count'])
        counts = {asset.id: count for asset, count in grouped}
        for rec in self:
            rec.assignment_count = counts.get(rec.id, 0)

    def action_view_assignments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Assignments of %s", self.display_name),
            'res_model': 'asset.assignment',
            'view_mode': 'list,form',
            'domain': [('asset_id', '=', self.id)],
            'context': {'default_asset_id': self.id},
        }

    @api.depends('acquisition_date')
    def _compute_laptop_age(self):
        for record in self:
            if record.acquisition_date:
                today = date.today()
                delta_days = (today - record.acquisition_date).days

                years = delta_days // 365
                months = (delta_days % 365) // 30
                total_months = years * 12 + months

                record.laptop_age = f"{years} Years {months} Months"
                record.laptop_age_total_months = total_months

            else:
                record.laptop_age = "0 Years 0 Months"
                record.laptop_age_total_months = 0

    # @api.depends('acquisition_date')
    # def _compute_laptop_age(self):
    #     for record in self:
    #         if record.acquisition_date:
    #             today = date.today()
    #             delta_days = (today - record.acquisition_date).days
    #
    #             years = delta_days // 365
    #             months = (delta_days % 365) // 30
    #
    #             record.laptop_age = f"{years} Years {months} Months"
    #         else:
    #             record.laptop_age = "0 Years 0 Months"

    def action_download_qr(self):
        """Return an action to download QR code"""
        self.ensure_one()
        if not self.qr_code:
            return False

        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{self._name}/{self.id}/qr_code/QR.png?download=true",
            "target": "self",
        }

    def _generate_qr_code(self):
        qr_fields = [
            'name', 'asset_name', 'value', 'acquisition_date', 'plant_id', 'model', 'make', 'department_id',
            'physical_location_id', 'serial_number', 'ip_address', 'machine_type',
            'controller', 'controller_model', 'network', 'remote_possibility',
            'process', 'robo_enabled', 'machines_criticality',
            'serial_number_1', 'serial_number_2', 'serial_number_3', 'amc_vendor_name_id', 'amc_start_date',
            'amc_end_date', 'amc_nature', 'licence_vendor_name_id', 'licence_start_date', 'licence_end_date',
            'licence_nature', 'warranty_vendor_name_id', 'warranty_start_date', 'warranty_end_date',
            'warranty_nature',
        ]

        for rec in self:
            # Build text
            details = []
            for field_name in qr_fields:
                field = rec._fields.get(field_name)
                if not field:
                    continue

                value = getattr(rec, field_name, False)
                if isinstance(value, models.BaseModel):
                    value = value.display_name if value and value.id else ""
                elif isinstance(value, models.Model):
                    value = ", ".join(value.mapped("display_name")) if value else ""

                details.append(f"{field.string}: {value or ''}")

            qr_text = "\n".join(details)

            # -----------------------------
            # Generate QR (high error correction)
            # -----------------------------
            qr = qrcode.QRCode(
                version=5,
                error_correction=qrcode.constants.ERROR_CORRECT_H,
                box_size=12,
                border=3,
            )
            qr.add_data(qr_text)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

            # -----------------------------
            # Add CENTER TEXT inside the QR
            # -----------------------------
            center_text = rec.name or "No Name"
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

            try:
                font = ImageFont.truetype(font_path, 100)
            except Exception as e:
                _logger.warning("QR FONT ERROR: %s", e)
                font = ImageFont.load_default()

            # text size
            draw = ImageDraw.Draw(qr_img)
            left, top, right, bottom = draw.textbbox((0, 0), center_text, font=font)
            text_w, text_h = right - left, bottom - top

            # semi-transparent white box behind text
            box_w = text_w + 100
            box_h = text_h + 50

            box_x = (qr_img.width - box_w) // 2
            box_y = (qr_img.height - box_h) // 2

            # White box
            draw.rectangle(
                [(box_x, box_y), (box_x + box_w, box_y + box_h)],
                fill="white"
            )

            # Draw text
            text_x = (qr_img.width - text_w) // 2
            text_y = (qr_img.height - text_h) // 2
            draw.text((text_x, text_y), center_text, font=font, fill="black")

            # Save result
            buffer = BytesIO()
            qr_img.save(buffer, format="PNG")
            rec.qr_code = base64.b64encode(buffer.getvalue())

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name'):
                vals['name'] = self.env['ir.sequence'].next_by_code('asset.addition') or _("New")
        records = super().create(vals_list)
        self.env["unified.asset.kanban"].create_unified_records()
        return records

    def write(self, vals):
        rec = super().write(vals)
        self.env["unified.asset.kanban"].create_unified_records()
        return rec

    def action_submit(self):
        self._generate_qr_code()  # generate QR when submitting
        self.state = "submit"

    def action_department_approve(self):
        self.state = "dept_approved"

    def action_incharge_approve(self):
        self.state = "incharge_approved"

    def action_cfo_approve(self):
        self.state = "cfo_approved"

    def action_done(self):
        for rec in self:
            rec.state = "done"
            # self.env["physical.verification"].create({
            #     "asset_id": rec.id,
            #     "schedule_date": fields.Date.today(),
            #     "plant_id": rec.plant_id.id if rec.plant_id else "",
            #     "physical_location_id": rec.physical_location_id.id if rec.physical_location_id else "",
            #     "department_id": rec.department_id.id if rec.department_id else "",
            #     "qr_code": rec.qr_code,
            # })


class AssetInternalTransfer(models.Model):
    _name = "asset.internal.transfer"
    _description = "Asset Internal Transfer"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "asset_id"
    _order = "id desc"

    name = fields.Char('name')
    asset_id = fields.Many2one("asset.addition", string='Asset')
    transfer_date = fields.Date("Transfer Date")
    department_id = fields.Many2one('hr.department', string="Department")
    state = fields.Selection(
        [('draft', 'Draft'),
         ('submit', 'Submit'),
         ('dept_approved', 'Department Approved'),
         ('incharge_approved', 'FI Approved'),
         ('cfo_approved', 'CFO Approved'),
         ('done', 'Done'),
         ('cancel', 'Cancel')], default='draft', string="Status", copy=False, tracking=True)
    location_from_id = fields.Many2one('physical.location', 'From')
    location_to_id = fields.Many2one('physical.location', 'To')

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env["unified.asset.kanban"].create_unified_records()
        return records

    def write(self, vals):
        rec = super().write(vals)
        self.env["unified.asset.kanban"].create_unified_records()
        return rec

    def action_submit(self):
        self.state = "submit"

    def action_department_approve(self):
        self.state = "dept_approved"

    def action_incharge_approve(self):
        self.state = "incharge_approved"

    def action_cfo_approve(self):
        self.state = "cfo_approved"

    def action_done(self):
        self.state = "done"


class AssetRemoval(models.Model):
    """Take an asset out of service (scrap / sale / donation / lost).

    Goes through the same approval chain as the other asset documents. The
    scrap itself is guarded: an asset that an employee is still holding cannot
    be scrapped until that employee has been given a replacement asset.
    """

    _name = "asset.removal"
    _description = "Asset Removal"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "name"
    _order = "id desc"

    name = fields.Char("Reference", default=lambda self: _("New"), copy=False, readonly=True)
    asset_id = fields.Many2one(
        'asset.addition', string='Asset', required=True, tracking=True,
        domain="[('state', '!=', 'removed')]")
    department_id = fields.Many2one(
        'hr.department', string="Department", compute='_compute_asset_details',
        store=True, readonly=False)
    removal_type = fields.Selection(
        [('scrap', 'Scrap'),
         ('sale', 'Sale'),
         ('donation', 'Donation'),
         ('lost', 'Lost / Stolen'),
         ('other', 'Other')],
        default='scrap', string="Removal Type", required=True, tracking=True)
    request_date = fields.Date("Request Date", default=fields.Date.context_today)
    removal_date = fields.Date("Removal Date", readonly=True, copy=False)
    location_from_id = fields.Many2one('physical.location', 'From')
    location_to_id = fields.Many2one('physical.location', 'To')
    reason = fields.Text("Reason")

    # Employee holding the asset when the removal was raised. Snapshotted so it
    # survives the auto-return performed on scrap.
    employee_id = fields.Many2one(
        'hr.employee', "Currently Held By", copy=False,
        help="Employee holding the asset when the removal was raised. Snapshotted "
             "so it survives the automatic hand-back performed on removal.")
    replacement_asset_id = fields.Many2one(
        'asset.addition', "Replacement Asset", copy=False, tracking=True,
        domain="[('state', '!=', 'removed'), ('id', '!=', asset_id)]",
        help="Asset given to the employee in place of the one being removed. "
             "Required before an asset held by an employee can be scrapped.")
    replacement_ready = fields.Boolean(
        "Replacement Ready", compute='_compute_replacement_ready',
        help="True when the employee holding this asset already holds another one.")

    state = fields.Selection(
        [('draft', 'Draft'),
         ('submit', 'Submit'),
         ('dept_approved', 'Department Approved'),
         ('approved', 'Approved'),
         ('done', 'Removed'),
         ('cancel', 'Cancel')], default='draft', string="Status", copy=False, tracking=True)

    @api.depends('asset_id')
    def _compute_asset_details(self):
        for rec in self:
            rec.department_id = rec.asset_id.department_id

    @api.depends('employee_id', 'asset_id', 'replacement_asset_id')
    def _compute_replacement_ready(self):
        for rec in self:
            rec.replacement_ready = bool(rec._get_replacement_assignment())

    def _get_replacement_assignment(self):
        """Active assignment of another asset to the same employee, if any."""
        self.ensure_one()
        if not self.employee_id:
            return self.env['asset.assignment']
        domain = [
            ('employee_id', '=', self.employee_id.id),
            ('state', '=', 'assigned'),
            ('asset_id', '!=', self.asset_id.id),
        ]
        if self.replacement_asset_id:
            domain.append(('asset_id', '=', self.replacement_asset_id.id))
        return self.env['asset.assignment'].search(domain, limit=1)

    @api.onchange('asset_id')
    def _onchange_asset_id(self):
        if self.asset_id:
            self.location_from_id = self.asset_id.physical_location_id
            self.employee_id = self.asset_id.employee_id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _("New")) == _("New"):
                vals['name'] = self.env['ir.sequence'].next_by_code('asset.removal') or _("New")
            # Snapshot the holder for records created outside the form (import, RPC).
            if not vals.get('employee_id') and vals.get('asset_id'):
                asset = self.env['asset.addition'].browse(vals['asset_id'])
                vals['employee_id'] = asset.employee_id.id or False
        records = super().create(vals_list)
        self.env["unified.asset.kanban"].create_unified_records()
        return records

    def write(self, vals):
        rec = super().write(vals)
        self.env["unified.asset.kanban"].create_unified_records()
        return rec

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------

    def action_submit(self):
        for rec in self:
            if rec.asset_id.state == 'removed':
                raise UserError(_(
                    "Asset %s has already been removed.", rec.asset_id.display_name))
            if not rec.reason:
                raise UserError(_("Please give a reason before submitting the removal."))
            open_removal = self.search([
                ('id', '!=', rec.id),
                ('asset_id', '=', rec.asset_id.id),
                ('state', 'not in', ('done', 'cancel')),
            ], limit=1)
            if open_removal:
                raise UserError(_(
                    "Removal %(ref)s is already in progress for asset %(asset)s.",
                    ref=open_removal.name, asset=rec.asset_id.display_name))
            rec.write({'state': 'submit', 'employee_id': rec.asset_id.employee_id.id or False})

    def action_department_approve(self):
        self.state = 'dept_approved'

    def action_approve(self):
        self.state = 'approved'

    def action_done(self):
        """Remove / scrap the asset.

        Blocked while the asset is still in an employee's hands and that
        employee has not been given a replacement.
        """
        for rec in self:
            if rec.asset_id.state == 'removed':
                raise UserError(_(
                    "Asset %s has already been removed.", rec.asset_id.display_name))

            assignment = rec.asset_id.current_assignment_id
            if assignment and rec.employee_id != assignment.employee_id:
                rec.employee_id = assignment.employee_id
            if assignment:
                replacement = rec._get_replacement_assignment()
                if not replacement:
                    raise UserError(_(
                        "Asset %(asset)s is assigned to %(employee)s.\n\n"
                        "Assign a replacement asset to %(employee)s first, then "
                        "remove this one. Set it in the Replacement Asset field "
                        "so the handover is recorded.",
                        asset=rec.asset_id.display_name,
                        employee=rec.employee_id.display_name))
                if not rec.replacement_asset_id:
                    rec.replacement_asset_id = replacement.asset_id
                # Hand the old asset back before scrapping it.
                assignment.action_return()

            removal_date = fields.Date.context_today(rec)
            rec.asset_id.write({
                'state': 'removed',
                'removal_id': rec.id,
                'removal_date': removal_date,
                'physical_location_id': rec.location_to_id.id or rec.asset_id.physical_location_id.id,
            })
            rec.write({'state': 'done', 'removal_date': removal_date})
            rec.asset_id.message_post(body=_(
                "Removed (%(type)s) through %(ref)s.",
                type=dict(rec._fields['removal_type'].selection).get(rec.removal_type),
                ref=rec.name))

    def action_cancel(self):
        self.state = 'cancel'

    def action_draft(self):
        self.state = 'draft'

    def action_view_asset(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'asset.addition',
            'res_id': self.asset_id.id,
            'view_mode': 'form',
        }


class PhysicalVerification(models.Model):
    _name = "physical.verification"
    _description = "Physical Verification"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "name"
    _order = "id desc"

    # year = fields.Selection([('2024', '2024'), ('2025', '2025'), ('2026', '2026')], string="Year", default=lambda r: str(fields.Date.today().year))
    name = fields.Char("Reference", default="New", readonly=True)
    # asset_id = fields.Many2one("asset.addition", string='Asset')
    schedule_date = fields.Date("Schedule")
    plant_id = fields.Many2one('plant.master', string="Plant")
    department_id = fields.Many2one('hr.department', string="Department")
    physical_location_id = fields.Many2one('physical.location', string="Physical Location")
    qr_code = fields.Binary("QR Code")
    
    # RFID CSV Upload
    csv_file = fields.Binary(string="RFID CSV File", attachment=True)
    csv_filename = fields.Char(string="CSV Filename")
    rfid_comparison_id = fields.Many2one('asset.rfid.comparison', string="RFID Comparison", readonly=True)
    
    state = fields.Selection(
        [('draft', 'Draft'),
         ('submit', 'Submit'),
         ('approved', 'Approved'),
         ('done', 'Done'),
         ('cancel', 'Cancel')], default='draft', string="Status", copy=False, tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                 vals['name'] = self.env['ir.sequence'].next_by_code('physical.verification') or 'New'
        rec = super().create(vals_list)
        self.env["unified.asset.kanban"].create_unified_records()
        return rec

    def write(self, vals):
        rec = super().write(vals)
        self.env["unified.asset.kanban"].create_unified_records()
        return rec

    def action_download_qr(self):
        """Return an action to download QR code"""
        self.ensure_one()
        if not self.qr_code:
            return False

        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{self._name}/{self.id}/qr_code/QR.png?download=true",
            "target": "self",
        }

    def action_view_rfid_comparison(self):
        """Open the linked RFID Comparison record"""
        self.ensure_one()
        if self.rfid_comparison_id:
            return {
                'type': 'ir.actions.act_window',
                'name': 'RFID Comparison',
                'res_model': 'asset.rfid.comparison',
                'view_mode': 'form',
                'res_id': self.rfid_comparison_id.id,
                'target': 'current',
            }
        return False

    def action_submit(self):
        # self._generate_qr_code()  # generate QR when submitting
        self.state = "submit"

    def action_department_approve(self):
        self.state = "approved"

    def action_done(self):
        for rec in self:
            if not rec.csv_file:
                raise ValidationError(
                    "Please upload an RFID CSV file before marking the verification as Done."
                )
            if rec.csv_file and not rec.rfid_comparison_id:
                comparison = self.env['asset.rfid.comparison'].create({
                    'csv_file': rec.csv_file,
                    'csv_filename': rec.csv_filename or 'rfid_scan.csv',
                    'verification_id': rec.id,
                    'notes': f"Auto-created from Physical Verification: {rec.name}",
                })
                rec.rfid_comparison_id = comparison.id
                
                # Auto-run the comparison
                comparison.action_compare()
                rec.state = "done"


class HistoryAsset(models.Model):
    _name = "history.asset"
    _description = "Asset AMC/Licence/Warranty History"

    vendor_id = fields.Many2one('res.partner', "Vendor")
    asset_type = fields.Selection([('amc', 'AMC'),
                                   ('licence', 'Licence'),
                                   ('warranty', 'Warranty')], default='amc', string='Asset Type')
    start_date = fields.Date("Strat Date")
    end_date = fields.Date("End Date")
    asset_id = fields.Many2one("asset.addition", string='Asset')
