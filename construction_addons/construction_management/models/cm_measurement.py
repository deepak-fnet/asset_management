# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ConstructionMeasurement(models.Model):
    _name = 'cm.measurement'
    _description = 'Measurement / Quantity Verification'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'measurement_date desc, id desc'

    boq_line_id = fields.Many2one('cm.boq.line', required=True, index=True, string='BOQ Item')
    stage_id = fields.Many2one(related='boq_line_id.stage_id', store=True, string='Stage')
    project_id = fields.Many2one(related='stage_id.project_id', store=True)
    product_id = fields.Many2one(related='boq_line_id.product_id', store=True, string='Item')
    uom_id = fields.Many2one(related='boq_line_id.uom_id')
    rate = fields.Float(related='boq_line_id.rate')
    currency_id = fields.Many2one(related='stage_id.currency_id')
    measurement_date = fields.Date(required=True, default=fields.Date.context_today)
    qty_this_measurement = fields.Float(required=True, string='Qty Measured')
    cumulative_qty = fields.Float(compute='_compute_cumulative_qty', store=True,
                                   help="Total certified quantity for this BOQ item, up to and including this "
                                        "measurement's date.")
    amount = fields.Monetary(compute='_compute_amount', store=True)
    measured_by = fields.Many2one('res.users', default=lambda self: self.env.user)
    remarks = fields.Char()
    state = fields.Selection([
        ('draft', 'Draft'),
        ('certified', 'Certified'),
    ], default='draft', tracking=True, copy=False)

    @api.depends('boq_line_id', 'qty_this_measurement', 'state', 'measurement_date')
    def _compute_cumulative_qty(self):
        for rec in self:
            if not rec.boq_line_id:
                rec.cumulative_qty = 0.0
                continue
            domain = [
                ('boq_line_id', '=', rec.boq_line_id.id),
                ('state', '=', 'certified'),
                ('measurement_date', '<=', rec.measurement_date),
            ]
            if rec.id:
                domain.append(('id', '!=', rec.id))
            certified = self.search(domain)
            total = sum(certified.mapped('qty_this_measurement'))
            if rec.state == 'certified':
                total += rec.qty_this_measurement
            rec.cumulative_qty = total

    @api.depends('qty_this_measurement', 'rate')
    def _compute_amount(self):
        for rec in self:
            rec.amount = rec.qty_this_measurement * rec.rate

    def action_certify(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only a draft measurement can be certified."))
            rec.state = 'certified'

    def action_reset_to_draft(self):
        self.write({'state': 'draft'})
