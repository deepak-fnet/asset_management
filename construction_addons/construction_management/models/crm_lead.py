# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    land_extent = fields.Char(string='Land Extent', help="e.g. 2.5 acres")
    master_project_id = fields.Many2one('cm.master.project', string='Master Project', readonly=True, copy=False)

    # --- Tender / Opportunity ---
    tender_reference = fields.Char(string='Tender Reference No.')
    emd_amount = fields.Monetary(string='EMD Amount', currency_field='company_currency')
    tender_submission_date = fields.Date(string='Tender Submission Date')

    # --- Estimation & BOQ ---
    boq_line_ids = fields.One2many('cm.boq.line', 'lead_id', string='Estimation BOQ')
    estimation_total = fields.Monetary(
        string='Estimation Total', compute='_compute_estimation_total', currency_field='company_currency')

    # --- Quotation / Proposal ---
    sale_order_id = fields.Many2one('sale.order', string='Contract Quotation', readonly=True, copy=False)

    # --- Purchase / Profitability roll-up (across all sub-projects/stages) ---
    purchase_order_ids = fields.Many2many(
        'purchase.order', compute='_compute_financials', string='Purchase Orders')
    purchase_order_count = fields.Integer(compute='_compute_financials')
    budget_amount = fields.Monetary(
        string='Budget (Sale Order)', compute='_compute_financials', currency_field='company_currency')
    purchased_amount = fields.Monetary(
        string='Purchased Amount', compute='_compute_financials', currency_field='company_currency',
        help="Real total of confirmed Purchase Order lines across every stage of this project.")
    billed_amount = fields.Monetary(
        string='Billed Amount', compute='_compute_financials', currency_field='company_currency',
        help="Total of posted vendor bills across every stage of this project.")
    pending_bill_amount = fields.Monetary(
        string='Pending Bill', compute='_compute_financials', currency_field='company_currency',
        help="Purchased minus Billed - ordered/received but not yet invoiced by the vendor.")
    total_purchase_payment = fields.Monetary(
        string='Purchase Payments Made', compute='_compute_financials', currency_field='company_currency',
        help="Sum of all payments actually registered against vendor bills for this project's purchases.")
    profit_amount = fields.Monetary(
        string='Profit / Loss (Budget - Billed)', compute='_compute_financials',
        currency_field='company_currency')

    @api.depends('boq_line_ids.amount')
    def _compute_estimation_total(self):
        for lead in self:
            lead.estimation_total = sum(lead.boq_line_ids.mapped('amount'))

    @api.depends('sale_order_id.amount_total', 'master_project_id.subproject_ids.cm_stage_ids.purchase_order_ids',
                 'master_project_id.subproject_ids.cm_stage_ids.purchased_amount',
                 'master_project_id.subproject_ids.cm_stage_ids.billed_amount',
                 'master_project_id.subproject_ids.cm_stage_ids.payment_total')
    def _compute_financials(self):
        for lead in self:
            stages = lead.master_project_id.subproject_ids.cm_stage_ids
            orders = stages.purchase_order_ids
            lead.purchase_order_ids = orders
            lead.purchase_order_count = len(orders)
            lead.budget_amount = lead.sale_order_id.amount_total
            lead.purchased_amount = sum(stages.mapped('purchased_amount'))
            lead.billed_amount = sum(stages.mapped('billed_amount'))
            lead.pending_bill_amount = lead.purchased_amount - lead.billed_amount
            lead.total_purchase_payment = sum(stages.mapped('payment_total'))
            lead.profit_amount = lead.budget_amount - lead.billed_amount

    def action_set_won(self):
        res = super().action_set_won()
        for lead in self:
            if not lead.master_project_id:
                lead._handle_partner_assignment(create_missing=True)
                lead.master_project_id = self.env['cm.master.project'].create({
                    'partner_id': lead.partner_id.id,
                    'lead_id': lead.id,
                    'sale_order_id': lead.sale_order_id.id,
                    'total_contract_value': lead.sale_order_id.amount_total or lead.expected_revenue,
                    'start_date': fields.Date.context_today(lead),
                })
        return res

    def action_create_quotation(self):
        self.ensure_one()
        if self.sale_order_id:
            raise UserError(_("A quotation has already been created for this opportunity: %s") % self.sale_order_id.name)
        if not self.boq_line_ids:
            raise UserError(_("Add at least one Estimation BOQ line before creating a quotation."))
        if not self.partner_id:
            self._handle_partner_assignment(create_missing=True)
        contract_product = self.env.ref('construction_management.product_contract_value')
        order = self.env['sale.order'].create({
            'partner_id': self.partner_id.id,
            'opportunity_id': self.id,
            'order_line': [(0, 0, {
                'product_id': contract_product.product_variant_id.id,
                'name': self.name,
                'product_uom_qty': 1,
                'price_unit': self.estimation_total,
                'tax_ids': [(6, 0, [])],
            })],
        })
        self.sale_order_id = order.id
        return {
            'type': 'ir.actions.act_window',
            'name': _('Quotation'),
            'res_model': 'sale.order',
            'view_mode': 'form',
            'res_id': order.id,
        }

    def action_view_sale_order(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sale Order'),
            'res_model': 'sale.order',
            'view_mode': 'form',
            'res_id': self.sale_order_id.id,
        }

    def action_view_purchase_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Purchase Orders'),
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.purchase_order_ids.ids)],
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

    def action_view_master_project(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Master Project'),
            'res_model': 'cm.master.project',
            'view_mode': 'form',
            'res_id': self.master_project_id.id,
        }

    def action_view_flowchart(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'cm_flowchart_client_action',
            'name': _('Flow Chart'),
            'context': {'default_master_id': self.master_project_id.id},
        }
