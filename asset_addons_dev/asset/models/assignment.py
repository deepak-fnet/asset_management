# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# States in which an assignment is considered to actually hold the asset.
ACTIVE_ASSIGNMENT_STATES = ('assigned',)


class AssetAssignment(models.Model):
    """Assign an asset to an employee through an approval chain."""

    _name = "asset.assignment"
    _description = "Asset Assignment"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"
    _rec_name = "name"

    name = fields.Char("Reference", default=lambda self: _("New"), copy=False, readonly=True)
    asset_id = fields.Many2one(
        'asset.addition', "Asset", required=True, tracking=True,
        domain="[('state', '!=', 'removed')]")
    employee_id = fields.Many2one('hr.employee', "Employee", required=True, tracking=True)
    department_id = fields.Many2one(
        'hr.department', "Department", compute='_compute_department_id',
        store=True, readonly=False)
    plant_id = fields.Many2one('plant.master', "Plant")
    physical_location_id = fields.Many2one('physical.location', "Physical Location")
    assign_date = fields.Date("Assign Date", default=fields.Date.context_today, tracking=True)
    return_date = fields.Date("Return Date", readonly=True, copy=False, tracking=True)
    notes = fields.Text("Notes")
    state = fields.Selection(
        [('draft', 'Draft'),
         ('submit', 'Submit'),
         ('dept_approved', 'Department Approved'),
         ('approved', 'Approved'),
         ('assigned', 'Assigned'),
         ('returned', 'Returned'),
         ('cancel', 'Cancel')],
        default='draft', string="Status", copy=False, tracking=True)

    @api.depends('employee_id')
    def _compute_department_id(self):
        for rec in self:
            rec.department_id = rec.employee_id.department_id

    @api.onchange('asset_id')
    def _onchange_asset_id(self):
        if self.asset_id:
            self.plant_id = self.asset_id.plant_id
            self.physical_location_id = self.asset_id.physical_location_id

    @api.constrains('asset_id', 'state')
    def _check_single_active_assignment(self):
        """An asset can be held by only one employee at a time."""
        for rec in self:
            if rec.state not in ACTIVE_ASSIGNMENT_STATES:
                continue
            clash = self.search([
                ('id', '!=', rec.id),
                ('asset_id', '=', rec.asset_id.id),
                ('state', 'in', ACTIVE_ASSIGNMENT_STATES),
            ], limit=1)
            if clash:
                raise ValidationError(_(
                    "Asset %(asset)s is already assigned to %(employee)s (%(ref)s). "
                    "Return it before assigning it to somebody else.",
                    asset=rec.asset_id.display_name,
                    employee=clash.employee_id.display_name,
                    ref=clash.name))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _("New")) == _("New"):
                vals['name'] = self.env['ir.sequence'].next_by_code('asset.assignment') or _("New")
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------

    def action_submit(self):
        for rec in self:
            if rec.asset_id.state == 'removed':
                raise UserError(_(
                    "Asset %s has been removed and cannot be assigned.",
                    rec.asset_id.display_name))
            if rec.asset_id.current_assignment_id:
                raise UserError(_(
                    "Asset %(asset)s is currently held by %(employee)s. "
                    "Return it first.",
                    asset=rec.asset_id.display_name,
                    employee=rec.asset_id.employee_id.display_name))
            rec.state = 'submit'

    def action_department_approve(self):
        self.state = 'dept_approved'

    def action_approve(self):
        self.state = 'approved'

    def action_assign(self):
        """Hand the asset over to the employee."""
        for rec in self:
            if rec.asset_id.current_assignment_id:
                raise UserError(_(
                    "Asset %(asset)s is currently held by %(employee)s. "
                    "Return it first.",
                    asset=rec.asset_id.display_name,
                    employee=rec.asset_id.employee_id.display_name))
            rec.write({'state': 'assigned', 'assign_date': rec.assign_date or fields.Date.context_today(rec)})
            rec.asset_id.message_post(body=_(
                "Assigned to %(employee)s (%(ref)s).",
                employee=rec.employee_id.display_name, ref=rec.name))

    def action_return(self):
        for rec in self:
            rec.write({'state': 'returned', 'return_date': fields.Date.context_today(rec)})
            rec.asset_id.message_post(body=_(
                "Returned by %(employee)s (%(ref)s).",
                employee=rec.employee_id.display_name, ref=rec.name))

    def action_cancel(self):
        self.state = 'cancel'

    def action_draft(self):
        self.state = 'draft'
