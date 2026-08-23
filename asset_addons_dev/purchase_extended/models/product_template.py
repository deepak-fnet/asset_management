from odoo import models, api

class ProductTemplate(models.Model):
    _inherit = "product.template"

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals['is_storable'] = True
        return super().create(vals_list)