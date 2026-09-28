from odoo import models, fields, api


class PurchaseOrderTermsLine(models.Model):
    _name = "purchase.order.terms.line"
    _description = "Purchase Order Terms & Conditions Line"
    _order = "id"

    order_id = fields.Many2one(
        "purchase.order", string="Purchase Order",
        required=True, ondelete="cascade", index=True,
    )
    term_id = fields.Many2one(
        "purchase.terms.condition", string="Name", required=True,
    )
    value_id = fields.Many2one(
        "purchase.terms.condition.value", string="Value",
        domain="[('term_id', '=', term_id)]",
    )


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    # Labeled "Payment Terms" in the view (and positioned at the header,
    # before Purchase Agreement) rather than "Terms & Conditions Template" -
    # same field, same model (purchase.terms.condition.template), just this
    # business's preferred name for "which bundle of terms applies here."
    # Picking one prefills terms_condition_line_ids with one row per term
    # type in the template; each row's own Value cell is domain-filtered to
    # that term's value_ids (see views/purchase_order_views.xml).
    terms_template_id = fields.Many2one(
        "purchase.terms.condition.template", string="Payment Terms",
        help="Pick a template to prefill the Terms Conditions lines below "
             "with one row per term type in the template.",
    )
    terms_condition_line_ids = fields.One2many(
        "purchase.order.terms.line", "order_id", string="Terms & Conditions",
    )

    @api.onchange("terms_template_id")
    def _onchange_terms_template_id(self):
        # Replaces whatever is already there — applying a template always
        # starts from a clean set of lines instead of appending to it.
        for order in self:
            if order.terms_template_id:
                order.terms_condition_line_ids = [(5, 0, 0)] + [
                    (0, 0, {"term_id": term.id})
                    for term in order.terms_template_id.term_ids
                ]
