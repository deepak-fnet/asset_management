from . import models


from odoo import api, SUPERUSER_ID

def post_init_hook(env):
    model = env['ir.model'].search([('model', '=', 'hr.payslip')], limit=1)
    if model:
        report = env.ref('report_management.action_payslip_report', raise_if_not_found=False)
        if report:
            report.binding_model_id = model.id