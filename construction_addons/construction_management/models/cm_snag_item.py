# -*- coding: utf-8 -*-
from odoo import Command, api, fields, models, _
from odoo.exceptions import UserError


class ConstructionSnagItem(models.Model):
    _name = 'cm.snag.item'
    _description = 'Snag Item / Defect (Post-Handover)'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'reported_date desc, id desc'

    name = fields.Char(
        string='Snag No.', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    project_id = fields.Many2one(
        'project.project', string='Sub-Project', required=True, ondelete='cascade', index=True)
    master_project_id = fields.Many2one(related='project_id.master_project_id', store=True)
    partner_id = fields.Many2one(related='project_id.partner_id', store=True, string='Client')
    stage_id = fields.Many2one(
        'cm.stage', string='Related Stage / Area', domain="[('project_id', '=', project_id)]",
        help="Reference only, for reporting - the original stage is never reopened. All "
             "rectification cost for this defect is tracked here, separately.")
    currency_id = fields.Many2one(related='project_id.currency_id')

    description = fields.Text(required=True)
    severity = fields.Selection([
        ('minor', 'Minor'),
        ('major', 'Major'),
        ('critical', 'Critical'),
    ], default='minor', required=True, tracking=True)
    reported_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    reported_by = fields.Many2one('res.users', default=lambda self: self.env.user)
    assigned_to = fields.Many2one(
        'res.partner', string='Assigned To (Contractor/Team)',
        help="Who is responsible for fixing this - the subcontractor whose work is defective, "
             "or an internal team.")
    cost_borne_by = fields.Selection([
        ('company', 'Company (Absorbed)'),
        ('contractor', 'Contractor (Deduct from Retention/Payment)'),
        ('client', 'Client (Out of Warranty / Not Covered)'),
    ], default='company', required=True, tracking=True,
        help="Who ultimately pays for the rectification. 'Contractor' is informational for now - "
             "actually deducting this from a subcontractor's retention/payment needs Subcontractor "
             "Billing, which is not built yet.")

    warranty_expiry_date = fields.Date(
        compute='_compute_dlp_status', store=True,
        help="This sub-project's Defect Liability Period end date, from its Handover Certificate.")
    is_within_dlp = fields.Boolean(
        string='Within Warranty (DLP)', compute='_compute_dlp_status', store=True,
        help="Whether this snag was reported before the Defect Liability Period expired. If not, "
             "it is likely outside the warranty and 'Cost Borne By' should probably be Client.")

    state = fields.Selection([
        ('open', 'Open'),
        ('in_progress', 'In Progress'),
        ('fixed', 'Fixed - Pending Client Verification'),
        ('verified', 'Client Verified'),
    ], default='open', tracking=True, copy=False)
    fixed_date = fields.Date(readonly=True, copy=False)
    verified_date = fields.Date(readonly=True, copy=False)
    client_remarks = fields.Text(help="Client's feedback on the fix - required when rejecting it.")

    line_ids = fields.One2many('cm.snag.item.line', 'snag_id', string='Rectification Cost')
    rectification_cost_total = fields.Monetary(compute='_compute_rectification_cost_total', store=True)
    picking_ids = fields.One2many('stock.picking', 'snag_id', string='Material Issued')
    picking_count = fields.Integer(compute='_compute_picking_count')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('cm.snag.item') or _('New')
        return super().create(vals_list)

    @api.depends('line_ids.amount')
    def _compute_rectification_cost_total(self):
        for snag in self:
            snag.rectification_cost_total = sum(snag.line_ids.mapped('amount'))

    @api.depends('project_id.handover_certificate_ids.warranty_expiry_date', 'reported_date')
    def _compute_dlp_status(self):
        for snag in self:
            cert = snag.project_id.handover_certificate_ids[:1]
            snag.warranty_expiry_date = cert.warranty_expiry_date if cert else False
            snag.is_within_dlp = bool(
                snag.warranty_expiry_date and snag.reported_date
                and snag.reported_date <= snag.warranty_expiry_date)

    def _compute_picking_count(self):
        for snag in self:
            snag.picking_count = len(snag.picking_ids)

    def action_start(self):
        for snag in self:
            if snag.state != 'open':
                raise UserError(_("Only an open snag can be started."))
            snag.state = 'in_progress'

    def action_mark_fixed(self):
        for snag in self:
            if snag.state != 'in_progress':
                raise UserError(_("Only a snag that is in progress can be marked fixed."))
            snag.write({'state': 'fixed', 'fixed_date': fields.Date.context_today(snag)})

    def action_client_verify(self):
        for snag in self:
            if snag.state != 'fixed':
                raise UserError(_("Only a fixed snag can be client-verified."))
            snag.write({'state': 'verified', 'verified_date': fields.Date.context_today(snag)})

    def action_client_reject(self):
        for snag in self:
            if snag.state != 'fixed':
                raise UserError(_("Only a fixed snag can be rejected back for rework."))
            if not snag.client_remarks:
                raise UserError(_("Enter the client's feedback in Client Remarks before rejecting the fix."))
            snag.write({'state': 'in_progress', 'fixed_date': False})

    def action_issue_rectification_material(self):
        self.ensure_one()
        lines = self.line_ids.filtered(lambda l: l.product_id.type != 'service' and l.quantity > 0)
        if not lines:
            raise UserError(_("Add material lines with a quantity before issuing."))
        company = self.project_id.company_id or self.env.company
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
        if not warehouse:
            raise UserError(_("No warehouse configured for company %s.") % company.name)
        # The site-consumption location is a shared, warehouse-level location (not tied to any
        # one stage) - cm.stage._get_site_consumption_location() doesn't actually use any stage
        # field, so it's reused here directly rather than duplicating the lookup.
        site_location = self.env['cm.stage']._get_site_consumption_location(warehouse)

        picking = self.env['stock.picking'].create({
            'picking_type_id': warehouse.int_type_id.id,
            'partner_id': self.project_id.partner_id.id,
            'origin': self.name,
            'project_id': self.project_id.id,
            'is_snag_rectification': True,
            'snag_id': self.id,
            'move_ids': [Command.create({
                'product_id': line.product_id.id,
                'product_uom_qty': line.quantity,
                'product_uom': line.product_id.uom_id.id,
            }) for line in lines],
        })
        picking.location_id = warehouse.lot_stock_id.id
        picking.location_dest_id = site_location.id
        picking.move_ids.write({
            'location_id': warehouse.lot_stock_id.id,
            'location_dest_id': site_location.id,
        })
        picking.action_confirm()
        picking.action_assign()
        for move in picking.move_ids:
            line = lines.filtered(lambda l: l.product_id == move.product_id)[:1]
            move.quantity = move.product_uom_qty
            move.picked = True
            move.issue_amount = move.quantity * (line.rate if line else 0.0)
        picking.button_validate()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Material Issue'),
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': picking.id,
        }

    def action_view_pickings(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Material Issued'),
            'res_model': 'stock.picking',
            'view_mode': 'list,form',
            'domain': [('snag_id', '=', self.id)],
        }


class ConstructionSnagItemLine(models.Model):
    _name = 'cm.snag.item.line'
    _description = 'Snag Item Rectification Cost Line'

    snag_id = fields.Many2one('cm.snag.item', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', required=True, string='Item / Labour')
    uom_id = fields.Many2one(related='product_id.uom_id', string='UoM', readonly=True)
    quantity = fields.Float(default=1.0)
    rate = fields.Float()
    currency_id = fields.Many2one(related='snag_id.currency_id')
    amount = fields.Monetary(compute='_compute_amount', store=True, currency_field='currency_id')

    @api.depends('quantity', 'rate')
    def _compute_amount(self):
        for line in self:
            line.amount = line.quantity * line.rate

    @api.onchange('product_id')
    def _onchange_product_id(self):
        for line in self:
            if line.product_id:
                line.rate = line.product_id.standard_price or line.product_id.list_price
