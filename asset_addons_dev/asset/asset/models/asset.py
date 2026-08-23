from odoo import models, fields, api
import base64
import qrcode
from io import BytesIO


class AssetAddition(models.Model):
    _name = "asset.addition"
    _order = "id desc"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = 'asset_name'
    _description = "Asset"

    asset_name = fields.Char("Asset Name")
    ip_address = fields.Char("Ip Address")
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
         ('cancel', 'Cancel')], default='draft', string="Status", copy=False, tracking=True)
    qr_code = fields.Binary("QR Code", copy=False,)
    history_ids = fields.One2many('history.asset', 'asset_id', "History")

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
        """Generate QR code only for specific fields"""
        qr_fields = [
            'asset_name', 'value', 'acquisition_date', 'plant_id',
            'department_id', 'physical_location_id', 'serial_number_1', 'ip_address',
            'serial_number_2', 'serial_number_3','amc_vendor_name_id', 'amc_start_date', 'amc_end_date','amc_nature',
            'licence_vendor_name_id', 'licence_start_date', 'licence_end_date', 'licence_nature',
            'warranty_vendor_name_id', 'warranty_start_date', 'warranty_end_date', 'warranty_nature',
        ]

        for rec in self:
            details = []
            for field_name in qr_fields:
                field = rec._fields.get(field_name)
                if not field:
                    continue

                value = getattr(rec, field_name, False)

                # Handle Many2one
                if isinstance(value, models.BaseModel):
                    value = value.display_name if value and value.id else ""

                # Handle One2many/Many2many (skip history_ids or summarize)
                elif isinstance(value, models.Model):
                    value = ", ".join(value.mapped('display_name')) if value else ""

                details.append(f"{field.string}: {value or ''}")

            qr_text = "\n".join(details)

            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_H,
                box_size=10,
                border=4,
            )
            qr.add_data(qr_text or "No Data")
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")

            buffer = BytesIO()
            img.save(buffer, format="PNG")
            rec.qr_code = base64.b64encode(buffer.getvalue())

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
            self.env["physical.verification"].create({
                "asset_id": rec.id,
                "schedule_date": fields.Date.today(),
                "plant_id": rec.plant_id.id if rec.plant_id else "",
                "physical_location_id": rec.physical_location_id.id if rec.physical_location_id else "",
                "department_id": rec.department_id.id if rec.department_id else "",
            })


class AssetInternalTransfer(models.Model):
    _name = "asset.internal.transfer"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "asset_id"
    _order = "id desc"

    name = fields.Char('name')
    asset_id = fields.Many2one("asset.addition", string='Asset')
    transfer_date = fields.Date("Transfer Date")
    state = fields.Selection(
        [('draft', 'Draft'),
         ('submit', 'Submit'),
         ('dept_approved', 'Department Approved'),
         ('incharge_approved', 'FI Approved'),
         ('cfo_approved', 'CFO Approved'),
         ('done', 'Done'),
         ('cancel', 'Cancel')], default='draft', string="Status", copy=False, tracking=True)
    location_from_id = fields.Many2one('from.location', 'From')
    location_to_id = fields.Many2one('to.location', 'To')

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
    _name = "asset.removal"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "asset_id"
    _order = "id desc"

    name = fields.Char("name")
    asset_id = fields.Many2one("asset.addition", string='Asset')
    state = fields.Selection(
        [('draft', 'Draft'),
         ('submit', 'Submit'),
         ('cancel', 'Cancel')], default='draft', string="Status", copy=False, tracking=True)
    location_from_id = fields.Many2one('from.location', 'From')
    location_to_id = fields.Many2one('to.location', 'To')
    reason = fields.Text("reason")

    def action_submit(self):
        self.state = "submit"


class PhysicalVerification(models.Model):
    _name = "physical.verification"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "asset_id"
    _order = "id desc"

    asset_id = fields.Many2one("asset.addition", string='Asset')
    schedule_date = fields.Date("Schedule")
    plant_id = fields.Many2one('plant.master', string="Plant")
    department_id = fields.Many2one('hr.department', string="Department")
    physical_location_id = fields.Many2one('physical.location', string="Physical Location")
    qr_code = fields.Binary("QR Code")
    state = fields.Selection(
        [('draft', 'Draft'),
         ('submit', 'Submit'),
         ('dept_approved', 'Department Approved'),
         ('incharge_approved', 'FI Approved'),
         ('cfo_approved', 'CFO Approved'),
         ('done', 'Done'),
         ('cancel', 'Cancel')], default='draft', string="Status", copy=False, tracking=True)

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
        """Generate QR code only for specific fields"""
        qr_fields = [
            'asset_id', 'schedule_date', 'department_id', 'plant_id', 'physical_location_id',
        ]

        for rec in self:
            details = []
            for field_name in qr_fields:
                field = rec._fields.get(field_name)
                if not field:
                    continue

                value = getattr(rec, field_name, False)

                # Handle Many2one
                if isinstance(value, models.BaseModel):
                    value = value.display_name if value and value.id else ""

                # Handle One2many/Many2many (skip history_ids or summarize)
                elif isinstance(value, models.Model):
                    value = ", ".join(value.mapped('display_name')) if value else ""

                details.append(f"{field.string}: {value or ''}")

            qr_text = "\n".join(details)

            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_H,
                box_size=10,
                border=4,
            )
            qr.add_data(qr_text or "No Data")
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")

            buffer = BytesIO()
            img.save(buffer, format="PNG")
            rec.qr_code = base64.b64encode(buffer.getvalue())

    def action_submit(self):
        self._generate_qr_code()  # generate QR when submitting
        self.state = "submit"


class HistoryAsset(models.Model):
    _name = "history.asset"

    vendor_id = fields.Many2one('res.partner', "Vendor")
    asset_type = fields.Selection([('amc', 'AMC'),
                                   ('licence', 'Licence'),
                                   ('warranty', 'Warranty')], default='amc', string='Asset Type')
    start_date = fields.Date("Strat Date")
    end_date = fields.Date("End Date")
    asset_id = fields.Many2one("asset.addition", string='Asset')
