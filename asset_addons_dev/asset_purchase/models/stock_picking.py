# -*- coding: utf-8 -*-

from odoo import _, fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    asset_count = fields.Integer(string="Assets", compute='_compute_asset_count')

    def _compute_asset_count(self):
        grouped = self.env['asset.asset']._read_group(
            [('picking_id', 'in', self.ids)], ['picking_id'], ['__count'])
        counts = {picking.id: count for picking, count in grouped}
        for picking in self:
            picking.asset_count = counts.get(picking.id, 0)

    def action_view_assets(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Assets from %s", self.name),
            'res_model': 'asset.asset',
            'view_mode': 'list,form',
            'views': [
                (self.env.ref('general_asset.view_asset_general_list').id, 'list'),
                (self.env.ref('general_asset.view_asset_general_form').id, 'form'),
            ],
            'domain': [('picking_id', '=', self.id)],
            'target': 'current',
        }