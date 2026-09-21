# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ConstructionCosting(models.Model):
    _name = 'cm.costing'
    _description = 'Construction Sale Costing'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(
        string='Costing No.', required=True, copy=False, readonly=True,
        default=lambda self: _('New'))
    lead_id = fields.Many2one('crm.lead', string='Opportunity', required=True, ondelete='cascade', tracking=True)
    partner_id = fields.Many2one(related='lead_id.partner_id', string='Customer', store=True, readonly=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id', string='Currency', readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('done', 'Done'),
    ], default='draft', tracking=True, copy=False)

    line_ids = fields.One2many('cm.boq.line', 'costing_id', string='Costing Lines')
    cost_total = fields.Monetary(string='Cost Total', compute='_compute_totals', store=True)
    margin_total = fields.Monetary(string='Margin Total', compute='_compute_totals', store=True)
    sell_total = fields.Monetary(string='Sell Total', compute='_compute_totals', store=True)
    margin_percent_overall = fields.Float(
        string='Overall Margin (%)', compute='_compute_totals', store=True,
        help="Margin Total as a percentage of Sell Total, across every line.")

    sale_order_id = fields.Many2one('sale.order', string='Quotation', readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('cm.costing') or _('New')
        return super().create(vals_list)

    @api.depends('line_ids.amount', 'line_ids.margin_value', 'line_ids.sell_amount')
    def _compute_totals(self):
        for costing in self:
            costing.cost_total = sum(costing.line_ids.mapped('amount'))
            costing.margin_total = sum(costing.line_ids.mapped('margin_value'))
            costing.sell_total = sum(costing.line_ids.mapped('sell_amount'))
            costing.margin_percent_overall = (
                costing.margin_total / costing.sell_total * 100.0 if costing.sell_total else 0.0)

    def action_confirm(self):
        for costing in self:
            if not costing.line_ids:
                raise UserError(_("Add at least one costing line before confirming."))
            costing.state = 'confirmed'

    def action_reset_to_draft(self):
        for costing in self:
            if costing.sale_order_id:
                raise UserError(_("Cannot reset to draft - a quotation has already been created."))
            costing.state = 'draft'

    def action_create_quotation(self):
        self.ensure_one()
        if self.state != 'confirmed':
            raise UserError(_("Confirm the costing before creating a quotation."))
        if self.sale_order_id:
            raise UserError(_("A quotation already exists for this costing: %s") % self.sale_order_id.name)
        if not self.lead_id.partner_id:
            self.lead_id._handle_partner_assignment(create_missing=True)
        order = self.env['sale.order'].create({
            'partner_id': self.lead_id.partner_id.id,
            'opportunity_id': self.lead_id.id,
            'order_line': [(0, 0, {
                'product_id': line.product_id.id,
                'name': line.product_id.display_name,
                'product_uom_qty': line.quantity,
                'product_uom_id': line.uom_id.id,
                'price_unit': line.sell_rate,
                'tax_ids': [(6, 0, [])],
            }) for line in self.line_ids],
        })
        self.write({'sale_order_id': order.id, 'state': 'done'})
        self.lead_id.sale_order_id = order.id
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
