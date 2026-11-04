from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    budget_exceed_mode = fields.Selection(
        [('approval', 'Extra approval level'), ('block', 'Block')], string='Over Budget',
        config_parameter='asset_budget.exceed_mode', default='approval',
        help="What happens when a request's RFQ / order is over its budget on Submit for Approval.")
    budget_approver_id = fields.Many2one(
        'res.users', string='Budget Approver', config_parameter='asset_budget.approver_id',
        domain=[('share', '=', False)],
        help="Added as the last approval level when an order is over budget.")
