# -*- coding: utf-8 -*-
from odoo import Command, fields, models, _
from odoo.exceptions import UserError


class ConstructionMaterialReturnWizard(models.TransientModel):
    _name = 'cm.material.return.wizard'
    _description = 'Return Excess Material'

    stage_id = fields.Many2one('cm.stage', required=True)
    line_ids = fields.One2many('cm.material.return.wizard.line', 'wizard_id', string='Items to Return')

    def action_return(self):
        self.ensure_one()
        lines_to_return = self.line_ids.filtered(lambda l: l.quantity > 0)
        if not lines_to_return:
            raise UserError(_("Enter a quantity greater than zero for at least one item."))

        stage = self.stage_id
        company = stage.company_id or self.env.company
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
        if not warehouse:
            raise UserError(_("No warehouse configured for company %s.") % company.name)
        site_location = stage._get_site_consumption_location(warehouse)

        picking = self.env['stock.picking'].create({
            'picking_type_id': warehouse.int_type_id.id,
            'partner_id': stage.project_id.partner_id.id,
            'origin': stage.name,
            'project_id': stage.project_id.id,
            'stage_id': stage.id,
            'is_material_return': True,
            'move_ids': [Command.create({
                'product_id': line.boq_line_id.product_id.id,
                'product_uom_qty': line.quantity,
                'product_uom': line.boq_line_id.product_id.uom_id.id,
            }) for line in lines_to_return],
        })
        # Reverse of Quick Material Issue: site -> warehouse.
        picking.location_id = site_location.id
        picking.location_dest_id = warehouse.lot_stock_id.id
        picking.move_ids.write({
            'location_id': site_location.id,
            'location_dest_id': warehouse.lot_stock_id.id,
        })
        picking.action_confirm()
        picking.action_assign()
        for move in picking.move_ids:
            line = lines_to_return.filtered(lambda l: l.boq_line_id.product_id == move.product_id)[:1]
            move.quantity = move.product_uom_qty
            move.picked = True
            boq_line = line.boq_line_id
            avg_rate = (boq_line.issued_amount / boq_line.issued_qty) if boq_line.issued_qty else boq_line.rate
            move.issue_amount = move.quantity * avg_rate
        picking.button_validate()

        return {
            'type': 'ir.actions.act_window',
            'name': _('Material Return'),
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': picking.id,
        }


class ConstructionMaterialReturnWizardLine(models.TransientModel):
    _name = 'cm.material.return.wizard.line'
    _description = 'Return Excess Material Line'

    wizard_id = fields.Many2one('cm.material.return.wizard', required=True, ondelete='cascade')
    boq_line_id = fields.Many2one('cm.boq.line', required=True)
    product_id = fields.Many2one(related='boq_line_id.product_id', readonly=True)
    uom_id = fields.Many2one(related='boq_line_id.uom_id', readonly=True)
    returnable_qty = fields.Float(
        related='boq_line_id.returnable_qty', string='Suggested (Issued - Certified Used)', readonly=True)
    quantity = fields.Float(string='Return Now')
