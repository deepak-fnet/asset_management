from odoo import models, fields

class RepairSparePart(models.Model):
    _name = 'repair.spare.part'
    _description = 'Repair Spare Part Line'

    repair_id = fields.Many2one('repair.management', string='Repair', ondelete='cascade', required=True)
    product_id = fields.Many2one('product.product', string='Product', required=True)
    quantity = fields.Float(string='Qty', default=1.0, required=True)
    description = fields.Char(string='Description')
    cost = fields.Float(string='Cost')
    currency_id = fields.Many2one(
        'res.currency',
        related='repair_id.currency_id',  # or default to company currency
        store=True
    )