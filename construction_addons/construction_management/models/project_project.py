# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ProjectProject(models.Model):
    _inherit = 'project.project'

    master_project_id = fields.Many2one(
        'cm.master.project', string='Master Project', tracking=True, index=True, ondelete='cascade')
    project_no = fields.Char(string='Project No.', copy=False)
    sale_order_line_id = fields.Many2one(
        'sale.order.line', string='Source Quotation Line', copy=False, index=True,
        help="The quotation line this sub-project was auto-created from, if any.")
    variation_order_id = fields.Many2one(
        'cm.variation.order', string='Source Variation Order', copy=False, index=True,
        help="Set if this sub-project was created from new scope added after the contract was "
             "confirmed, via a Variation Order, rather than from the original quotation.")
    allow_task_dependencies = fields.Boolean(default=True)
    allow_milestones = fields.Boolean(default=True)
    cm_budget = fields.Monetary(string='Allocated Budget', currency_field='currency_id', tracking=True)
    floors = fields.Integer(string='Floors')
    cm_status = fields.Selection([
        ('not_started', 'Not Started'),
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('closed', 'Closed'),
    ], compute='_compute_cm_status', store=True, tracking=True, copy=False,
        help="Derived automatically from the stages: Active once any stage has started, "
             "Completed once every stage is certified. Closed is set only by closing the "
             "sub-project and then stays fixed regardless of later stage changes.")

    cm_stage_ids = fields.One2many('cm.stage', 'project_id', string='Construction Stages')
    cm_stage_count = fields.Integer(compute='_compute_cm_stage_count')
    completion_percent = fields.Float(
        string='Completion (%)', compute='_compute_completion_percent', store=True,
        help="Stages fully completed and certified, divided by total configured stages.")

    budget_line_ids = fields.One2many('cm.budget.line', 'project_id', string='Budget Lines')
    budget_line_count = fields.Integer(compute='_compute_budget_line_count')

    purchased_amount = fields.Monetary(compute='_compute_financials', string='Purchased Amount')
    billed_amount = fields.Monetary(compute='_compute_financials', string='Billed Amount')
    pending_bill_amount = fields.Monetary(compute='_compute_financials', string='Pending Bill')
    payment_total = fields.Monetary(compute='_compute_financials', string='Payments Made')
    labour_wage_total = fields.Monetary(
        compute='_compute_financials', string='Labour Wages (Attendance)',
        help="Sum of Labour Attendance wages across every stage - shown only as a cross-check "
             "against the Labour BOQ line's real purchased/billed amount; not included in "
             "Billed Amount or Budget Variance, which come only from real bills.")
    profit_amount = fields.Monetary(
        compute='_compute_financials', string='Budget Variance',
        help="Allocated Budget minus Billed Amount across every stage of this sub-project - "
             "whether spend stayed within the internal cost budget. This is NOT the contract "
             "margin (Allocated Budget here is a pure cost envelope, unlike the Sale Order's "
             "Profit/Loss which is measured against the marked-up contract value) - the two "
             "numbers will not match, by design.")

    final_inspection_done = fields.Boolean(string='Final Quality Inspection Done')
    final_payment_cleared = fields.Boolean(
        string='Final Payment Cleared', compute='_compute_final_payment_cleared')
    client_signoff = fields.Boolean(string='Client Sign-off')
    keys_handed_over = fields.Boolean(string='Keys Handed Over')
    electricity_connection_transferred = fields.Boolean(string='Electricity Connection Transferred')
    water_connection_transferred = fields.Boolean(string='Water Connection Transferred')
    warranty_documents_handed_over = fields.Boolean(string='Warranty Documents Handed Over')
    handover_date = fields.Date(string='Handover Date')
    warranty_months = fields.Integer(string='Warranty Period (Months)')
    handover_certificate_ids = fields.One2many(
        'cm.handover.certificate', 'project_id', string='Handover Certificates')
    handover_certificate_count = fields.Integer(compute='_compute_handover_certificate_count')
    snag_item_ids = fields.One2many('cm.snag.item', 'project_id', string='Snag Items')
    snag_item_count = fields.Integer(compute='_compute_snag_item_count')
    open_snag_item_count = fields.Integer(compute='_compute_snag_item_count')

    def _compute_cm_stage_count(self):
        for project in self:
            project.cm_stage_count = len(project.cm_stage_ids)

    @api.depends('cm_stage_ids.state')
    def _compute_cm_status(self):
        for project in self:
            if project.cm_status == 'closed':
                continue
            stages = project.cm_stage_ids
            if stages and all(s.state == 'certified' for s in stages):
                project.cm_status = 'completed'
            elif stages and any(s.state != 'not_started' for s in stages):
                project.cm_status = 'active'
            else:
                project.cm_status = 'not_started'

    @api.depends('cm_stage_ids.state')
    def _compute_completion_percent(self):
        for project in self:
            stages = project.cm_stage_ids
            if stages:
                certified = len(stages.filtered(lambda s: s.state == 'certified'))
                project.completion_percent = certified / len(stages) * 100.0
            else:
                project.completion_percent = 0.0

    @api.depends('cm_stage_ids.purchased_amount', 'cm_stage_ids.billed_amount', 'cm_stage_ids.payment_total',
                 'cm_stage_ids.labour_wage_total')
    def _compute_financials(self):
        for project in self:
            stages = project.cm_stage_ids
            project.purchased_amount = sum(stages.mapped('purchased_amount'))
            project.billed_amount = sum(stages.mapped('billed_amount'))
            project.pending_bill_amount = project.purchased_amount - project.billed_amount
            project.payment_total = sum(stages.mapped('payment_total'))
            project.labour_wage_total = sum(stages.mapped('labour_wage_total'))
            project.profit_amount = project.cm_budget - project.billed_amount

    def _compute_final_payment_cleared(self):
        for project in self:
            bills = self.env['account.move'].search([
                ('stage_id', 'in', project.cm_stage_ids.ids), ('move_type', '=', 'in_invoice'),
                ('state', '=', 'posted'),
            ])
            project.final_payment_cleared = bool(bills) and all(
                b.payment_state in ('paid', 'in_payment') for b in bills)

    def _compute_budget_line_count(self):
        for project in self:
            project.budget_line_count = len(project.budget_line_ids)

    def _compute_handover_certificate_count(self):
        for project in self:
            project.handover_certificate_count = len(project.handover_certificate_ids)

    def _compute_snag_item_count(self):
        for project in self:
            project.snag_item_count = len(project.snag_item_ids)
            project.open_snag_item_count = len(
                project.snag_item_ids.filtered(lambda s: s.state != 'verified'))

    def action_view_budget_lines(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Budget Lines'),
            'res_model': 'cm.budget.line',
            'view_mode': 'list,form',
            'domain': [('project_id', '=', self.id)],
            'context': {'default_project_id': self.id},
        }

    def action_view_cm_stages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Construction Stages'),
            'res_model': 'cm.stage',
            'view_mode': 'list,form',
            'domain': [('project_id', '=', self.id)],
            'context': {'default_project_id': self.id},
        }

    def action_close_subproject(self):
        for project in self:
            not_certified = project.cm_stage_ids.filtered(lambda s: s.state != 'certified')
            if not project.cm_stage_ids or not_certified:
                raise UserError(_(
                    "All stages must be certified before closing this sub-project. "
                    "Not yet certified: %s") % ', '.join(not_certified.mapped('name')))
            if not project.final_payment_cleared:
                raise UserError(_("All payments linked to this sub-project must be marked paid first."))
            checklist = [
                (project.final_inspection_done, _("Final Quality Inspection Done")),
                (project.client_signoff, _("Client Sign-off / Acceptance")),
                (project.keys_handed_over, _("Keys Handed Over")),
                (project.electricity_connection_transferred, _("Electricity Connection Transferred")),
                (project.water_connection_transferred, _("Water Connection Transferred")),
                (project.warranty_documents_handed_over, _("Warranty Documents Handed Over")),
            ]
            missing = [label for done, label in checklist if not done]
            if missing:
                raise UserError(_(
                    "Complete the handover checklist before closing this sub-project. "
                    "Still missing: %s") % ', '.join(missing))
            project.cm_status = 'closed'
            project._cm_create_handover_certificate()

    def _cm_create_handover_certificate(self):
        self.ensure_one()
        if self.handover_certificate_ids:
            return self.handover_certificate_ids[0]
        return self.env['cm.handover.certificate'].create({
            'project_id': self.id,
            'handover_date': self.handover_date or fields.Date.context_today(self),
            'warranty_months': self.warranty_months,
            'final_inspection_done': self.final_inspection_done,
            'client_signoff': self.client_signoff,
            'keys_handed_over': self.keys_handed_over,
            'electricity_connection_transferred': self.electricity_connection_transferred,
            'water_connection_transferred': self.water_connection_transferred,
            'warranty_documents_handed_over': self.warranty_documents_handed_over,
            'issued_by': self.env.user.id,
        })

    def action_view_handover_certificates(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Handover Certificates'),
            'res_model': 'cm.handover.certificate',
            'view_mode': 'list,form',
            'domain': [('project_id', '=', self.id)],
        }

    def action_view_snag_items(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Snag Items'),
            'res_model': 'cm.snag.item',
            'view_mode': 'list,form',
            'domain': [('project_id', '=', self.id)],
            'context': {'default_project_id': self.id},
        }
