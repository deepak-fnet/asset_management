# -*- coding: utf-8 -*-

from odoo import models


class AssetAsset(models.Model):
    """Receipt traceability helpers on asset.asset.

    The traceability FIELDS themselves (stock_move_id, picking_id,
    purchase_order_id, product_id, lot_id, receipt_date, ...) live in
    general_asset, not here. They were originally declared in this module
    against asset.addition, but that put them behind this module's
    installation - so general_asset's own views could not reference them
    without being conditional. Declaring them once in general_asset and only
    USING them here keeps that dependency running one way.
    """

    _inherit = "asset.asset"

    def action_open_source_picking(self):
        self.ensure_one()
        if not self.picking_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'res_id': self.picking_id.id,
            'view_mode': 'form',
            'target': 'current',
        }
