from odoo import models, fields, api

class RepairSparePart(models.Model):
    _name = 'repair.spare.part'
    _description = 'Repair Spare Part Line'

    repair_id = fields.Many2one('repair.management', string='Repair', ondelete='cascade', required=True)
    # No domain restriction on purpose: a labour/service-type product is a
    # valid line here too, alongside storable spare parts (is_spare=True) -
    # that is what lets an Internal Team repair's cost cover both parts AND
    # the work itself, see repair.management._sync_cost_from_spares().
    product_id = fields.Many2one('product.product', string='Product', required=True)
    quantity = fields.Float(string='Qty', default=1.0, required=True)
    description = fields.Char(string='Description')
    cost = fields.Float(string='Cost')
    currency_id = fields.Many2one(
        'res.currency',
        related='repair_id.currency_id',  # or default to company currency
        store=True
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.repair_id._sync_cost_from_spares()
        return records

    def write(self, vals):
        result = super().write(vals)
        if 'cost' in vals or 'quantity' in vals:
            self.repair_id._sync_cost_from_spares()
        return result

    def unlink(self):
        repairs = self.repair_id
        result = super().unlink()
        repairs._sync_cost_from_spares()
        return result