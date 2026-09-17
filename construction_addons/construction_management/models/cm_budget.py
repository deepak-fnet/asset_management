# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ConstructionBudgetLine(models.Model):
    _name = 'cm.budget.line'
    _description = 'Construction Budget Line (Planned vs Actual by Cost Head)'
    _order = 'project_id, cost_head'

    project_id = fields.Many2one('project.project', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='project_id.currency_id', store=True)
    cost_head = fields.Selection([
        ('material', 'Material'),
        ('labour', 'Labour'),
        ('subcontract', 'Subcontract'),
        ('equipment', 'Equipment'),
        ('overhead', 'Overhead'),
    ], required=True)
    planned_amount = fields.Monetary(string='Planned Amount', required=True)
    actual_amount = fields.Monetary(string='Actual Amount', compute='_compute_actual_amount')
    variance = fields.Monetary(compute='_compute_variance')

    _uniq_cost_head_per_project = models.Constraint(
        'UNIQUE(project_id, cost_head)',
        'A budget line for this cost head already exists on this project.',
    )

    @api.depends('project_id.cm_stage_ids.purchased_amount', 'project_id.cm_stage_ids.dpr_ids.labour_wage_total')
    def _compute_actual_amount(self):
        for line in self:
            stages = line.project_id.cm_stage_ids
            if line.cost_head == 'material':
                line.actual_amount = sum(stages.mapped('purchased_amount'))
            elif line.cost_head == 'labour':
                line.actual_amount = sum(stages.mapped('dpr_ids.labour_wage_total'))
            else:
                line.actual_amount = 0.0

    @api.depends('planned_amount', 'actual_amount')
    def _compute_variance(self):
        for line in self:
            line.variance = line.planned_amount - line.actual_amount
