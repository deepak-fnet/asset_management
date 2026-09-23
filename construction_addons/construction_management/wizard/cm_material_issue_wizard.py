# -*- coding: utf-8 -*-
from odoo import Command, api, fields, models, _
from odoo.exceptions import UserError


class ConstructionMaterialIssueWizard(models.TransientModel):
    _name = 'cm.material.issue.wizard'
    _description = 'Quick Material Issue'

    stage_id = fields.Many2one('cm.stage', required=True)
    line_ids = fields.One2many('cm.material.issue.wizard.line', 'wizard_id', string='Items to Issue')

    def action_issue(self):
        self.ensure_one()
        lines_to_issue = self.line_ids.filtered(lambda l: l.quantity > 0)
        if not lines_to_issue:
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
            'is_material_issue': True,
            'move_ids': [Command.create({
                'product_id': line.boq_line_id.product_id.id,
                'product_uom_qty': line.quantity,
                'product_uom': line.boq_line_id.product_id.uom_id.id,
            }) for line in lines_to_issue],
        })
        # location_id/location_dest_id are precompute fields keyed off picking_type_id's own
        # defaults - set the real destination (the site) explicitly after creation, matching
        # the same pattern used by the stage's own bulk action_create_material_issue.
        picking.location_id = warehouse.lot_stock_id.id
        picking.location_dest_id = site_location.id
        picking.move_ids.write({
            'location_id': warehouse.lot_stock_id.id,
            'location_dest_id': site_location.id,
        })
        picking.action_confirm()
        picking.action_assign()
        for move in picking.move_ids:
            line = lines_to_issue.filtered(lambda l: l.boq_line_id.product_id == move.product_id)[:1]
            move.quantity = move.product_uom_qty
            move.picked = True
            move.issue_amount = move.quantity * (line.boq_line_id.rate if line else 0.0)
        picking.button_validate()

        return {
            'type': 'ir.actions.act_window',
            'name': _('Material Issue'),
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': picking.id,
        }


class ConstructionMaterialIssueWizardLine(models.TransientModel):
    _name = 'cm.material.issue.wizard.line'
    _description = 'Quick Material Issue Line'

    wizard_id = fields.Many2one('cm.material.issue.wizard', required=True, ondelete='cascade')
    boq_line_id = fields.Many2one('cm.boq.line', required=True)
    product_id = fields.Many2one(related='boq_line_id.product_id', readonly=True)
    uom_id = fields.Many2one(related='boq_line_id.uom_id', readonly=True)
    available_qty = fields.Float(
        related='boq_line_id.balance_qty', string='Available (This Stage)', readonly=True,
        help="Balance tracked for this stage's own purchases only. You can still issue more "
             "than this if the product is genuinely available elsewhere in the warehouse - "
             "stock validation will simply refuse it if there truly isn't enough on hand.")
    quantity = fields.Float(string='Issue Now')
