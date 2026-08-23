from odoo import models, fields, api


class VendorCategoryDocumentLine(models.Model):
    _name = 'vendor.category.document.line'
    _description = 'Vendor Category Document Configuration'
    _rec_name = 'document_type_id'
    _order = 'category_id, document_type_id'

    category_id = fields.Many2one(
        'vendor.category',
        string="Vendor Category",
        required=True,
        ondelete='cascade',
        index=True
    )
    
    document_type_id = fields.Many2one(
        'vendor.document.type',
        string="Document Type",
        required=True,
        index=True
    )
    
    is_mandatory = fields.Boolean(
        string="Mandatory",
        default=True,
        help="If checked, this document is mandatory for vendors in this category."
    )
    
    requires_expiry = fields.Boolean(
        string="Requires Expiry Date",
        default=True,
        help="If checked, expiry date will be mandatory for this document type in this category."
    )
    
    _sql_constraints = [
        (
            'category_document_unique',
            'unique(category_id, document_type_id)',
            'This document type is already configured for this category.'
        )
    ]

    @api.model
    def _set_default_expiry_configs(self):
        """Set intelligent defaults for common document types."""
        # Find PAN document type
        pan_doc = self.env['vendor.document.type'].search([('code', '=', 'PAN')], limit=1)
        if pan_doc:
            # Set requires_expiry=False for PAN in all categories
            pan_configs = self.search([('document_type_id', '=', pan_doc.id)])
            pan_configs.write({'requires_expiry': False})
        
        # Similarly for other static documents
        static_docs = self.env['vendor.document.type'].search([
            ('code', 'in', ['PAN', 'TAN', 'CIN'])
        ])
        for doc in static_docs:
            configs = self.search([('document_type_id', '=', doc.id)])
            configs.write({'requires_expiry': False})
