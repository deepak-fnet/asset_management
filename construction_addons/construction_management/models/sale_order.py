# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    retention_percent = fields.Float(
        string='Retention (%)', help="Percentage of each certified bill withheld as retention money.")
    dlp_months = fields.Integer(string='Defect Liability Period (Months)')

    costing_id = fields.Many2one('cm.costing', string='Sale Costing', related='opportunity_id.costing_id')
    master_project_id = fields.Many2one(
        'cm.master.project', string='Master Project', related='opportunity_id.master_project_id')
    master_project_state = fields.Selection(related='master_project_id.state', string='Project Status')
    master_project_start_date = fields.Date(related='master_project_id.start_date', string='Project Start Date')
    advance_received = fields.Monetary(related='master_project_id.advance_received')
    overall_completion = fields.Float(related='master_project_id.overall_completion', string='Overall Completion (%)')
    purchase_order_count = fields.Integer(related='opportunity_id.purchase_order_count')
    budget_amount = fields.Monetary(
        related='opportunity_id.budget_amount', string='Total Project Cost')
    purchased_amount = fields.Monetary(related='opportunity_id.purchased_amount', string='Purchase Cost')
    billed_amount = fields.Monetary(related='opportunity_id.billed_amount', string='Bill Cost')
    pending_bill_amount = fields.Monetary(
        related='opportunity_id.pending_bill_amount', string='Pending Billing')
    profit_amount = fields.Monetary(related='opportunity_id.profit_amount', string='Profit / Loss')
    subproject_ids = fields.One2many(
        'project.project', compute='_compute_subproject_ids', string='Sub-Projects')

    @api.depends('master_project_id.subproject_ids')
    def _compute_subproject_ids(self):
        for order in self:
            order.subproject_ids = order.master_project_id.subproject_ids

    def action_view_costing(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sale Costing'),
            'res_model': 'cm.costing',
            'view_mode': 'form',
            'res_id': self.costing_id.id,
        }

    def action_view_master_project(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Project'),
            'res_model': 'cm.master.project',
            'view_mode': 'form',
            'res_id': self.master_project_id.id,
        }

    def action_view_purchase_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Purchase Orders'),
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.opportunity_id.purchase_order_ids.ids)],
        }

    def action_view_flowchart(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'cm_flowchart_client_action',
            'name': _('Flow Chart'),
            'context': {'default_master_id': self.master_project_id.id},
        }

    def action_view_stage_pnl(self):
        self.ensure_one()
        stages = self.master_project_id.subproject_ids.cm_stage_ids
        return {
            'type': 'ir.actions.act_window',
            'name': _('Profit & Loss by Stage'),
            'res_model': 'cm.stage',
            'view_mode': 'list',
            'views': [(self.env.ref('construction_management.view_cm_stage_pnl_list').id, 'list')],
            'domain': [('id', 'in', stages.ids)],
        }

    def action_confirm(self):
        res = super().action_confirm()
        for order in self:
            master = self.env['cm.master.project'].search([('sale_order_id', '=', order.id)], limit=1)
            lead = order.opportunity_id
            if not master and lead:
                master = self.env['cm.master.project'].create({
                    'partner_id': order.partner_id.id,
                    'lead_id': lead.id,
                    'sale_order_id': order.id,
                    'total_contract_value': order.amount_total,
                    'start_date': fields.Date.context_today(order),
                })
                lead.master_project_id = master.id
            if master:
                master.total_contract_value = order.amount_total
                order._cm_create_subprojects_from_lines(master)
        return res

    def _cm_create_subprojects_from_lines(self, master):
        Project = self.env['project.project']
        existing_line_ids = set(Project.search(
            [('sale_order_line_id', 'in', self.order_line.ids)]).mapped('sale_order_line_id').ids)
        Project.create([{
            'name': line.name,
            'master_project_id': master.id,
            'partner_id': self.partner_id.id,
            'cm_budget': line.price_subtotal,
            'sale_order_line_id': line.id,
        } for line in self.order_line if not line.display_type and line.id not in existing_line_ids])
