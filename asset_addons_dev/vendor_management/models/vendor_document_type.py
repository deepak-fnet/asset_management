from odoo import models, fields, api


class VendorDocumentType(models.Model):
    _name = 'vendor.document.type'
    _description = 'Vendor Document Type'
    _rec_name = 'name'

    name = fields.Char(string='Document Name', required=True)
    code = fields.Char(string='Code', help="Auto-generated from name if not provided")
    active = fields.Boolean(default=True)
    validation_regex = fields.Char(string='Validation Regex')
    
    # NOTE: Expiry and mandatory configuration has been moved to 
    # vendor.category.document.line for per-category flexibility.
    
    is_critical = fields.Boolean(string="Critical", default=False, help="If expired, vendor risk level becomes High.")
    category = fields.Selection([
        ('legal', 'Legal'),
        ('financial', 'Financial'),
        ('technical', 'Technical'),
        ('others', 'Others')
    ], string='Category', default='others')
    validity_days = fields.Integer(string='Validity Days', default=0)

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-generate code from name if not provided."""
        for vals in vals_list:
            if not vals.get('code') and vals.get('name'):
                vals['code'] = vals['name'].upper().replace(' ', '_')
        return super().create(vals_list)
