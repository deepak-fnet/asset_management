from odoo import models, fields, api, _


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    assigned_asset_ids = fields.One2many(
        'asset.asset',
        'assigned_employee_id',
        string='Assigned Assets'
    )

    asset_assignment_history_ids = fields.One2many(
        'asset.assignment.history',
        'employee_id',
        string='Asset Assignment History'
    )

    # ── Lifecycle process counts ──────────────────────────────────────────

    joining_process_count = fields.Integer(
        string='Joining Processes',
        compute='_compute_lifecycle_counts',
    )
    exit_process_count = fields.Integer(
        string='Exit Processes',
        compute='_compute_lifecycle_counts',
    )

    def _compute_lifecycle_counts(self):
        for emp in self:
            emp.joining_process_count = self.env['asset.joining.process'].search_count([
                ('employee_id', '=', emp.id),
            ])
            emp.exit_process_count = self.env['asset.exit.process'].search_count([
                ('employee_id', '=', emp.id),
            ])

    def action_view_joining_processes(self):
        self.ensure_one()
        return {
            'name': _('Joining Processes'),
            'type': 'ir.actions.act_window',
            'res_model': 'asset.joining.process',
            'view_mode': 'list,form',
            'domain': [('employee_id', '=', self.id)],
            'context': {'default_employee_id': self.id},
        }

    def action_view_exit_processes(self):
        self.ensure_one()
        return {
            'name': _('Exit Processes'),
            'type': 'ir.actions.act_window',
            'res_model': 'asset.exit.process',
            'view_mode': 'list,form',
            'domain': [('employee_id', '=', self.id)],
            'context': {'default_employee_id': self.id},
        }

    def action_create_joining_process(self):
        self.ensure_one()
        return {
            'name': _('New Joining Process'),
            'type': 'ir.actions.act_window',
            'res_model': 'asset.joining.process',
            'view_mode': 'form',
            'target': 'current',
            'context': {'default_employee_id': self.id},
        }

    def action_create_exit_process(self):
        self.ensure_one()
        return {
            'name': _('New Exit Process'),
            'type': 'ir.actions.act_window',
            'res_model': 'asset.exit.process',
            'view_mode': 'form',
            'target': 'current',
            'context': {'default_employee_id': self.id},
        }

