from odoo import models, fields


class PurchaseTermsCondition(models.Model):
    _name = "purchase.terms.condition"
    _description = "Purchase Terms & Conditions"
    _order = "name"

    name = fields.Char(string="Name", required=True)
    value_ids = fields.One2many(
        "purchase.terms.condition.value", "term_id", string="Values",
    )


class PurchaseTermsConditionValue(models.Model):
    _name = "purchase.terms.condition.value"
    _description = "Purchase Terms & Conditions Value"
    _order = "id"
    # No "name" field on this model - without this, Odoo has nothing to
    # build display_name from and falls back to "purchase.terms.condition
    # .value,<id>" everywhere this record is shown as text (m2o widgets,
    # many2many_tags, breadcrumbs...).
    _rec_name = "value"

    term_id = fields.Many2one(
        "purchase.terms.condition", string="Term",
        required=True, ondelete="cascade", index=True,
    )
    value = fields.Char(string="Value", required=True)


class PurchaseTermsConditionTemplate(models.Model):
    _name = "purchase.terms.condition.template"
    _description = "Purchase Terms & Conditions Template"
    _order = "name"

    name = fields.Char(string="Name", required=True)
    term_ids = fields.Many2many(
        "purchase.terms.condition", string="Terms & Conditions",
        help="Term types bundled in this template. Applying the template on "
             "a Purchase Order adds one line per term here.",
    )
