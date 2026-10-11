from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AssetReplaceLaptopWizard(models.TransientModel):
    """
    Wizard triggered from the Assign Process form.
    Lets IT pick an available general asset as a temporary replacement
    while the original is under repair.
    """
    _name = 'asset.replace.laptop.wizard'
    _description = 'Replace Asset Wizard (Assign Process)'

    assign_process_id = fields.Many2one(
        'asset.assign.process', string='Assign Process',
        required=True, readonly=True,
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Employee', readonly=True,
    )
    asset_id = fields.Many2one(related='assign_process_id.asset_id', string='Asset Under Repair')
    category_id = fields.Many2one(related='assign_process_id.asset_id.category_id', string='Category')
    department_id = fields.Many2one(related='assign_process_id.department_id')
    location_id = fields.Many2one(related='assign_process_id.location_id')
    replacement_asset_id = fields.Many2one(
        'asset.asset', string='Select Replacement Asset',
        required=True,
        # Same category as the asset under repair, ready to hand out.
        domain="[('category_id', '=', category_id), ('state', '=', 'submit'), "
               "('in_repair', '=', False), ('id', '!=', asset_id)]",
    )
    assignment_date = fields.Date(
        string='Temporary Assignment Date',
        default=fields.Date.context_today,
        required=True,
    )
    notes = fields.Text(string='Notes')

    def action_replace(self):
        self.ensure_one()
        new = self.replacement_asset_id
        if new.state != 'submit' or new.in_repair or new.category_id != self.category_id:
            raise UserError(_(
                "Pick a Submitted %s asset that is not under repair.", self.category_id.name or ''))

        # The standby takes over the asset's assignment: an employee, or for
        # a common asset just its department / location.
        process = self.assign_process_id
        new.write({
            'assigned_employee_id': process.employee_id.id,
            'department_id': process.department_id.id,
            'location_id': process.location_id.id,
            'assignment_date': self.assignment_date,
            'state': 'assigned',
        })

        # Update assign process record
        self.assign_process_id.write({
            'replacement_asset_id': self.replacement_asset_id.id,
            'state': 'replaced',
        })

        self.assign_process_id.message_post(
            body=_(
                "Replacement asset <b>%s</b> temporarily assigned to <b>%s</b>.<br/>"
                "Original asset <b>%s</b> remains under repair. "
                "It will be auto-returned once repaired."
            ) % (
                new.asset_name,
                ", ".join(n for n in [process.employee_id.name, process.department_id.name,
                                      process.location_id.complete_name] if n) or _("its location"),
                process.asset_id.asset_name,
            )
        )

        return {'type': 'ir.actions.act_window_close'}
