from odoo import models, fields, api, _
from odoo.exceptions import UserError


class AssetAssignLaptopWizard(models.TransientModel):
    """
    Wizard triggered from the Joining Process form after approval.
    Lets IT pick an available (draft-state) laptop and assigns it to the employee.
    """
    _name = 'asset.assign.laptop.wizard'
    _description = 'Assign Laptop Wizard (Joining Process)'

    joining_id = fields.Many2one(
        'asset.joining.process', string='Joining Process',
        required=True, readonly=True,
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Employee',
        required=True, readonly=True,
    )
    asset_id = fields.Many2one(
        'asset.asset', string='Select Available Laptop',
        required=True,
        domain="[('state', '=', 'draft'), ('category_id.name', 'ilike', 'laptop')]",
    )
    assignment_date = fields.Date(
        string='Assignment Date',
        default=fields.Date.context_today,
        required=True,
    )
    notes = fields.Text(string='Notes')

    def action_assign(self):
        self.ensure_one()
        if self.asset_id.state != 'draft':
            raise UserError(_("The selected laptop is no longer available. Please choose another."))

        # Assign the laptop
        self.asset_id.write({
            'assigned_employee_id': self.employee_id.id,
            'assignment_date': self.assignment_date,
            'state': 'assigned',
        })

        # Mark joining process as done
        # NOTE: this wizard is no longer wired to any button - the Joining
        # Process form now assigns multiple assets directly via
        # assigned_asset_ids / action_assign_assets(), since a joining
        # process can request more than one category (laptop + mouse, etc.)
        # and this wizard only ever handled a single asset_id. Left in place
        # rather than deleted, but updated so it does not reference the
        # removed assigned_asset_id field if anything still calls it.
        self.joining_id.write({
            'state': 'done',
            'assigned_asset_ids': [(4, self.asset_id.id)],
        })

        # Log in chatter
        self.joining_id.message_post(
            body=_("Laptop <b>%s</b> assigned to <b>%s</b> on %s.")
            % (self.asset_id.asset_name, self.employee_id.name, self.assignment_date)
        )

        return {'type': 'ir.actions.act_window_close'}