from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    approver_ids = fields.Many2many(
        "res.users", "purchase_order_approver_rel", "order_id", "user_id",
        string="Approvers",
        help="Users who must approve this Purchase Order before it can be "
             "confirmed. Leave empty if this order does not require approval.",
    )
    approval_line_ids = fields.One2many(
        "purchase.approval.line", "order_id", string="Approvals",
    )
    approval_status = fields.Selection(
        [
            ("draft", "Not Submitted"),
            ("submitted", "Waiting Approval"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        string="Approval Status", compute="_compute_approval_status",
        store=True, copy=False,
    )
    approval_progress = fields.Char(
        string="Approval Progress", compute="_compute_approval_status",
    )

    @api.depends("approval_line_ids.status")
    def _compute_approval_status(self):
        for order in self:
            lines = order.approval_line_ids
            if not lines:
                order.approval_status = "draft"
                order.approval_progress = ""
                continue
            approved = lines.filtered(lambda l: l.status == "approved")
            order.approval_progress = "%d / %d Approved" % (len(approved), len(lines))
            if any(l.status == "rejected" for l in lines):
                order.approval_status = "rejected"
            elif len(approved) == len(lines):
                order.approval_status = "approved"
            else:
                order.approval_status = "submitted"

    approval_config_id = fields.Many2one(
        "purchase.approval.config", string="Approval Flow", copy=False,
        help="Approval configuration mapped to this order. Set automatically "
             "from the default configuration when the order is created.",
    )

    can_approve = fields.Boolean(
        string="Can Approve", compute="_compute_can_approve",
        help="Technical field: True when the current user has a pending "
             "approval line on this order. Drives the header buttons.",
    )

    @api.depends("approval_line_ids.status", "approval_line_ids.approver_id")
    def _compute_can_approve(self):
        uid = self.env.user
        for order in self:
            order.can_approve = bool(order.approval_line_ids.filtered(
                lambda l: l.approver_id == uid and l.status == "pending"
            ))

    def _own_pending_line(self):
        """The current user's pending approval line on this order."""
        self.ensure_one()
        return self.approval_line_ids.filtered(
            lambda l: l.approver_id == self.env.user and l.status == "pending"
        )[:1]

    def action_approve_order(self):
        """Header button — approves only the current user's own line."""
        for order in self:
            line = order._own_pending_line()
            if not line:
                raise UserError(
                    "You do not have a pending approval on this Purchase Order."
                )
            line.action_approve()
        return True

    def action_reject_order(self):
        """Header button — rejects only the current user's own line."""
        for order in self:
            line = order._own_pending_line()
            if not line:
                raise UserError(
                    "You do not have a pending approval on this Purchase Order."
                )
            line.action_reject()
        return True

    # ------------------------------------------------------------------
    # Configuration mapping
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        Config = self.env["purchase.approval.config"]
        for vals in vals_list:
            # Respect anything explicitly passed in (imports, other modules,
            # duplicating an order) — only fall back to the default flow.
            if vals.get("approval_config_id") or vals.get("approver_ids"):
                continue
            company = self.env["res.company"].browse(vals["company_id"]) \
                if vals.get("company_id") else self.env.company
            config = Config._get_default_config(company)
            if config and config.approver_ids:
                vals["approval_config_id"] = config.id
                vals["approver_ids"] = [(6, 0, config.approver_ids.ids)]
        return super().create(vals_list)

    def action_apply_approval_config(self):
        """Re-apply the mapped (or default) configuration — useful if the
        configuration changed after the order was created."""
        for order in self:
            config = order.approval_config_id or \
                self.env["purchase.approval.config"]._get_default_config(order.company_id)
            if not config:
                raise UserError("No approval configuration is available.")
            order.write({
                "approval_config_id": config.id,
                "approver_ids": [(6, 0, config.approver_ids.ids)],
            })
        return True

    # ------------------------------------------------------------------
    def action_submit_for_approval(self):
        for order in self:
            if not order.approver_ids:
                raise UserError(
                    "Please select at least one Approver before submitting "
                    "this Purchase Order for approval."
                )

            existing = order.approval_line_ids
            existing_approvers = existing.mapped("approver_id")

            # Drop lines for approvers who were removed from approver_ids.
            (existing.filtered(lambda l: l.approver_id not in order.approver_ids)).unlink()

            # Resubmitting resets every remaining line back to pending, so a
            # previous rejection (or a stale approval on a changed order)
            # doesn't silently carry over.
            kept = order.approval_line_ids
            if kept:
                kept.write({
                    "status": "pending",
                    "comment": False,
                    "action_date": False,
                })

            new_approvers = order.approver_ids - existing_approvers
            for user in new_approvers:
                self.env["purchase.approval.line"].create({
                    "order_id": order.id,
                    "approver_id": user.id,
                })

            for user in order.approver_ids:
                order.activity_schedule(
                    "mail.mail_activity_data_todo",
                    user_id=user.id,
                    summary="Approval requested for %s" % order.name,
                    note="Please review and approve or reject this Purchase Order.",
                )

            order.message_post(
                body="Submitted for approval to: %s"
                     % ", ".join(order.approver_ids.mapped("display_name"))
            )
        return True

    def action_reset_approval(self):
        for order in self:
            order.approval_line_ids.unlink()
            order.message_post(body="Approval process reset.")
        return True

    # ------------------------------------------------------------------
    # Enforcement is done in Python, not by hiding the button in a view.
    # Overriding the core button's `invisible` attribute conflicts with any
    # other module that manages the same button (Odoo replaces rather than
    # merges the attribute), so visibility is left to whichever module owns
    # that button and correctness is guaranteed here instead.
    # ------------------------------------------------------------------
    def button_confirm(self):
        for order in self:
            if order.approver_ids and order.approval_status != "approved":
                raise UserError(
                    "This Purchase Order requires approval from all assigned "
                    "Approvers before it can be confirmed.\n\n"
                    "Approval status: %s"
                    % (order.approval_progress or "Not submitted")
                )
        return super().button_confirm()

    # ------------------------------------------------------------------
    # Hard gate: no matter which button, wizard, or external call moves this
    # order into 'purchase', it cannot land there while approval is
    # incomplete. This does not rely on button_confirm() being called, so it
    # stays correct even alongside other modules that override or bypass it.
    # ------------------------------------------------------------------
    @api.constrains("state", "approver_ids")
    def _check_approved_before_purchase(self):
        for order in self:
            if order.state == "purchase" and order.approver_ids and order.approval_status != "approved":
                raise ValidationError(
                    "This Purchase Order requires approval from all assigned "
                    "Approvers before it can be confirmed.\n\n"
                    "Approval status: %s" % (order.approval_progress or "Not submitted")
                )
