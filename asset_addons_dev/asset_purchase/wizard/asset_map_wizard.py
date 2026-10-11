# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.asset_assign_request import ASSIGN_TYPES


class AssetMapWizard(models.TransientModel):
    _name = 'asset.map.wizard'
    _description = 'Map Assets to an Assign Request Line'

    request_line_id = fields.Many2one('asset.joining.request.line', required=True)
    joining_id = fields.Many2one(related='request_line_id.joining_id')
    product_id = fields.Many2one(related='request_line_id.product_id')
    category_id = fields.Many2one(related='request_line_id.category_id')
    pending_qty = fields.Integer(related='request_line_id.pending_qty', string="Still to assign")
    assign_type = fields.Selection(ASSIGN_TYPES, string="Assign As", default='employee', required=True)
    line_ids = fields.One2many('asset.map.wizard.line', 'wizard_id', string="Mappings")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        line = self.env['asset.joining.request.line'].browse(res.get('request_line_id'))
        if line:
            assign_type = res.get('assign_type', 'employee')
            # One empty row per unit still pending; the asset is picked by hand.
            res['line_ids'] = [(0, 0, self._default_line_vals(line, assign_type))
                               for _i in range(max(line.pending_qty, 1))]
        return res

    @api.model
    def _default_line_vals(self, line, assign_type):
        # Location / employee / department are always filled in by hand.
        return {'assign_type': assign_type}

    @api.onchange('assign_type')
    def _onchange_assign_type(self):
        # The radio sets the type of every row and clears what was entered
        # for the previous type.
        for line in self.line_ids:
            line.update({'assign_type': self.assign_type, 'location_id': False,
                         'employee_id': False, 'department_id': False})

    def action_confirm(self):
        self.ensure_one()
        req = self.request_line_id
        lines = self.line_ids.filtered('asset_id')
        if not lines:
            raise UserError(_("No asset is available to assign."))
        assets = lines.asset_id
        if len(assets) != len(lines):
            raise UserError(_("The same asset is picked on more than one row."))
        if len(assets) > req.pending_qty:
            raise UserError(_("Only %(n)s more unit(s) can be assigned on this line; %(m)s picked.",
                              n=req.pending_qty, m=len(assets)))
        wrong = assets.filtered(lambda a: not a._is_mappable_for(req))
        if wrong:
            raise UserError(_("Not available for this line (must be %(cat)s / %(prod)s, Submitted, "
                              "not under repair): %(assets)s",
                              cat=req.category_id.name, prod=req.product_id.display_name,
                              assets=", ".join(wrong.mapped('display_name'))))
        for line in lines:
            if line.assign_type == 'employee' and line.department_id != line.employee_id.department_id:
                raise UserError(_("%(emp)s belongs to %(dept)s - the department must match the employee's.",
                                  emp=line.employee_id.name,
                                  dept=line.employee_id.department_id.name or _("no department")))
            if line.assign_type == 'common':
                line.employee_id = False
            line.asset_id._assign_to_target(
                line.assign_type, line.employee_id, line.department_id, line.location_id)
            self.env['asset.joining.allocation'].create({
                'request_line_id': req.id,
                'assign_type': line.assign_type,
                'asset_ids': [(6, 0, line.asset_id.ids)],
                'quantity': 1,
                'location_id': line.location_id.id,
                'employee_id': line.employee_id.id,
                'department_id': line.department_id.id,
            })
        req.joining_id._update_assign_state()
        req.joining_id.message_post(body=_(
            "%(desc)s: assigned %(assets)s (%(done)s of %(qty)s).",
            desc=req.description, assets=", ".join(assets.mapped('display_name')),
            done=req.assigned_qty, qty=req.quantity))
        return {'type': 'ir.actions.act_window_close'}


class AssetMapWizardLine(models.TransientModel):
    _name = 'asset.map.wizard.line'
    _description = 'Map Assets Row'

    wizard_id = fields.Many2one('asset.map.wizard', required=True, ondelete='cascade')
    assign_type = fields.Selection(ASSIGN_TYPES, string="Type", required=True, default='employee')
    category_id = fields.Many2one(related='wizard_id.category_id')
    product_id = fields.Many2one(related='wizard_id.product_id')
    asset_id = fields.Many2one('asset.asset', string="Asset")
    location_id = fields.Many2one('asset.location', string="Location")
    employee_id = fields.Many2one('hr.employee', string="Employee")
    department_id = fields.Many2one('hr.department', string="Department")

    @api.onchange('assign_type')
    def _onchange_assign_type(self):
        self.location_id = self.employee_id = self.department_id = False

    @api.onchange('employee_id')
    def _onchange_employee_id(self):
        # Employee rows take the employee's own department.
        if self.assign_type == 'employee':
            self.department_id = self.employee_id.department_id


class AssetAssetMappable(models.Model):
    _inherit = 'asset.asset'

    def _is_mappable_for(self, request_line):
        """Ready to hand out for this request line: same category and
        product, Submitted, and no repair open."""
        self.ensure_one()
        return (self.state == 'submit'
                and not self.in_repair
                and self.category_id == request_line.category_id
                and self.product_id.id == request_line.product_id.id)
