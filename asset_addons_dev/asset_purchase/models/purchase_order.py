# -*- coding: utf-8 -*-

from odoo import _, fields, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    asset_count = fields.Integer(
        string="Assets", compute='_compute_asset_count')

    def _compute_asset_count(self):
        grouped = self.env['asset.asset']._read_group(
            [('purchase_order_id', 'in', self.ids)],
            ['purchase_order_id'],
            ['__count'],
        )
        counts = {order.id: count for order, count in grouped}
        for order in self:
            order.asset_count = counts.get(order.id, 0)

    def action_view_assets(self):
        self.ensure_one()
        list_view = self.env.ref('general_asset.view_asset_general_list', raise_if_not_found=False)
        form_view = self.env.ref('general_asset.view_asset_general_form', raise_if_not_found=False)
        action = {
            'type': 'ir.actions.act_window',
            'name': _("Assets from %s", self.name),
            'res_model': 'asset.asset',
            'view_mode': 'list,form',
            'domain': [('purchase_order_id', '=', self.id)],
            'target': 'current',
        }
        if list_view and form_view:
            action['views'] = [(list_view.id, 'list'), (form_view.id, 'form')]
        return action
