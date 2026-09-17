from odoo import models, fields, api, _
from odoo.exceptions import UserError


class VendorDocumentRequest(models.Model):
    _name = 'vendor.document.request'
    _description = 'Vendor Document Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(string="Request Reference", readonly=True, copy=False, default="New")

    vendor_id = fields.Many2one(
        'res.partner',
        string="Vendor",
        domain="[('is_vendor','=',True)]",
        required=True,
        tracking=True
    )

    doc_type_id = fields.Many2one(
        'vendor.document.type',
        string="Document Type",
        required=True,
        tracking=True
    )

    description = fields.Text(string="Request Reason / Notes", required=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('rejected', 'Rejected'),
    ], default='draft', tracking=True)

    requested_by_portal = fields.Boolean(default=False, tracking=True)
    assigned_user_id = fields.Many2one('res.users', string="Assigned To", tracking=True)

    # ================= WORKFLOW =================

    def action_submit(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only Draft requests can be submitted."))
            rec.state = 'submitted'

    def action_start(self):
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(_("Only Submitted requests can be started."))
            rec.state = 'in_progress'

    def action_complete(self):
        for rec in self:
            if rec.state != 'in_progress':
                raise UserError(_("Only In Progress requests can be completed."))
            rec.state = 'completed'

    def action_reject(self):
        for rec in self:
            if rec.state not in ('submitted', 'in_progress'):
                raise UserError(_("Only Submitted or In Progress requests can be rejected."))
            rec.state = 'rejected'

    # ================= SEQUENCE =================

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        seq = self.env['ir.sequence'].next_by_code('vendor.document.request') or 'REQ'
        for rec in records:
            if rec.name == 'New':
                rec.name = seq
        return records
