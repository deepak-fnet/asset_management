# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ConstructionVariationOrder(models.Model):
    _name = 'cm.variation.order'
    _description = 'Construction Variation Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(
        string='VO No.', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    sale_order_id = fields.Many2one(
        'sale.order', string='Contract (Sale Order)', required=True, ondelete='cascade', index=True,
        domain="[('state', '=', 'sale')]",
        help="Only a confirmed contract can receive a Variation Order - it adds new scope on top "
             "of what was already agreed, it does not edit the original lines.")
    master_project_id = fields.Many2one(
        related='sale_order_id.master_project_id', store=True, string='Master Project')
    partner_id = fields.Many2one(related='sale_order_id.partner_id', store=True, string='Client')
    currency_id = fields.Many2one(related='sale_order_id.currency_id')
    date = fields.Date(required=True, default=fields.Date.context_today)
    reason = fields.Text(
        required=True, help="Why this scope change is needed - client request, site condition, "
                             "design change, etc. Kept on record instead of silently editing the "
                             "original contract.")
    line_ids = fields.One2many('cm.variation.order.line', 'variation_order_id', string='New Scope Lines')
    amount_total = fields.Monetary(compute='_compute_amount_total', store=True, currency_field='currency_id')
    new_contract_value = fields.Monetary(
        compute='_compute_new_contract_value', currency_field='currency_id',
        help="While still Draft: a preview of what the contract value would become if approved "
             "(current contract value plus this VO's total). Once Approved, the VO's own lines "
             "are already part of the contract, so this simply mirrors the contract's own total.")
    subproject_count = fields.Integer(compute='_compute_subproject_count')

    state = fields.Selection([
        ('draft', 'Draft'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], default='draft', tracking=True, copy=False)
    prepared_by = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    approved_by = fields.Many2one('res.users', readonly=True, copy=False)
    approved_date = fields.Datetime(readonly=True, copy=False)
    rejection_reason = fields.Text(help="Why this was rejected - required when rejecting.")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('cm.variation.order') or _('New')
        return super().create(vals_list)

    @api.depends('line_ids.amount')
    def _compute_amount_total(self):
        for vo in self:
            vo.amount_total = sum(vo.line_ids.mapped('amount'))

    @api.depends('sale_order_id.amount_total', 'amount_total', 'state')
    def _compute_new_contract_value(self):
        for vo in self:
            order_total = vo.sale_order_id.amount_total or 0.0
            # Once approved, this VO's lines are already part of order_total - adding
            # amount_total again would double-count them. While draft/rejected, order_total
            # doesn't include this VO yet, so the preview adds it on top.
            vo.new_contract_value = order_total if vo.state == 'approved' else order_total + vo.amount_total

    def _compute_subproject_count(self):
        Project = self.env['project.project']
        for vo in self:
            vo.subproject_count = Project.search_count([('variation_order_id', '=', vo.id)])

    def action_approve(self):
        for vo in self:
            if vo.state != 'draft':
                raise UserError(_("Only a draft Variation Order can be approved."))
            if not vo.line_ids:
                raise UserError(_("Add at least one scope line before approving."))
            order = vo.sale_order_id
            if order.state != 'sale':
                raise UserError(_("The contract is no longer confirmed - cannot add scope to it."))

            new_so_lines = self.env['sale.order.line'].create([{
                'order_id': order.id,
                'product_id': line.product_id.id,
                'name': line.name or line.product_id.display_name,
                'product_uom_qty': line.quantity,
                'product_uom_id': line.product_id.uom_id.id,
                'price_unit': line.price_unit,
                'tax_ids': [(6, 0, [])],
                'variation_order_id': vo.id,
            } for line in vo.line_ids])
            for vo_line, so_line in zip(vo.line_ids, new_so_lines):
                vo_line.sale_order_line_id = so_line.id

            order._cm_create_subprojects_from_lines(order.master_project_id)
            if order.master_project_id:
                order.master_project_id.total_contract_value = order.amount_total
                order.master_project_id.message_post(body=_(
                    "Variation Order %(name)s approved: +%(amount)s added to contract scope. Reason: %(reason)s"
                ) % {'name': vo.name, 'amount': vo.amount_total, 'reason': vo.reason})

            vo.write({
                'state': 'approved',
                'approved_by': self.env.user.id,
                'approved_date': fields.Datetime.now(),
            })

    def action_reject(self):
        for vo in self:
            if vo.state != 'draft':
                raise UserError(_("Only a draft Variation Order can be rejected."))
            if not vo.rejection_reason:
                raise UserError(_("Enter a reason in Rejection Reason before rejecting."))
            vo.state = 'rejected'

    def action_reset_to_draft(self):
        for vo in self:
            if vo.state != 'rejected':
                raise UserError(_(
                    "Only a rejected Variation Order can be reset to draft - an approved one has "
                    "already created real contract lines and sub-projects."))
            vo.state = 'draft'

    def action_view_subprojects(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sub-Projects'),
            'res_model': 'project.project',
            'view_mode': 'list,form',
            'domain': [('variation_order_id', '=', self.id)],
        }


class ConstructionVariationOrderLine(models.Model):
    _name = 'cm.variation.order.line'
    _description = 'Construction Variation Order Line'

    variation_order_id = fields.Many2one(
        'cm.variation.order', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', required=True, string='Item / Scope')
    name = fields.Char(string='Description')
    quantity = fields.Float(default=1.0)
    uom_id = fields.Many2one(related='product_id.uom_id', string='UoM', readonly=True)
    price_unit = fields.Float(string='Price')
    currency_id = fields.Many2one(related='variation_order_id.currency_id')
    amount = fields.Monetary(compute='_compute_amount', store=True, currency_field='currency_id')
    sale_order_line_id = fields.Many2one(
        'sale.order.line', readonly=True, copy=False,
        help="The Sale Order line this became once the Variation Order was approved.")

    @api.depends('quantity', 'price_unit')
    def _compute_amount(self):
        for line in self:
            line.amount = line.quantity * line.price_unit

    @api.onchange('product_id')
    def _onchange_product_id(self):
        for line in self:
            if line.product_id and not line.name:
                line.name = line.product_id.display_name
                line.price_unit = line.product_id.list_price


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    variation_order_id = fields.Many2one(
        'cm.variation.order', readonly=True, copy=False,
        help="Set if this line was added after the contract was confirmed, via a Variation Order, "
             "rather than being part of the original quotation.")
