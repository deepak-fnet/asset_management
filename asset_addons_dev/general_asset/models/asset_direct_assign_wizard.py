# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AssetDirectAssignWizard(models.TransientModel):
    """Assign button on a Draft general asset: hand it to an employee, or
    place it as a common asset (meeting-room AC, a department's fan)."""
    _name = 'asset.direct.assign.wizard'
    _description = 'Assign Asset'

    asset_id = fields.Many2one('asset.asset', required=True)
    assign_type = fields.Selection([('employee', 'Employee'), ('common', 'Common')],
                                   string="Assign As", default='employee', required=True)
    location_id = fields.Many2one('asset.location', string="Location")
    employee_id = fields.Many2one('hr.employee', string="Employee")
    department_id = fields.Many2one('hr.department', string="Department")

    @api.onchange('assign_type')
    def _onchange_assign_type(self):
        if self.assign_type == 'common':
            self.employee_id = False

    @api.onchange('employee_id')
    def _onchange_employee_id(self):
        if self.employee_id and self.assign_type == 'employee':
            self.department_id = self.employee_id.department_id

    def action_assign(self):
        self.ensure_one()
        if self.asset_id.state != 'draft':
            raise UserError(_("Only a Draft asset can be assigned from here."))
        self.asset_id._assign_to_target(self.assign_type, self.employee_id,
                                        self.department_id, self.location_id)
        self.asset_id.message_post(body=_(
            "Assigned (%(type)s) by %(user)s: %(target)s.",
            type=dict(self._fields['assign_type'].selection)[self.assign_type],
            user=self.env.user.name,
            target=", ".join(n for n in [self.employee_id.name, self.department_id.name,
                                          self.location_id.complete_name] if n)))
        return {'type': 'ir.actions.act_window_close'}


class AssetAsset(models.Model):
    _inherit = 'asset.asset'

    def action_open_direct_assign(self):
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_("Only a Draft asset can be assigned from here."))
        return {
            'type': 'ir.actions.act_window',
            'name': _("Assign Asset"),
            'res_model': 'asset.direct.assign.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_asset_id': self.id},
        }
