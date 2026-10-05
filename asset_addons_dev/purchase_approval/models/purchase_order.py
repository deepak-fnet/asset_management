import json

from markupsafe import Markup

from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import formatLang


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    approver_ids = fields.Many2many(
        "res.users", "purchase_order_approver_rel", "order_id", "user_id",
        string="Approvers", compute="_compute_approver_ids", store=True,
        help="Users required to approve this Purchase Order, resolved from "
             "the approval rules that match its total. Read-only: populated "
             "by 'Submit for Approval'. Empty means this order does not "
             "require approval (yet).",
    )
    approval_line_ids = fields.One2many(
        "purchase.approval.line", "order_id", string="Approvals",
    )

    @api.depends("approval_line_ids.approver_id")
    def _compute_approver_ids(self):
        for order in self:
            order.approver_ids = order.approval_line_ids.approver_id

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
        """Header button — asks for a reason, then rejects only the current
        user's own line (see _reject_with_reason)."""
        self.ensure_one()
        if not self._own_pending_line():
            raise UserError(
                "You do not have a pending approval on this Purchase Order."
            )
        return {
            "type": "ir.actions.act_window",
            "name": "Reject Purchase Order",
            "res_model": "purchase.approval.reject.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_order_id": self.id},
        }

    # ------------------------------------------------------------------
    # Rejection history: reason every time, and what changed afterwards
    # ------------------------------------------------------------------
    approval_submitted_by = fields.Many2one(
        "res.users", string="Submitted for Approval By", copy=False, readonly=True)
    rejection_ids = fields.One2many(
        "purchase.approval.rejection", "order_id", string="Rejections", copy=False)
    rejection_count = fields.Integer(compute="_compute_rejection_count")

    @api.depends("rejection_ids")
    def _compute_rejection_count(self):
        for order in self:
            order.rejection_count = len(order.rejection_ids)

    def _reject_with_reason(self, reason):
        self.ensure_one()
        line = self._own_pending_line()
        if not line:
            raise UserError(
                "You do not have a pending approval on this Purchase Order."
            )
        line.comment = reason
        line.action_reject()
        rejection = self.env["purchase.approval.rejection"].create({
            "order_id": self.id,
            "cycle": len(self.rejection_ids) + 1,
            "rejected_by": self.env.user.id,
            "rejected_on": fields.Datetime.now(),
            "reason": reason,
            "snapshot": json.dumps(self._approval_snapshot()),
        })
        # Nobody else needs to act on this round any more.
        self._close_approval_activities()
        self._approval_notify(
            self._approval_requesters(),
            "Rejected: %s" % self.name,
            Markup(
                "<p><b>%s</b> was <b>rejected</b> by %s (rejection #%s).</p>"
                "<p><b>Reason:</b> %s</p>"
                "<p>Please make the required changes and submit it for approval again.</p>"
            ) % (self.name, self.env.user.name, rejection.cycle, reason),
        )
        return rejection

    def action_view_rejections(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Rejections - %s" % self.name,
            "res_model": "purchase.approval.rejection",
            "view_mode": "list,form",
            "domain": [("order_id", "=", self.id)],
            "context": {"create": False, "delete": False},
        }

    def _approval_snapshot(self):
        """Reviewable values of the order, flat: {key: [label, value]}.

        Compared at resubmission against the snapshot taken at rejection.
        Other modules extend it (vendor_management adds vendor bids and
        awarded vendors) by calling super() and adding keys."""
        self.ensure_one()
        snap = {}

        def money(value):
            return formatLang(self.env, value or 0.0, currency_obj=self.currency_id)

        snap["hdr:payment_term"] = ["Payment Terms (Other Information)",
                                    self.payment_term_id.display_name or ""]
        if "terms_template_id" in self._fields:
            snap["hdr:terms_template"] = ["Payment Terms",
                                          self.terms_template_id.display_name or ""]
            for term in self.terms_condition_line_ids:
                snap["term:%s" % term.term_id.id] = [
                    "Terms - %s" % term.term_id.display_name,
                    term.value_id.display_name or ""]
        for line in self.order_line.filtered(lambda l: not l.display_type):
            name = line.product_id.display_name or line.name
            snap["line:%s:qty" % line.id] = ["%s - Quantity" % name,
                                             "%g %s" % (line.product_qty, line.product_uom_id.name or "")]
            snap["line:%s:price" % line.id] = ["%s - Unit Price" % name, money(line.price_unit)]
            if line.discount:
                snap["line:%s:discount" % line.id] = ["%s - Discount" % name, "%g%%" % line.discount]
            snap["line:%s:taxes" % line.id] = ["%s - Taxes" % name,
                                               ", ".join(line.tax_ids.mapped("name"))]
        snap["hdr:total"] = ["Order Total", money(self.amount_total)]
        return snap

    @staticmethod
    def _approval_diff(before, after):
        """Human-readable list of what changed between two snapshots."""
        changes = []
        for key, (label, value) in after.items():
            old = before.get(key)
            if old is None:
                changes.append("%s: added (%s)" % (label, value or "-"))
            elif old[1] != value:
                changes.append("%s: %s → %s" % (label, old[1] or "-", value or "-"))
        for key, (label, value) in before.items():
            if key not in after:
                changes.append("%s: removed (was %s)" % (label, value or "-"))
        return changes

    # ------------------------------------------------------------------
    # Mail communication
    # ------------------------------------------------------------------
    def _approval_requesters(self):
        """Who to tell about the outcome: whoever submitted it, and the buyer."""
        self.ensure_one()
        return (self.approval_submitted_by | self.user_id).filtered("partner_id")

    def _approval_notify(self, users, subject, body):
        """Email + chatter message to the given users (skips the current user)."""
        self.ensure_one()
        partners = (users - self.env.user).partner_id
        if partners:
            self.message_post(
                body=body, subject=subject, partner_ids=partners.ids,
                message_type="comment", subtype_xmlid="mail.mt_comment")

    def _approval_request_tier(self, lines):
        """Ask the approvers on these lines to act: activity + email."""
        self.ensure_one()
        users = lines.approver_id
        for user in users:
            self.activity_schedule(
                "mail.mail_activity_data_todo",
                user_id=user.id,
                summary="Approval requested for %s" % self.name,
                note="Please review and approve or reject this Purchase Order.",
            )
        self._approval_notify(
            users,
            "Approval required: %s" % self.name,
            Markup("<p><b>%s</b> (%s) is waiting for your approval.</p>") % (
                self.name, formatLang(self.env, self.amount_total, currency_obj=self.currency_id)),
        )

    def _close_approval_activities(self):
        self.ensure_one()
        self.env["mail.activity"].search([
            ("res_model", "=", "purchase.order"),
            ("res_id", "=", self.id),
            ("summary", "=", "Approval requested for %s" % self.name),
        ]).action_feedback(feedback="Closed by the approval flow.")

    def _approval_after_line_approved(self, line):
        """A line was approved: move to the next tier, or report the result."""
        self.ensure_one()
        lines = self.approval_line_ids
        if lines and all(l.status == "approved" for l in lines):
            self._approval_notify(
                self._approval_requesters(),
                "Approved: %s" % self.name,
                Markup("<p><b>%s</b> is fully approved and can now be confirmed.</p>") % self.name,
            )
            return
        # Only when this approver's whole tier is done does the next one start.
        if any(l.status == "pending" and l.sequence <= line.sequence for l in lines):
            return
        pending = lines.filtered(lambda l: l.status == "pending")
        if pending:
            next_seq = min(pending.mapped("sequence"))
            self._approval_request_tier(pending.filtered(lambda l: l.sequence == next_seq))

    # ------------------------------------------------------------------
    # Configuration mapping
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        Config = self.env["purchase.approval.config"]
        # Orders created from an already-approved RFQ (vendor_management's
        # per-vendor split) must not pick up a fresh approval flow - the
        # RFQ's approval already covers them.
        if self.env.context.get("purchase_approval_skip_default"):
            return super().create(vals_list)
        for vals in vals_list:
            # Respect anything explicitly passed in (imports, other modules,
            # duplicating an order) — only fall back to the default flow.
            if vals.get("approval_config_id"):
                continue
            company = self.env["res.company"].browse(vals["company_id"]) \
                if vals.get("company_id") else self.env.company
            config = Config._get_default_config(company)
            if config and config.line_ids:
                vals["approval_config_id"] = config.id
        return super().create(vals_list)

    def action_apply_approval_config(self):
        """Re-apply the mapped (or default) configuration — useful if the
        configuration changed after the order was created."""
        for order in self:
            config = order.approval_config_id or \
                self.env["purchase.approval.config"]._get_default_config(order.company_id)
            if not config:
                raise UserError("No approval configuration is available.")
            order.approval_config_id = config.id
        return True

    # ------------------------------------------------------------------
    def action_submit_for_approval(self):
        for order in self:
            lines = order.approval_config_id.line_ids

            # Highest-sequence tier whose amount band contains the order
            # total: lower_limit <= amount_total, and amount_total <=
            # upper_limit unless upper_limit is 0 (open-ended top tier).
            matched = lines.filtered(
                lambda l: order.amount_total >= l.lower_limit
                and (not l.upper_limit or order.amount_total <= l.upper_limit)
            )
            if not matched:
                raise UserError(
                    "No approval rule matches this order amount."
                )
            matched_line = matched.sorted("sequence")[-1]

            # Cascading: qualifying for the matched tier also requires every
            # lower tier's sign-off, in sequence order.
            required_tiers = lines.filtered(
                lambda l: l.sequence <= matched_line.sequence
            ).sorted("sequence")

            # One entry per required approver, keyed to the lowest (earliest)
            # tier sequence that requires them.
            required_sequence = {}
            for tier in required_tiers:
                for user in tier.role_id.user_ids:
                    required_sequence.setdefault(user, tier.sequence)

            if not required_sequence:
                raise UserError(
                    "The matched approval rule has no approvers assigned. "
                    "Add users to the approval role(s) before submitting."
                )

            required_users = self.env["res.users"].browse(
                [user.id for user in required_sequence]
            )
            existing = order.approval_line_ids
            existing_approvers = existing.mapped("approver_id")

            # Drop lines for approvers no longer required by any tier.
            (existing.filtered(lambda l: l.approver_id not in required_users)).unlink()

            # Resubmitting resets every remaining line back to pending (and
            # re-syncs its sequence to the matched rule), so a previous
            # rejection (or a stale approval on a changed order) doesn't
            # silently carry over.
            for line in order.approval_line_ids:
                line.write({
                    "sequence": required_sequence[line.approver_id],
                    "status": "pending",
                    "comment": False,
                    "action_date": False,
                })

            new_approvers = required_users - existing_approvers
            for user in new_approvers:
                self.env["purchase.approval.line"].create({
                    "order_id": order.id,
                    "approver_id": user.id,
                    "sequence": required_sequence[user],
                })

            # A resubmission after rejection: record what was changed.
            reopened = order.rejection_ids.filtered(lambda r: r.state == "open")
            if reopened:
                reopened._record_resubmission()
                for rejection in reopened:
                    order.message_post(body=Markup(
                        "<p>Resubmitted after rejection #%s. Changes made:</p><pre>%s</pre>"
                    ) % (rejection.cycle, rejection.change_summary))

            order.approval_submitted_by = self.env.user
            order._close_approval_activities()
            order.message_post(
                body="Submitted for approval to: %s"
                     % ", ".join(required_users.mapped("display_name"))
            )
            # Sequential tiers: only the first tier is asked now; each next
            # tier is asked once the previous one has fully approved.
            first_seq = min(order.approval_line_ids.mapped("sequence"))
            order._approval_request_tier(
                order.approval_line_ids.filtered(lambda l: l.sequence == first_seq))
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
    def _check_approval_before_confirm(self):
        """Raise unless every order with an approval flow is fully approved.

        Gated on approval_config_id, NOT approver_ids: approver_ids is only
        populated by action_submit_for_approval() (it rolls up
        approval_line_ids.approver_id, and those lines only exist once
        submitted). Gating on it meant an order nobody had submitted yet had
        an empty approver_ids and sailed straight through. approval_config_id
        is set at create() time, before any submission. Separate method so
        other modules that replace the confirm step (vendor_management's RFQ
        split) can enforce the same rule without calling super()."""
        for order in self:
            if order.approval_config_id and order.approval_status != "approved":
                raise UserError(
                    "This Purchase Order requires approval before it can be "
                    "confirmed. Submit it for approval first.\n\n"
                    "Approval status: %s"
                    % (order.approval_progress or "Not submitted")
                )

    def button_confirm(self):
        self._check_approval_before_confirm()
        return super().button_confirm()

    # ------------------------------------------------------------------
    # Hard gate: no matter which button, wizard, or external call moves this
    # order into 'purchase', it cannot land there while approval is
    # incomplete. This does not rely on button_confirm() being called, so it
    # stays correct even alongside other modules that override or bypass it.
    # ------------------------------------------------------------------
    @api.constrains("state", "approval_config_id", "approval_status")
    def _check_approved_before_purchase(self):
        for order in self:
            if order.state == "purchase" and order.approval_config_id and order.approval_status != "approved":
                raise ValidationError(
                    "This Purchase Order requires approval before it can be "
                    "confirmed. Submit it for approval first.\n\n"
                    "Approval status: %s" % (order.approval_progress or "Not submitted")
                )
