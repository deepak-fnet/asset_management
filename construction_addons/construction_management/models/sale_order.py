# -*- coding: utf-8 -*-
from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    retention_percent = fields.Float(
        string='Retention (%)', help="Percentage of each certified bill withheld as retention money.")
    dlp_months = fields.Integer(string='Defect Liability Period (Months)')

    def action_confirm(self):
        res = super().action_confirm()
        for order in self:
            master = self.env['cm.master.project'].search([('sale_order_id', '=', order.id)], limit=1)
            if not master and order.opportunity_id and order.opportunity_id.master_project_id:
                master = order.opportunity_id.master_project_id
                master.sale_order_id = order.id
            if master:
                master.total_contract_value = order.amount_total
        return res
