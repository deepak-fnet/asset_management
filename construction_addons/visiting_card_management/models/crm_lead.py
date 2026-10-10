from odoo import fields, models


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    visiting_card_id = fields.Many2one('visiting.card', string='Source Visiting Card', readonly=True, copy=False)

    def action_view_visiting_card(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'visiting.card',
            'view_mode': 'form',
            'res_id': self.visiting_card_id.id,
            'target': 'current',
        }
