from odoo import _, fields, models
from odoo.exceptions import UserError


class AssetAssetAssignTarget(models.Model):
    _inherit = 'asset.asset'

    def _assign_to_target(self, assign_type, employee=False, department=False, location=False):
        """Assign these assets to an employee, or as a common asset to a
        location / department.

        * employee: employee, department and location are all required (the
          department is the employee's own).
        * common: at least one of location, department or employee.
        """
        if assign_type == 'employee':
            if not employee:
                raise UserError(_("Select the employee."))
            department = department or employee.sudo().department_id
            if not department or not location:
                raise UserError(_("Assigning to an employee needs the employee, "
                                  "their department and a location."))
        else:
            # A common asset belongs to a place / team, never to a person.
            employee = False
            if not (department or location):
                raise UserError(_("A common asset needs a location or a department."))
        self.write({
            'assigned_employee_id': employee.id if employee else False,
            'department_id': department.id if department else False,
            'location_id': location.id if location else False,
            'assignment_date': fields.Date.context_today(self),
            'state': 'assigned',
        })
