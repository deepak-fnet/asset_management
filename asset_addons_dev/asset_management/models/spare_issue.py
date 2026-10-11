# -*- coding: utf-8 -*-
"""Spare Issue: the store's queue of spare parts requested by repairs.

Repair: the engineer lists the spares needed and clicks Request Spares ->
one Spare Issue record. The store sees every request under Spare Issue,
enters the issued quantity and the unit price, and clicks Issue: stock goes
down (moved to a Repair Consumption location) and the cost lands on the
repair. Anything not in stock can be sent to purchase (Spare Request ->
RFQ/PO) from the same record.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SpareIssue(models.Model):
    _name = 'spare.issue'
    _description = 'Spare Issue'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(default=lambda self: _('New'), readonly=True, copy=False)
    repair_id = fields.Many2one('repair.management', string='Repair Task', required=True,
                                ondelete='cascade', index=True, readonly=True)
    asset_id = fields.Many2one(related='repair_id.asset_id', store=True)
    engineer_id = fields.Many2one(related='repair_id.engineer_id', store=True, string='Engineer')
    requested_by = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    request_date = fields.Datetime(default=fields.Datetime.now, readonly=True)
    issued_by = fields.Many2one('res.users', readonly=True, copy=False)
    issue_date = fields.Datetime(readonly=True, copy=False)
    state = fields.Selection([
        ('requested', 'Requested'),
        ('issued', 'Issued'),
        ('cancel', 'Cancelled'),
    ], default='requested', tracking=True)
    line_ids = fields.One2many('spare.issue.line', 'issue_id', string='Spares')
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id)
    amount_total = fields.Monetary(compute='_compute_amount_total', string='Total')
    picking_id = fields.Many2one('stock.picking', string='Stock Transfer', readonly=True, copy=False)
    spare_request_id = fields.Many2one('spare.request', string='Purchase Request', readonly=True, copy=False)

    @api.depends('line_ids.subtotal')
    def _compute_amount_total(self):
        for rec in self:
            rec.amount_total = sum(rec.line_ids.mapped('subtotal'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('spare.issue') or _('New')
        return super().create(vals_list)

    def _consumption_location(self):
        Location = self.env['stock.location'].sudo()
        loc = Location.search([('name', '=', 'Repair Consumption'), ('usage', '=', 'inventory'),
                               ('company_id', 'in', (False, self.env.company.id))], limit=1)
        if not loc:
            parent = self.env.ref('stock.stock_location_locations_virtual', raise_if_not_found=False)
            loc = Location.create({'name': 'Repair Consumption', 'usage': 'inventory',
                                   'location_id': parent.id if parent else False,
                                   'company_id': self.env.company.id})
        return loc

    def action_issue(self):
        """Take the issued quantities out of stock and cost the repair."""
        self.ensure_one()
        if self.state != 'requested':
            raise UserError(_("Only a requested spare issue can be issued."))
        lines = self.line_ids.filtered(lambda l: l.qty_issued > 0)
        if not lines:
            raise UserError(_("Enter the issued quantity on at least one line."))
        warehouse = self.env['stock.warehouse'].sudo().search(
            [('company_id', '=', self.env.company.id)], limit=1)
        if not warehouse:
            raise UserError(_("No warehouse configured."))
        source = warehouse.lot_stock_id
        short = []
        for line in lines.filtered(lambda l: l.product_id.is_storable):
            on_hand = line.product_id.with_context(location=source.id).qty_available
            if on_hand < line.qty_issued:
                short.append(_("%(p)s: issuing %(q)s, on hand %(h)s",
                               p=line.product_id.display_name, q=line.qty_issued, h=on_hand))
        if short:
            raise UserError(_("Not enough stock:\n%s\n\nUse Request Purchase for the missing parts.")
                            % "\n".join(short))
        storable = lines.filtered(lambda l: l.product_id.is_storable)
        picking = self.env['stock.picking']
        if storable:
            dest = self._consumption_location()
            picking_type = warehouse.int_type_id or warehouse.out_type_id
            picking = picking.sudo().create({
                'picking_type_id': picking_type.id,
                'location_id': source.id,
                'location_dest_id': dest.id,
                'origin': '%s / %s' % (self.name, self.repair_id.name),
            })
            for line in storable:
                self.env['stock.move'].sudo().create({
                    'description_picking': _("Spare for %s", self.repair_id.name),
                    'product_id': line.product_id.id,
                    'product_uom_qty': line.qty_issued,
                    'product_uom': line.product_id.uom_id.id,
                    'picking_id': picking.id,
                    'location_id': source.id,
                    'location_dest_id': dest.id,
                })
            picking.action_confirm()
            picking.action_assign()
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
                move.picked = True
            picking.button_validate()
        # Cost onto the repair.
        for line in lines:
            vals = {'quantity': line.qty_issued, 'cost': line.subtotal, 'issued': True}
            if line.repair_spare_line_id:
                line.repair_spare_line_id.write(vals)
            else:
                line.repair_spare_line_id = self.env['repair.spare.part'].create(dict(
                    vals, repair_id=self.repair_id.id, product_id=line.product_id.id,
                    description=line.description))
        # Lines not issued now (out of stock) go back to the task, so they
        # can be requested again once the purchase arrives.
        (self.line_ids - lines).repair_spare_line_id.write({'issue_line_id': False})
        self.write({'state': 'issued', 'picking_id': picking.id, 'issued_by': self.env.user.id,
                    'issue_date': fields.Datetime.now()})
        self.repair_id.message_post(body=_(
            "Spares issued (%(issue)s): %(lines)s. Cost %(cost)s.",
            issue=self.name,
            lines=", ".join("%s x %s" % (l.product_id.display_name, l.qty_issued) for l in lines),
            cost=self.amount_total))

    def action_request_purchase(self):
        """Raise a Spare Request (-> RFQ/PO) for what the store cannot issue."""
        self.ensure_one()
        lines = []
        for line in self.line_ids:
            missing = line.qty_requested - line.available_qty
            if missing > 0:
                lines.append((0, 0, {
                    'product_id': line.product_id.id,
                    'description': line.description or line.product_id.display_name,
                    'quantity': missing,
                    'unit_price': line.unit_price or line.product_id.standard_price,
                }))
        if not lines:
            raise UserError(_("Everything requested is in stock - issue it instead."))
        request = self.env['spare.request'].create({
            'repair_id': self.repair_id.id,
            'asset_id': self.asset_id.id,
            'reason': _("Spares for %(rep)s (%(issue)s)", rep=self.repair_id.name, issue=self.name),
            'line_ids': lines,
        })
        self.spare_request_id = request
        return {'type': 'ir.actions.act_window', 'res_model': 'spare.request',
                'view_mode': 'form', 'res_id': request.id}

    def action_cancel(self):
        self.filtered(lambda r: r.state == 'requested').write({'state': 'cancel'})
        self.line_ids.repair_spare_line_id.filtered(lambda l: not l.issued).write({'issue_line_id': False})


class SpareIssueLine(models.Model):
    _name = 'spare.issue.line'
    _description = 'Spare Issue Line'

    issue_id = fields.Many2one('spare.issue', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', string='Spare', required=True)
    description = fields.Char()
    qty_requested = fields.Float(string='Requested Qty', default=1.0, readonly=True)
    qty_issued = fields.Float(string='Issue Qty')
    available_qty = fields.Float(related='product_id.qty_available', string='On Hand')
    unit_price = fields.Float(string='Unit Price')
    currency_id = fields.Many2one(related='issue_id.currency_id')
    subtotal = fields.Monetary(compute='_compute_subtotal', string='Cost')
    repair_spare_line_id = fields.Many2one('repair.spare.part', readonly=True)

    @api.depends('qty_issued', 'unit_price')
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.qty_issued * line.unit_price


class RepairSparePart(models.Model):
    _inherit = 'repair.spare.part'

    issued = fields.Boolean(readonly=True, copy=False)
    issue_line_id = fields.Many2one('spare.issue.line', readonly=True, copy=False)


class RepairManagement(models.Model):
    _inherit = 'repair.management'

    spare_issue_ids = fields.One2many('spare.issue', 'repair_id', string='Spare Issues')
    spare_issue_count = fields.Integer(compute='_compute_spare_issue_count')
    standby_out = fields.Boolean(compute='_compute_standby_out')

    def _compute_standby_out(self):
        for rec in self:
            rec.standby_out = bool(rec.id) and bool(self.env['asset.assign.process'].search_count([
                ('repair_ticket_id', '=', rec.id), ('state', '=', 'replaced')]))

    @api.depends('spare_issue_ids')
    def _compute_spare_issue_count(self):
        for rec in self:
            rec.spare_issue_count = len(rec.spare_issue_ids)

    def action_request_spare_issue(self):
        """Send the spares listed on this task to the store as one Spare Issue."""
        self.ensure_one()
        lines = self.spare_part_ids.filtered(lambda l: not l.issued and not l.issue_line_id)
        if not lines:
            raise UserError(_("Add the spare parts needed (not yet requested) first."))
        issue = self.env['spare.issue'].create({
            'repair_id': self.id,
            'line_ids': [(0, 0, {
                'product_id': l.product_id.id,
                'description': l.description,
                'qty_requested': l.quantity,
                'qty_issued': l.quantity,
                'unit_price': l.product_id.standard_price,
                'repair_spare_line_id': l.id,
            }) for l in lines],
        })
        for l, il in zip(lines, issue.line_ids):
            l.issue_line_id = il
        self.message_post(body=_("Spares requested from the store: %s.", issue.name))
        return {'type': 'ir.actions.act_window', 'res_model': 'spare.issue',
                'view_mode': 'form', 'res_id': issue.id}

    def action_view_spare_issues(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _("Spare Issues"), 'res_model': 'spare.issue',
                'view_mode': 'list,form', 'domain': [('repair_id', '=', self.id)],
                'context': {'create': False}}
