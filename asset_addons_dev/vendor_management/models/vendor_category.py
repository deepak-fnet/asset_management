from odoo import models, fields, api


class VendorCategory(models.Model):
    _name = 'vendor.category'
    _description = 'Vendor Category'
    _order = 'name'

    name = fields.Char(
        string="Category Name",
        required=True,
        index=True
    )

    active = fields.Boolean(
        string="Active",
        default=True,
        store=True
    )
    
    # REQUIRED FIELDS FOR DESIGN SYSTEM
    # These must be stored=True and directly on the model
    icon = fields.Char(
        string="Icon", 
        default="fa-tags",
        help="Font Awesome icon class for the category display",
        store=True
    )
    
    color = fields.Integer(
        string="Color Index",
        help="Color index for kanban and tags",
        store=True
    )

    icon_display = fields.Html(compute="_compute_icon_display", sanitize=False)

    @api.depends('icon')
    def _compute_icon_display(self):
        for rec in self:
            icon_class = rec.icon or 'fa-tags'
            rec.icon_display = f'<i class="fa {icon_class}" title="Category Icon"></i>'

    vendor_count = fields.Integer(
        string="Vendor Count",
        compute="_compute_vendor_count",
        store=False
    )

    def _compute_vendor_count(self):
        for rec in self:
            rec.vendor_count = self.env['res.partner'].search_count([
                ('vendor_category_ids', 'in', rec.id),
                ('is_vendor', '=', True)
            ])

    # New One2many relationship to intermediate model
    mandatory_document_line_ids = fields.One2many(
        comodel_name='vendor.category.document.line',
        inverse_name='category_id',
        string="Mandatory Documents Configuration",
        help="Configure document requirements and expiry settings per document type"
    )

    # Computed Many2many for backward compatibility
    mandatory_doc_type_ids = fields.Many2many(
        comodel_name='vendor.document.type',
        compute='_compute_mandatory_doc_type_ids',
        string="Mandatory Document Types",
        store=False,
        help="List of mandatory document types (computed from configurations)"
    )

    @api.depends('mandatory_document_line_ids.document_type_id', 'mandatory_document_line_ids.is_mandatory')
    def _compute_mandatory_doc_type_ids(self):
        """Compute mandatory document types from configurations where is_mandatory=True."""
        for rec in self:
            rec.mandatory_doc_type_ids = rec.mandatory_document_line_ids.filtered(
                lambda l: l.is_mandatory
            ).mapped('document_type_id')

    custom_rules = fields.Text(string="Custom Rules")

    _sql_constraints = [
        (
            'vendor_category_name_unique',
            'unique(name)',
            'Vendor Category name must be unique.'
        )
    ]
