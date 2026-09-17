# -*- coding: utf-8 -*-
from odoo import api, fields, models, Command, _
from odoo.exceptions import UserError


class ConstructionStage(models.Model):
    _name = 'cm.stage'
    _description = 'Construction Project Stage'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'project_id, sequence, id'

    name = fields.Char(required=True, tracking=True)
    sequence = fields.Integer(default=10)
    project_id = fields.Many2one('project.project', required=True, ondelete='cascade', index=True)
    master_project_id = fields.Many2one(related='project_id.master_project_id', store=True)
    company_id = fields.Many2one(related='project_id.company_id', store=True)
    currency_id = fields.Many2one(related='project_id.currency_id', store=True)

    budget_allocation = fields.Monetary(string='Budget Allocation', tracking=True)
    target_date = fields.Date(string='Target Completion Date', tracking=True)
    milestone_id = fields.Many2one('project.milestone', string='Project Milestone', readonly=True, copy=False)

    boq_line_ids = fields.One2many('cm.boq.line', 'stage_id', string='BOQ Items')
    boq_total = fields.Monetary(compute='_compute_boq_total', store=True, string='BOQ Total')
    material_boq_total = fields.Monetary(
        compute='_compute_boq_total', store=True, string='Material Total (for PO)',
        help="BOQ total excluding service/labour lines - what a Purchase Order for this stage covers.")
    boq_actual_total = fields.Monetary(
        compute='_compute_boq_total', string='BOQ Actual',
        help="Sum of the real Actual Amount across every BOQ line - material actually issued to "
             "site plus labour/services actually purchased. Starts at 0 and grows as work "
             "happens; BOQ Total above stays a fixed qty x rate estimate for comparison.")
    can_fetch_lead_boq = fields.Boolean(compute='_compute_can_fetch_lead_boq')

    task_ids = fields.One2many('project.task', 'cm_stage_id', string='Room / Work Tasks')
    percent_complete = fields.Float(
        compute='_compute_percent_complete', store=True, string='Physical Progress (%)',
        help="Average, across each room/work task on this stage, of that task's own completion "
             "(fully done if it has no sub-tasks, else closed sub-tasks / total sub-tasks). "
             "Informational only - stage/sub-project completion for closure purposes is driven "
             "by certification, not this figure.")
    task_count = fields.Integer(compute='_compute_task_stats', string='Tasks')
    task_done_count = fields.Integer(compute='_compute_task_stats', string='Tasks Done')
    subtask_count = fields.Integer(compute='_compute_task_stats', string='Sub-tasks')
    subtask_done_count = fields.Integer(compute='_compute_task_stats', string='Sub-tasks Done')

    purchase_order_ids = fields.One2many('purchase.order', 'stage_id', string='Purchase Orders')
    purchase_order_count = fields.Integer(compute='_compute_purchase_order_count')
    procurement_plan_ids = fields.One2many('purchase.requisition', 'stage_id', string='Procurement Plans')
    procurement_plan_count = fields.Integer(compute='_compute_purchase_order_count')
    purchased_amount = fields.Monetary(
        compute='_compute_cost_amounts', string='Purchased',
        help="Real total (price total, not qty x BOQ rate) of confirmed Purchase Order lines "
             "raised for this stage's BOQ items - actual vendor rates may differ line to line.")
    used_amount = fields.Monetary(
        compute='_compute_cost_amounts', string='Used (Paid to Vendors)',
        help="Sum of payments actually registered against vendor bills for this stage's purchases.")
    billed_amount = fields.Monetary(
        compute='_compute_payments', string='Billed Amount',
        help="Total of posted vendor bills for this stage's purchases - can differ from Purchased "
             "when a PO has been received/ordered but not yet invoiced by the vendor.")
    pending_bill_amount = fields.Monetary(
        compute='_compute_payments', string='Pending Bill',
        help="Purchased minus Billed - ordered but not yet invoiced by the vendor.")
    profit_amount = fields.Monetary(
        compute='_compute_payments', string='Profit / Loss',
        help="Budget Allocation minus Billed Amount for this stage.")
    payment_ids = fields.Many2many('account.payment', compute='_compute_payments', string='Payments')
    payment_count = fields.Integer(compute='_compute_payments')
    payment_total = fields.Monetary(compute='_compute_payments', string='Payments Made')

    ncr_ids = fields.One2many('cm.ncr', 'stage_id', string='NCRs')
    ncr_count = fields.Integer(compute='_compute_quality_safety_counts')
    safety_incident_ids = fields.One2many('cm.safety.incident', 'stage_id', string='Safety Incidents')
    safety_incident_count = fields.Integer(compute='_compute_quality_safety_counts')

    labour_attendance_ids = fields.One2many('cm.labour.attendance', 'stage_id', string='Labour Attendance')
    labour_attendance_count = fields.Integer(compute='_compute_dpr_count')
    dpr_ids = fields.One2many('cm.dpr', 'stage_id', string='Daily Progress Reports')
    dpr_count = fields.Integer(compute='_compute_dpr_count')

    measurement_ids = fields.One2many('cm.measurement', 'stage_id', string='Measurements')
    measurement_count = fields.Integer(compute='_compute_measurement_stats')
    measured_amount = fields.Monetary(
        compute='_compute_measurement_stats',
        help="Total value of certified measurements recorded against this stage's BOQ items.")

    state = fields.Selection([
        ('not_started', 'Not Started'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('certified', 'Certified'),
    ], default='not_started', tracking=True, copy=False)
    certified_by = fields.Many2one('res.users', string='Certified By', readonly=True, copy=False)
    certified_date = fields.Datetime(string='Certification Date', readonly=True, copy=False)

    @api.depends('boq_line_ids.amount', 'boq_line_ids.product_id.type')
    def _compute_boq_total(self):
        for stage in self:
            stage.boq_total = sum(stage.boq_line_ids.mapped('amount'))
            stage.material_boq_total = sum(
                line.amount for line in stage.boq_line_ids if line.product_id.type != 'service'
            )
            stage.boq_actual_total = sum(stage.boq_line_ids.mapped('actual_amount'))

    @api.depends('task_ids.state', 'task_ids.child_ids.state')
    def _compute_percent_complete(self):
        for stage in self:
            top_tasks = stage.task_ids.filtered(lambda t: not t.parent_id)
            if not top_tasks:
                stage.percent_complete = 0.0
                continue
            total = 0.0
            for task in top_tasks:
                children = task.child_ids
                if children:
                    total += len(children.filtered('is_closed')) / len(children) * 100.0
                else:
                    total += 100.0 if task.is_closed else 0.0
            stage.percent_complete = total / len(top_tasks)

    def _compute_purchase_order_count(self):
        for stage in self:
            stage.purchase_order_count = len(stage.purchase_order_ids)
            stage.procurement_plan_count = len(stage.procurement_plan_ids)

    @api.depends('task_ids.state', 'task_ids.parent_id', 'task_ids.child_ids.state')
    def _compute_task_stats(self):
        for stage in self:
            top_tasks = stage.task_ids.filtered(lambda t: not t.parent_id)
            children = top_tasks.mapped('child_ids')
            stage.task_count = len(top_tasks)
            stage.task_done_count = len(top_tasks.filtered('is_closed'))
            stage.subtask_count = len(children)
            stage.subtask_done_count = len(children.filtered('is_closed'))

    @api.depends('boq_line_ids.purchased_amount', 'payment_total')
    def _compute_cost_amounts(self):
        for stage in self:
            stage.purchased_amount = sum(stage.boq_line_ids.mapped('purchased_amount'))
            stage.used_amount = stage.payment_total

    @api.depends('purchase_order_ids.state', 'budget_allocation', 'purchased_amount')
    def _compute_payments(self):
        for stage in self:
            bills = self.env['account.move'].search([
                ('stage_id', '=', stage.id), ('move_type', '=', 'in_invoice'), ('state', '=', 'posted'),
            ])
            payments = self.env['account.payment'].search(
                [('reconciled_bill_ids', 'in', bills.ids)]) if bills else self.env['account.payment']
            stage.payment_ids = payments
            stage.payment_count = len(payments)
            stage.payment_total = sum(payments.mapped('amount'))
            stage.billed_amount = sum(bills.mapped('amount_total'))
            stage.pending_bill_amount = stage.purchased_amount - stage.billed_amount
            stage.profit_amount = stage.budget_allocation - stage.billed_amount

    @api.depends('master_project_id.lead_id.boq_line_ids', 'boq_line_ids.source_line_id')
    def _compute_can_fetch_lead_boq(self):
        for stage in self:
            lead = stage.master_project_id.lead_id
            fetched = stage.boq_line_ids.mapped('source_line_id')
            stage.can_fetch_lead_boq = bool(lead and (lead.boq_line_ids - fetched))

    def _compute_quality_safety_counts(self):
        for stage in self:
            stage.ncr_count = len(stage.ncr_ids)
            stage.safety_incident_count = len(stage.safety_incident_ids)

    def _compute_dpr_count(self):
        for stage in self:
            stage.dpr_count = len(stage.dpr_ids)
            stage.labour_attendance_count = len(stage.labour_attendance_ids)

    @api.depends('measurement_ids.state', 'measurement_ids.amount')
    def _compute_measurement_stats(self):
        for stage in self:
            certified = stage.measurement_ids.filtered(lambda m: m.state == 'certified')
            stage.measurement_count = len(stage.measurement_ids)
            stage.measured_amount = sum(certified.mapped('amount'))

    @api.model_create_multi
    def create(self, vals_list):
        stages = super().create(vals_list)
        for stage in stages:
            if stage.project_id and not stage.milestone_id:
                stage.milestone_id = self.env['project.milestone'].create({
                    'name': stage.name,
                    'project_id': stage.project_id.id,
                    'deadline': stage.target_date,
                }).id
        return stages

    def action_start(self):
        self.filtered(lambda s: s.state == 'not_started').write({'state': 'in_progress'})

    def action_mark_completed(self):
        for stage in self:
            if stage.state not in ('in_progress', 'not_started'):
                raise UserError(_("Only an in-progress stage can be marked completed."))
        self.write({'state': 'completed'})

    def action_certify(self):
        for stage in self:
            if stage.state != 'completed':
                raise UserError(_("Only a completed stage can be certified."))
            stage.write({
                'state': 'certified',
                'certified_by': self.env.user.id,
                'certified_date': fields.Datetime.now(),
            })
            if stage.milestone_id:
                stage.milestone_id.is_reached = True

    def action_view_purchase_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Purchase Orders'),
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('stage_id', '=', self.id)],
            'context': {'default_stage_id': self.id, 'default_project_id': self.project_id.id},
        }

    def action_view_procurement_plans(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Procurement Plans'),
            'res_model': 'purchase.requisition',
            'view_mode': 'list,form',
            'domain': [('stage_id', '=', self.id)],
        }

    def action_view_payments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Payments'),
            'res_model': 'account.payment',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.payment_ids.ids)],
        }

    def action_view_ncrs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('NCRs'),
            'res_model': 'cm.ncr',
            'view_mode': 'list,form',
            'domain': [('stage_id', '=', self.id)],
            'context': {'default_stage_id': self.id, 'default_project_id': self.project_id.id},
        }

    def action_view_safety_incidents(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Safety Incidents'),
            'res_model': 'cm.safety.incident',
            'view_mode': 'list,form',
            'domain': [('stage_id', '=', self.id)],
            'context': {'default_stage_id': self.id, 'default_project_id': self.project_id.id},
        }

    def action_view_labour_attendance(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Labour Attendance'),
            'res_model': 'cm.labour.attendance',
            'view_mode': 'list,form',
            'domain': [('stage_id', '=', self.id)],
            'context': {'default_stage_id': self.id, 'default_project_id': self.project_id.id},
        }

    def action_view_dprs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Daily Progress Reports'),
            'res_model': 'cm.dpr',
            'view_mode': 'list,form',
            'domain': [('stage_id', '=', self.id)],
            'context': {'default_stage_id': self.id, 'default_project_id': self.project_id.id},
        }

    def action_view_store_ledger(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Store Ledger - %s') % self.name,
            'res_model': 'cm.boq.line',
            'view_mode': 'list',
            'views': [(self.env.ref('construction_management.view_cm_boq_line_ledger_list').id, 'list')],
            'domain': [('stage_id', '=', self.id)],
        }

    def action_view_measurements(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Measurements'),
            'res_model': 'cm.measurement',
            'view_mode': 'list,form',
            'domain': [('stage_id', '=', self.id)],
        }

    def action_fetch_boq_from_lead(self):
        self.ensure_one()
        lead = self.master_project_id.lead_id
        if not lead:
            raise UserError(_(
                "This stage's sub-project is not linked to a CRM opportunity with an estimation BOQ."))
        fetched = self.boq_line_ids.mapped('source_line_id')
        to_fetch = lead.boq_line_ids - fetched
        if not to_fetch:
            raise UserError(_("All estimation BOQ items have already been fetched to this stage."))
        self.env['cm.boq.line'].create([{
            'stage_id': self.id,
            'source_line_id': line.id,
            'product_id': line.product_id.id,
            'sequence': line.sequence,
            'quantity': 0.0,
            'rate': line.rate,
        } for line in to_fetch])
        return True

    def action_create_requisition(self):
        self.ensure_one()
        requested_lines = self.boq_line_ids.filtered(
            lambda l: l.state == 'requested' and not l.requisition_id)
        if not requested_lines:
            raise UserError(_(
                "No requested items (material or labour) are pending a procurement plan on this "
                "stage. The site team must raise a request on a BOQ line first."))
        requisition = self.env['purchase.requisition'].create({
            'project_id': self.project_id.id,
            'stage_id': self.id,
            'requisition_type': 'purchase_template',
            'line_ids': [Command.create({
                'product_id': line.product_id.id,
                'product_qty': line.quantity,
                'price_unit': line.rate,
            }) for line in requested_lines],
        })
        requisition.action_confirm()
        requested_lines.write({'requisition_id': requisition.id, 'state': 'procurement'})
        return {
            'type': 'ir.actions.act_window',
            'name': _('Procurement Plan / RFQ'),
            'res_model': 'purchase.requisition',
            'view_mode': 'form',
            'res_id': requisition.id,
        }

    def _get_site_consumption_location(self, warehouse):
        location = self.env['stock.location'].search([
            ('usage', '=', 'internal'),
            ('location_id', '=', warehouse.view_location_id.id),
            ('name', '=', 'Site Consumption'),
        ], limit=1)
        if not location:
            location = self.env['stock.location'].create({
                'name': 'Site Consumption',
                'usage': 'internal',
                'location_id': warehouse.view_location_id.id,
                'company_id': warehouse.company_id.id,
            })
        return location

    def action_create_material_issue(self):
        self.ensure_one()
        material_lines = self.boq_line_ids.filtered(
            lambda l: l.product_id.type != 'service' and l.quantity > l.issued_qty)
        if not material_lines:
            raise UserError(_("No material items requested on this stage yet to issue."))
        company = self.company_id or self.env.company
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
        if not warehouse:
            raise UserError(_("No warehouse configured for company %s.") % company.name)
        site_location = self._get_site_consumption_location(warehouse)

        def default_qty(line):
            # Prefer stock actually received for this stage's own purchases; if there is
            # none (e.g. the material was already on hand / bought for another stage),
            # fall back to whatever the warehouse has available overall, capped at what's
            # still outstanding on the request. Real availability is enforced natively by
            # the picking's own reservation, not by this figure - it is only a starting point.
            still_requested = line.quantity - line.issued_qty
            qty = line.balance_qty if line.balance_qty > 0 else line.product_id.qty_available
            return max(min(qty, still_requested), 0.0)

        picking = self.env['stock.picking'].create({
            'picking_type_id': warehouse.int_type_id.id,
            'partner_id': self.project_id.partner_id.id,
            'origin': self.name,
            'project_id': self.project_id.id,
            'stage_id': self.id,
            'is_material_issue': True,
            'move_ids': [Command.create({
                'product_id': line.product_id.id,
                'product_uom_qty': default_qty(line),
                'product_uom': line.product_id.uom_id.id,
            }) for line in material_lines],
        })
        # location_id/location_dest_id are precompute fields keyed off picking_type_id's own
        # defaults (which point back to WH/Stock for a single-step warehouse) - set the real
        # destination (the site) explicitly after creation, both on the picking and its moves.
        picking.location_id = warehouse.lot_stock_id.id
        picking.location_dest_id = site_location.id
        picking.move_ids.write({
            'location_id': warehouse.lot_stock_id.id,
            'location_dest_id': site_location.id,
        })
        picking.action_confirm()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Material Issue (Internal Transfer)'),
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': picking.id,
        }
