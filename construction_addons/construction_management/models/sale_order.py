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
    labour_wage_total = fields.Monetary(
        related='opportunity_id.labour_wage_total', string='Labour Wages (Attendance)')
    profit_amount = fields.Monetary(related='opportunity_id.profit_amount', string='Profit / Loss')
    subproject_ids = fields.One2many(
        'project.project', compute='_compute_subproject_ids', string='Sub-Projects')
    milestone_bill_ids = fields.One2many('cm.milestone.bill', 'sale_order_id', string='Milestone Bills')
    milestone_bill_count = fields.Integer(compute='_compute_milestone_bill_count')
    variation_order_ids = fields.One2many('cm.variation.order', 'sale_order_id', string='Variation Orders')
    variation_order_count = fields.Integer(compute='_compute_variation_order_count')

    @api.depends('master_project_id.subproject_ids')
    def _compute_subproject_ids(self):
        for order in self:
            order.subproject_ids = order.master_project_id.subproject_ids

    @api.depends('order_line.invoice_lines', 'milestone_bill_ids.invoice_id')
    def _get_invoiced(self):
        # Milestone/Advance/Retention invoices are created directly (they bill a % of the
        # whole contract, not any single order line) so they never populate the native
        # sale.order.line.invoice_lines relation the base method relies on - fold them in here
        # so the native "Invoices" smart button and invoice_count reflect them too.
        super()._get_invoiced()
        for order in self:
            milestone_invoices = order.milestone_bill_ids.invoice_id.filtered(
                lambda m: m.move_type in ('out_invoice', 'out_refund'))
            order.invoice_ids = order.invoice_ids | milestone_invoices
            order.invoice_count = len(order.invoice_ids)

    def _compute_milestone_bill_count(self):
        for order in self:
            order.milestone_bill_count = len(order.milestone_bill_ids)

    def _compute_variation_order_count(self):
        for order in self:
            order.variation_order_count = len(order.variation_order_ids)

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

    def action_view_milestone_bills(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Milestone Bills'),
            'res_model': 'cm.milestone.bill',
            'view_mode': 'list,form',
            'domain': [('sale_order_id', '=', self.id)],
            'context': {'default_sale_order_id': self.id},
        }

    def action_view_variation_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Variation Orders'),
            'res_model': 'cm.variation.order',
            'view_mode': 'list,form',
            'domain': [('sale_order_id', '=', self.id)],
            'context': {'default_sale_order_id': self.id},
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
            'variation_order_id': line.variation_order_id.id,
        } for line in self.order_line if not line.display_type and line.id not in existing_line_ids])
