from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    visiting_card_ids = fields.One2many('visiting.card', 'partner_id', string='Visiting Cards')
    visiting_card_count = fields.Integer(compute='_compute_visiting_card_count')

    def _compute_visiting_card_count(self):
        counts = self.env['visiting.card']._read_group(
            [('partner_id', 'in', self.ids)], ['partner_id'], ['__count'],
        )
        mapped = {partner.id: count for partner, count in counts}
        for partner in self:
            partner.visiting_card_count = mapped.get(partner.id, 0)

    def action_view_visiting_cards(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Visiting Cards',
            'res_model': 'visiting.card',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
        }
