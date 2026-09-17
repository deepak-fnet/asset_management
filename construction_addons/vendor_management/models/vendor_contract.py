from odoo import models, fields, api, _
from odoo.exceptions import UserError


class VendorContract(models.Model):
    _name = 'vendor.contract'
    _description = 'Vendor Contract'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'end_date desc'

    name = fields.Char(string="Contract Reference", required=True, tracking=True)

    vendor_id = fields.Many2one(
        'res.partner',
        string="Vendor",
        domain="[('is_vendor','=',True)]",
        required=True,
        tracking=True
    )

    start_date = fields.Date(string="Start Date", required=True, tracking=True)
    end_date = fields.Date(string="End Date", required=True, tracking=True)

    value = fields.Float(string="Contract Value", tracking=True)
    active = fields.Boolean(default=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('terminated', 'Terminated'),
    ], default='draft', string="Status", tracking=True)

    is_expired = fields.Boolean(compute='_compute_is_expired', store=True)

    @api.depends('end_date')
    def _compute_is_expired(self):
        today = fields.Date.today()
        for rec in self:
            rec.is_expired = bool(rec.end_date and rec.end_date < today)

    # ================= WORKFLOW =================

    def action_start(self):
        for rec in self:
            if rec.start_date and rec.start_date > fields.Date.today():
                raise UserError(_("Start date is in the future."))
        self.write({'state': 'active'})

    def action_terminate(self):
        self.write({'state': 'terminated', 'active': False})
