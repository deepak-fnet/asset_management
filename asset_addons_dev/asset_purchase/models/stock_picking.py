# -*- coding: utf-8 -*-

from odoo import _, fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    asset_count = fields.Integer(string="Assets", compute='_compute_asset_count')
    has_missing_assets = fields.Boolean(compute='_compute_asset_count')

    def _compute_asset_count(self):
        # sudo: just a count shown on the receipt - the stores/purchase user
        # opening it should not need Asset Management read rights for that.
        Asset = self.env['asset.asset'].sudo()
        grouped = Asset._read_group(
            [('picking_id', 'in', self.ids)], ['picking_id'], ['__count'])
        counts = {picking.id: count for picking, count in grouped}
        moves_with_assets = set(Asset.search(
            [('stock_move_id', 'in', self.move_ids.ids)]).stock_move_id.ids)
        for picking in self:
            picking.asset_count = counts.get(picking.id, 0)
            picking.has_missing_assets = (
                picking.state == 'done'
                and picking.picking_type_code == 'incoming'
                and any(m.is_fixed_asset and m.state == 'done' and m.id not in moves_with_assets
                        for m in picking.move_ids))

    def action_create_fixed_assets(self):
        """Retry asset creation for a validated receipt whose automatic
        creation failed (the reason is posted in the chatter). Safe to press
        more than once: moves that already have assets are skipped."""
        self.ensure_one()
        assets = self.move_ids.sudo()._create_fixed_assets()
        if not assets:
            # A notification, not a UserError: raising would roll back the
            # chatter note _create_fixed_assets just posted explaining why.
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'type': 'warning',
                    'message': _("No assets were created - see the chatter for the reason."),
                    'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
                },
            }
        return self.action_view_assets()

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
