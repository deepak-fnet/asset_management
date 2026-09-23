# -*- coding: utf-8 -*-
from odoo import _, fields, models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    stage_id = fields.Many2one(
        'cm.stage', string='Construction Stage', domain="[('project_id', '=', project_id)]")
    is_material_issue = fields.Boolean(
        string='Material Issue', copy=False,
        help="Set on the internal transfer created by a stage's 'Create Material Issue' action, "
             "to distinguish site-issue moves from incoming purchase receipts on the same product.")
    is_material_return = fields.Boolean(
        string='Material Return', copy=False,
        help="Set on the internal transfer created by a stage's 'Return Excess Material' action, "
             "for unused material sent back from site to the warehouse.")
    is_snag_rectification = fields.Boolean(
        string='Snag Rectification', copy=False,
        help="Set on the internal transfer created by a Snag Item's 'Issue Rectification "
             "Material' action - post-handover defect-fix material, kept separate from the "
             "original stage's own Store Ledger.")
    snag_id = fields.Many2one(
        'cm.snag.item', string='Snag Item', copy=False, index=True,
        help="The Snag Item this material was issued to fix, if any.")

    def button_validate(self):
        res = super().button_validate()
        for picking in self.filtered(lambda p: p.is_material_issue and p.state == 'done'):
            picking._create_draft_measurements()
        return res

    def _create_draft_measurements(self):
        self.ensure_one()
        Measurement = self.env['cm.measurement']
        BoqLine = self.env['cm.boq.line']
        for move in self.move_ids.filtered(lambda m: m.state == 'done'):
            boq_line = BoqLine.search([
                ('stage_id', '=', self.stage_id.id), ('product_id', '=', move.product_id.id),
            ], limit=1)
            if not boq_line or not move.quantity:
                continue
            measurement = Measurement.create({
                'boq_line_id': boq_line.id,
                'qty_this_measurement': move.quantity,
            })
            measurement.message_post(
                body=_("Draft measurement auto-created from Material Issue %s - review and certify or reject.")
                     % self.name)
