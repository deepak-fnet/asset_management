from odoo import models, fields, api
from odoo.exceptions import ValidationError


class PurchaseApprovalConfig(models.Model):
    _name = "purchase.approval.config"
    _description = "Purchase Approval Configuration"
    _order = "is_default desc, sequence, id"

    name = fields.Char(string="Name", required=True, default="Approval Flow")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    is_default = fields.Boolean(
        string="Default",
        default=True,
        help="When ticked, every new Purchase Order automatically picks up this "
             "approval flow and approval becomes mandatory. If no configuration "
             "is marked as default, Purchase Orders are created without any "
             "approval requirement.",
    )
    company_id = fields.Many2one(
        "res.company", string="Company",
        default=lambda self: self.env.company,
    )
    line_ids = fields.One2many(
        "purchase.approval.config.line", "config_id",
        string="Approvers", copy=True,
    )
    approver_ids = fields.Many2many(
        "res.users", string="Approver Users",
        compute="_compute_approver_ids",
        help="Technical: flat list of the users on the configuration lines.",
    )

    @api.depends("line_ids.user_id")
    def _compute_approver_ids(self):
        for config in self:
            config.approver_ids = config.line_ids.mapped("user_id")

    @api.constrains("is_default", "active", "company_id")
    def _check_single_default(self):
        """Only one active default flow per company — otherwise which flow a new
        Purchase Order picks up would be arbitrary."""
        for config in self:
            if not (config.is_default and config.active):
                continue
            clash = self.search([
                ("id", "!=", config.id),
                ("is_default", "=", True),
                ("active", "=", True),
                ("company_id", "=", config.company_id.id),
            ], limit=1)
            if clash:
                raise ValidationError(
                    "There is already a default approval configuration for this "
                    "company ('%s').\n\nUntick 'Default' on that one first, or "
                    "untick it here." % clash.display_name
                )

    @api.constrains("line_ids")
    def _check_lines(self):
        for config in self:
            if config.is_default and config.active and not config.line_ids:
                raise ValidationError(
                    "A default approval configuration must have at least one "
                    "approver, otherwise Purchase Orders could never be approved."
                )

    @api.model
    def _get_default_config(self, company=None):
        """The active default flow for a company, or an empty recordset."""
        company = company or self.env.company
        return self.search([
            ("is_default", "=", True),
            ("active", "=", True),
            ("company_id", "in", [company.id, False]),
        ], limit=1)


class PurchaseApprovalConfigLine(models.Model):
    _name = "purchase.approval.config.line"
    _description = "Purchase Approval Configuration Line"
    _order = "sequence, id"

    config_id = fields.Many2one(
        "purchase.approval.config", string="Configuration",
        required=True, ondelete="cascade", index=True,
    )
    sequence = fields.Integer(default=10)
    user_id = fields.Many2one(
        "res.users", string="Approver", required=True,
    )

    _sql_constraints = [
        ("uniq_user_per_config",
         "unique(config_id, user_id)",
         "The same approver cannot be added twice to one approval configuration."),
    ]
