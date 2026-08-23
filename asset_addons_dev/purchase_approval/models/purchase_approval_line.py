from odoo import models, fields, api
from odoo.exceptions import AccessError, UserError


class PurchaseApprovalLine(models.Model):
    _name = "purchase.approval.line"
    _description = "Purchase Order Approval Line"
    _order = "sequence, id"

    order_id = fields.Many2one(
        "purchase.order", string="Purchase Order",
        required=True, ondelete="cascade", index=True,
    )
    sequence = fields.Integer(default=10)
    approver_id = fields.Many2one(
        "res.users", string="Approver", required=True, index=True,
    )
    status = fields.Selection(
        [
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        string="Status", default="pending", required=True, copy=False,
    )
    comment = fields.Char(string="Comment", copy=False)
    action_date = fields.Datetime(string="Action Date", copy=False)

    can_action = fields.Boolean(
        string="Can Act", compute="_compute_can_action",
        help="Technical field: True only for the assigned approver, and "
             "only while their own line is still pending.",
    )

    @api.depends("approver_id", "status")
    def _compute_can_action(self):
        uid = self.env.user
        for line in self:
            line.can_action = (line.approver_id == uid and line.status == "pending")

    def _check_is_own_line(self):
        for line in self:
            if line.approver_id != self.env.user and not self.env.user.has_group("base.group_system"):
                raise AccessError(
                    "Only %s can approve or reject this line." % line.approver_id.display_name
                )

    def action_approve(self):
        self._check_is_own_line()
        for line in self:
            if line.status != "pending":
                continue
            line.write({"status": "approved", "action_date": fields.Datetime.now()})
            line.order_id.message_post(
                body="%s approved this Purchase Order." % line.approver_id.display_name
            )
            line._close_own_activity()
        return True

    def action_reject(self):
        self._check_is_own_line()
        for line in self:
            if line.status != "pending":
                continue
            line.write({"status": "rejected", "action_date": fields.Datetime.now()})
            reason = (" Reason: %s" % line.comment) if line.comment else ""
            line.order_id.message_post(
                body="%s rejected this Purchase Order.%s"
                     % (line.approver_id.display_name, reason)
            )
            line._close_own_activity()
        return True

    def _close_own_activity(self):
        self.ensure_one()
        activities = self.env["mail.activity"].search([
            ("res_model", "=", "purchase.order"),
            ("res_id", "=", self.order_id.id),
            ("user_id", "=", self.approver_id.id),
            ("summary", "=", self._activity_summary()),
        ])
        activities.action_feedback(feedback="Handled via Purchase Approval line.")

    def _activity_summary(self):
        self.ensure_one()
        return "Approval requested for %s" % self.order_id.name
