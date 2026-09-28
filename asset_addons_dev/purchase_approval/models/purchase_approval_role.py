from odoo import models, fields


class PurchaseApprovalRole(models.Model):
    _name = "purchase.approval.role"
    _description = "Purchase Approval Role"
    _order = "sequence, id"

    name = fields.Char(string="Name", required=True)
    sequence = fields.Integer(default=10)
    user_ids = fields.Many2many(
        "res.users", string="Approvers",
        help="Users who hold this approval role. Used on Approval "
             "Configuration lines to map an order amount band to a role "
             "instead of a fixed list of users.",
    )
