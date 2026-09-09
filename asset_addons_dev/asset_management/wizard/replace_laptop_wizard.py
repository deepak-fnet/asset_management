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
        'hr.employee', string='Employee',
        required=True, readonly=True,
    )
    replacement_asset_id = fields.Many2one(
        'asset.asset', string='Select Replacement Asset',
        required=True,
        # General assets only - this table is shared with the repair flow,
        # and a fixed/IT asset under agent monitoring has no business being
        # handed out as a stand-in here.
        domain="[('state', 'in', ['draft', 'submit']), ('is_general_asset', '=', True)]",
    )
    assignment_date = fields.Date(
        string='Temporary Assignment Date',
        default=fields.Date.context_today,
        required=True,
    )
    notes = fields.Text(string='Notes')

    def action_replace(self):
        self.ensure_one()
        if self.replacement_asset_id.state not in ('draft', 'submit'):
            raise UserError(_(
                "The selected asset is no longer available. Please choose another."
            ))

        # Assign replacement asset temporarily to the same employee
        self.replacement_asset_id.write({
            'assigned_employee_id': self.employee_id.id,
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
                "It will be auto-returned to this employee once repaired."
            ) % (
                self.replacement_asset_id.asset_name,
                self.employee_id.name,
                self.assign_process_id.asset_id.asset_name,
            )
        )

        return {'type': 'ir.actions.act_window_close'}
