"""Assigned assets that never got an Assignment History row - a common asset
(location / department, no employee) used to be skipped - get one now, from
the assignment they currently have."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    History = env['asset.assignment.history']
    assets = env['asset.asset'].search([('state', '=', 'assigned')])
    assets = assets.filtered(lambda a: not a.assignment_history_ids)
    for asset in assets:
        History.create({
            'asset_id': asset.id,
            'employee_id': asset.assigned_employee_id.id,
            'action': 'assign',
            'date': asset.assignment_date or asset.write_date,
            'description': 'Assigned to %s' % asset._assignment_target_text(),
        })
