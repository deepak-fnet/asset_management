from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AssetReplaceLaptopWizard(models.TransientModel):
    """
    Wizard triggered from the Assign Process form.
    Lets IT pick an available laptop as a temporary replacement
    while the original is under repair.
    """
    _name = 'asset.replace.laptop.wizard'
    _description = 'Replace Laptop Wizard (Assign Process)'

    assign_process_id = fields.Many2one(
        'asset.assign.process', string='Assign Process',
        required=True, readonly=True,
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Employee',
        required=True, readonly=True,
    )
    replacement_asset_id = fields.Many2one(
        'asset.asset', string='Select Replacement Laptop',
        required=True,
        domain="[('state', '=', 'draft'), ('category_id.name', 'ilike', 'laptop')]",
    )
    assignment_date = fields.Date(
        string='Temporary Assignment Date',
        default=fields.Date.context_today,
        required=True,
    )
    notes = fields.Text(string='Notes')

    def action_replace(self):
        self.ensure_one()
        if self.replacement_asset_id.state != 'draft':
            raise UserError(_(
                "The selected laptop is no longer available. Please choose another."
            ))

        # Assign replacement laptop temporarily to the same employee
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
                "Replacement laptop <b>%s</b> temporarily assigned to <b>%s</b>.<br/>"
                "Original laptop <b>%s</b> remains under repair. "
                "It will be auto-returned to this employee once repaired."
            ) % (
                self.replacement_asset_id.asset_name,
                self.employee_id.name,
                self.assign_process_id.asset_id.asset_name,
            )
        )

        return {'type': 'ir.actions.act_window_close'}
