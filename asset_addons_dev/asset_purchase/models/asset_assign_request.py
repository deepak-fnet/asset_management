# -*- coding: utf-8 -*-
"""Asset Assign Process: Request Details.

The employee writes what they need in plain words (one line each:
"AC for my room", "office chair"), submits, and their manager approves.
After approval the asset team completes each line - quantity, the product
(fixed-asset products only) and its asset category - and maps real assets
to it through the Map Assets popup:

* Employee: the unit is personal - employee, department and location.
* Common:   the unit belongs to a place / team (AC in a meeting room, a
            fan in a department's room) - at least one of location,
            department or employee.

Lines can be partly mapped (2 ACs requested, 1 in stock): the process is
then Partially Assigned, and the shortfall can be raised as an Asset
Request (purchase) with the same category and product.

Lives in asset_purchase because the product's asset category and the
fixed-asset flag are defined here.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

ASSIGN_TYPES = [('employee', 'Employee'), ('common', 'Common')]


class AssetJoiningRequestLine(models.Model):
    _name = 'asset.joining.request.line'
    _description = 'Asset Assign Request Detail'
    _order = 'joining_id, sequence, id'
    _rec_name = 'description'

    joining_id = fields.Many2one('asset.joining.process', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    description = fields.Char(string="Description", required=True,
                              help="What the employee needs, in their own words.")
    quantity = fields.Integer(string="Qty", default=1)
    product_id = fields.Many2one(
        'product.product', string="Product",
        domain="[('is_asset', '=', True)]")
    category_id = fields.Many2one(
        'asset.category', string="Category",
        compute='_compute_category_id', store=True, readonly=False)
    allocation_ids = fields.One2many('asset.joining.allocation', 'request_line_id', string="Mappings")
    assigned_qty = fields.Integer(string="Assigned", compute='_compute_assign_status', store=True)
    pending_qty = fields.Integer(string="Pending", compute='_compute_assign_status', store=True)
    assign_status = fields.Selection([
        ('pending', 'Pending'),
        ('partial', 'Partially Assigned'),
        ('assigned', 'Assigned'),
    ], string="Status", compute='_compute_assign_status', store=True)
    asset_request_line_id = fields.Many2one('asset.request.line', string="Purchase Request Line",
                                            copy=False, readonly=True)

    @api.depends('product_id')
    def _compute_category_id(self):
        for line in self:
            if line.product_id:
                line.category_id = line.product_id._get_asset_category()

    @api.depends('quantity', 'allocation_ids.asset_ids')
    def _compute_assign_status(self):
        for line in self:
            line.assigned_qty = len(line.allocation_ids.asset_ids)
            line.pending_qty = max(line.quantity - line.assigned_qty, 0)
            if not line.assigned_qty:
                line.assign_status = 'pending'
            elif line.pending_qty:
                line.assign_status = 'partial'
            else:
                line.assign_status = 'assigned'

    @api.constrains('quantity')
    def _check_quantity(self):
        for line in self:
            if line.quantity < 0:
                raise ValidationError(_("Quantity cannot be negative."))

    def action_open_map_wizard(self):
        self.ensure_one()
        if self.joining_id.state not in ('approved', 'partial'):
            raise UserError(_("Assets can be mapped once the manager has approved the request."))
        if not self.product_id or not self.category_id or self.quantity <= 0:
            raise UserError(_("Fill in the quantity, product and category of this line first."))
        if not self.pending_qty:
            raise UserError(_("Everything requested on this line is already assigned."))
        return {
            'type': 'ir.actions.act_window',
            'name': _("Map Assets - %s", self.description),
            'res_model': 'asset.map.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_request_line_id': self.id},
        }


class AssetJoiningAllocation(models.Model):
    """One mapping done through the popup: which units went where."""
    _name = 'asset.joining.allocation'
    _description = 'Asset Assign Mapping'
    _order = 'id'

    request_line_id = fields.Many2one('asset.joining.request.line', required=True, ondelete='cascade', index=True)
    joining_id = fields.Many2one(related='request_line_id.joining_id', store=True)
    assign_type = fields.Selection(ASSIGN_TYPES, string="Type", required=True)
    asset_ids = fields.Many2many('asset.asset', string="Assets")
    quantity = fields.Integer(string="Qty")
    location_id = fields.Many2one('asset.location', string="Location")
    employee_id = fields.Many2one('hr.employee', string="Employee")
    department_id = fields.Many2one('hr.department', string="Department")
    assigned_date = fields.Date(string="Assigned On", default=fields.Date.context_today)


class AssetJoiningProcess(models.Model):
    _inherit = 'asset.joining.process'

    request_line_ids = fields.One2many('asset.joining.request.line', 'joining_id', string="Request Details")
    allocation_ids = fields.One2many('asset.joining.allocation', 'joining_id', string="Mappings")
    has_shortfall = fields.Boolean(compute='_compute_has_shortfall')

    @api.depends('request_line_ids.pending_qty', 'request_line_ids.asset_request_line_id')
    def _compute_has_shortfall(self):
        for rec in self:
            rec.has_shortfall = any(
                l.pending_qty and not l.asset_request_line_id for l in rec.request_line_ids)

    def _check_can_submit(self):
        if self.request_line_ids:
            return
        if not self.requirement_ids:
            raise UserError(_("Add at least one line under Request Details."))
        return super()._check_can_submit()

    def _update_assign_state(self):
        for rec in self:
            lines = rec.request_line_ids
            if not lines or rec.state not in ('approved', 'partial'):
                continue
            if all(l.assign_status == 'assigned' for l in lines):
                rec.state = 'done'
            elif any(l.assigned_qty for l in lines):
                rec.state = 'partial'
            rec.assigned_asset_ids = [(6, 0, rec.allocation_ids.asset_ids.ids)]

    def action_raise_purchase_request(self):
        """Raise one Asset Request (purchase) for every line still short."""
        self.ensure_one()
        if self.state not in ('approved', 'partial'):
            raise UserError(_("Only an approved request can raise a purchase request."))
        short = self.request_line_ids.filtered(lambda l: l.pending_qty and not l.asset_request_line_id)
        if not short:
            raise UserError(_("Nothing is short - every line is assigned or already requested."))
        missing = short.filtered(lambda l: not l.category_id)
        if missing:
            raise UserError(_("Set the product/category on: %s", ", ".join(missing.mapped('description'))))
        request = self.env['asset.request'].create({
            'notes': _("Raised from %(proc)s for %(emp)s.", proc=self.name, emp=self.employee_id.name),
            'line_ids': [(0, 0, {
                'asset_category_id': l.category_id.id,
                'product_id': l.product_id.id,
                'description': l.description,
                'quantity': l.pending_qty,
            }) for l in short],
        })
        for line, req_line in zip(short, request.line_ids):
            line.asset_request_line_id = req_line
        if not self.asset_request_id:
            self.asset_request_id = request
        self.message_post(body=_("Purchase request %s raised for the items not in stock.", request.name))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'asset.request',
            'view_mode': 'form',
            'res_id': request.id,
        }
