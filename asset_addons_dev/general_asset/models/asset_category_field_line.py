# -*- coding: utf-8 -*-
"""Per-category, admin-configurable general-asset form layout.

view_asset_general_form used to hardcode a fixed Classification/Placement
block that every category shared, regardless of whether a given category
actually needed it_asset_id, cctv_asset_id, iot_device_id, etc. This lets
an admin pick, per category, which asset.asset fields appear in each of
the two columns the form's dynamic-fields widget renders - no XML/code
change needed to add or rearrange fields for a category.

Two separate concrete models (Left/Right) sharing one mixin, rather than
one model with a `side` selection field filtered by domain per column.
The single-model-plus-domain version was tried first and had a real bug:
a row added through the Right column list could end up saved with
side='left' - column identity has to be which TABLE a row lives in, not a
field value a shared editable-list widget has to remember to set right on
every new row.
"""

from odoo import models, fields, api


class AssetCategoryFieldLineMixin(models.AbstractModel):
    _name = "asset.category.field.line.mixin"
    _description = "General Asset Form Field (shared fields)"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    # NOT named display_name - that's a reserved/magic field name every
    # Odoo model auto-computes (from _rec_name / name_get), which silently
    # shadows a same-named custom field instead of raising an error.
    label = fields.Char(
        string="Label", required=True,
        help="Shown as the field's label on the form.")
    field_id = fields.Many2one(
        "ir.model.fields", string="Field", required=True,
        domain=[("model", "=", "asset.asset")], ondelete="cascade",
        help="The asset.asset field to render here.")
    field_name = fields.Char(
        related="field_id.name", store=True,
        help="Technical name, stored for the form widget to read without "
             "an extra hop through ir.model.fields.")
    readonly_condition = fields.Char(
        string="Readonly If",
        help="Python boolean expression, evaluated against the record - "
             "e.g. state not in ('draft',). Same syntax as a view's "
             "readonly=\"...\" attribute. Leave blank for never readonly.")
    invisible_condition = fields.Char(
        string="Invisible If",
        help="Python boolean expression, evaluated against the record - "
             "e.g. not is_iot_device. Same syntax as a view's "
             "invisible=\"...\" attribute. Leave blank for always visible.")

    @api.onchange("field_id")
    def _onchange_field_id(self):
        if self.field_id and not self.label:
            self.label = self.field_id.field_description


class AssetCategoryFieldLineLeft(models.Model):
    _name = "asset.category.field.line.left"
    _inherit = "asset.category.field.line.mixin"
    _description = "General Asset Form Field - Left Column"

    category_id = fields.Many2one(
        "asset.category", required=True, ondelete="cascade", index=True)

    # Picking the same field twice in one column isn't just redundant - the
    # dynamic-fields widget keys its t-foreach by field_name, and OWL hard
    # crashes ("Got duplicate key in t-foreach") the instant two rows share
    # one. Block it at save instead of at render time.
    _field_uniq = models.Constraint(
        "unique (category_id, field_id)",
        "This field is already in this column for this category.")


class AssetCategoryFieldLineRight(models.Model):
    _name = "asset.category.field.line.right"
    _inherit = "asset.category.field.line.mixin"
    _description = "General Asset Form Field - Right Column"

    category_id = fields.Many2one(
        "asset.category", required=True, ondelete="cascade", index=True)

    _field_uniq = models.Constraint(
        "unique (category_id, field_id)",
        "This field is already in this column for this category.")
