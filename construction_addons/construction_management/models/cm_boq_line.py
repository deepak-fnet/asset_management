# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ConstructionBoqLine(models.Model):
    _name = 'cm.boq.line'
    _description = 'Construction BOQ Line'
    _order = 'stage_id, lead_id, sequence, id'

    _stage_xor_lead = models.Constraint(
        'CHECK((stage_id IS NOT NULL AND lead_id IS NULL) OR (stage_id IS NULL AND lead_id IS NOT NULL))',
        'A BOQ line must belong to either a construction stage (execution BOQ) or an '
        'opportunity (estimation BOQ), not both and not neither.',
    )

    stage_id = fields.Many2one('cm.stage', ondelete='cascade', index=True)
    lead_id = fields.Many2one('crm.lead', string='Opportunity (Estimation)', ondelete='cascade', index=True)
    source_line_id = fields.Many2one(
        'cm.boq.line', string='Estimation Source', readonly=True, copy=False,
        help="The lead-level estimation BOQ line this execution line was fetched from, if any.")
    project_id = fields.Many2one('project.project', compute='_compute_project_id', store=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('product.product', required=True, string='Item')
    product_type = fields.Selection(related='product_id.type', string='Type')
    uom_id = fields.Many2one(related='product_id.uom_id', string='UoM', readonly=True)
    quantity = fields.Float(required=True, default=1.0)
    rate = fields.Float(
        default=0.0,
        help="An estimate only (used for the BOQ Total planning figure) - not required, since a "
             "line added directly on a stage may have no known rate yet until it's purchased.")
    currency_id = fields.Many2one('res.currency', compute='_compute_currency_id')
    amount = fields.Monetary(compute='_compute_amount', store=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('requested', 'Requested'),
        ('procurement', 'In Procurement'),
    ], default='draft', copy=False,
        help="Execution BOQ lines only: the site team raises a request once the quantity to "
             "procure is known; the purchase team then plans procurement from requested lines.")
    requisition_id = fields.Many2one(
        'purchase.requisition', string='Procurement Plan', readonly=True, copy=False)

    purchased_qty = fields.Float(compute='_compute_purchase_stats', string='Purchased Qty')
    purchased_amount = fields.Monetary(
        compute='_compute_purchase_stats', string='Purchased Amount', currency_field='currency_id',
        help="Real total of confirmed Purchase Order lines for this product on this stage "
             "(sum of actual line amounts, not qty x a fixed rate - different vendors/dates "
             "can invoice different rates).")
    received_qty = fields.Float(
        compute='_compute_purchase_stats', string='Received Qty',
        help="Quantity actually received into stock (validated incoming stock moves) for this "
             "product on this stage.")
    issued_qty = fields.Float(
        compute='_compute_purchase_stats', string='Issued Qty',
        help="Quantity actually issued to site (validated Material Issue internal transfers).")
    balance_qty = fields.Float(
        compute='_compute_purchase_stats', string='Balance in Store',
        help="Received Qty minus Issued Qty - what is still available in the store to issue.")
    issued_amount = fields.Monetary(
        compute='_compute_purchase_stats', string='Issued Amount', currency_field='currency_id',
        help="Real cumulative value of material issued to site so far - typed in by the "
             "storekeeper on each Material Issue, since the rate actually paid can differ from "
             "the BOQ's original estimate.")
    actual_amount = fields.Monetary(
        compute='_compute_purchase_stats', string='Amount', currency_field='currency_id',
        help="The real cost recognized so far for this line: for materials, the cumulative "
             "amount actually issued to site; for labour/services (no physical issue step), "
             "the purchased/billed amount. Starts at 0 - this is not a qty x rate estimate.")
    fulfillment_status = fields.Selection([
        ('pending', 'Pending'),
        ('partial', 'Partially Received'),
        ('received', 'Fully Received'),
        ('issued', 'Fully Issued'),
    ], compute='_compute_purchase_stats', string='Fulfillment',
        help="The real physical outcome of the request (nothing yet / partly in / fully in / "
             "fully issued to site), independent of the request/procurement workflow state above - "
             "a line can sit at 'In Procurement' forever even after it is fully received, since "
             "that field only tracks whether it went through the request+RFQ workflow.")

    @api.depends('stage_id.project_id')
    def _compute_project_id(self):
        for line in self:
            line.project_id = line.stage_id.project_id if line.stage_id else False

    @api.depends('stage_id.currency_id', 'lead_id.company_currency')
    def _compute_currency_id(self):
        for line in self:
            if line.stage_id:
                line.currency_id = line.stage_id.currency_id
            elif line.lead_id:
                line.currency_id = line.lead_id.company_currency
            else:
                line.currency_id = line.env.company.currency_id

    @api.depends('quantity', 'rate')
    def _compute_amount(self):
        for line in self:
            line.amount = line.quantity * line.rate

    @api.onchange('product_id')
    def _onchange_product_id(self):
        for line in self:
            if line.product_id:
                line.rate = line.product_id.standard_price or line.product_id.list_price

    def _compute_purchase_stats(self):
        for line in self:
            if not line.stage_id:
                line.purchased_qty = line.purchased_amount = 0.0
                line.received_qty = line.issued_qty = line.balance_qty = 0.0
                line.issued_amount = line.actual_amount = 0.0
                line.fulfillment_status = 'pending'
                continue
            po_lines = self.env['purchase.order.line'].search([
                ('order_id.stage_id', '=', line.stage_id.id),
                ('order_id.state', 'in', ('purchase', 'done')),
                ('product_id', '=', line.product_id.id),
            ])
            line.purchased_qty = sum(po_lines.mapped('product_qty'))
            line.purchased_amount = sum(po_lines.mapped('price_total'))
            moves = self.env['stock.move'].search([
                ('picking_id.stage_id', '=', line.stage_id.id),
                ('product_id', '=', line.product_id.id),
                ('state', '=', 'done'),
            ])
            issue_moves = moves.filtered(lambda m: m.picking_id.is_material_issue)
            line.received_qty = sum(moves.filtered(lambda m: not m.picking_id.is_material_issue).mapped('quantity'))
            line.issued_qty = sum(issue_moves.mapped('quantity'))
            line.balance_qty = line.received_qty - line.issued_qty
            line.issued_amount = sum(issue_moves.mapped('issue_amount'))
            line.actual_amount = (
                line.purchased_amount if line.product_id.type == 'service' else line.issued_amount)
            if line.product_id.type == 'service':
                # Services have no physical stock movement - they're "received" the moment
                # they're purchased, since delivery and consumption happen at the same time.
                line.fulfillment_status = 'received' if line.quantity and line.purchased_qty >= line.quantity else 'pending'
            elif not line.quantity:
                line.fulfillment_status = 'pending'
            elif line.issued_qty >= line.quantity:
                line.fulfillment_status = 'issued'
            elif line.received_qty >= line.quantity:
                line.fulfillment_status = 'received'
            elif line.received_qty > 0:
                line.fulfillment_status = 'partial'
            else:
                line.fulfillment_status = 'pending'

    def action_raise_request(self):
        for line in self:
            if not line.stage_id:
                raise UserError(_("Only execution BOQ lines on a stage can raise a material request."))
            if line.quantity <= 0:
                raise UserError(_("Enter a quantity before raising the request."))
            if line.state == 'draft':
                line.state = 'requested'

    def action_create_requisition(self):
        self.ensure_one()
        if not self.stage_id:
            raise UserError(_("Only execution BOQ lines on a stage can be sent to procurement."))
        return self.stage_id.action_create_requisition()
