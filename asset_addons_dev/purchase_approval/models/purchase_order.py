import json

from markupsafe import Markup

from odoo import _, models, fields, api
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
            ("query", "Query Raised"),
            ("revision", "Revision Requested"),
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
            elif any(l.status == "revision" for l in lines):
                order.approval_status = "revision"
            elif any(l.status == "query" for l in lines):
                order.approval_status = "query"
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

    @api.depends("approval_line_ids.status", "approval_line_ids.approver_id",
                 "approval_line_ids.sequence")
    @api.depends_context("uid")
    def _compute_can_approve(self):
        for order in self:
            order.can_approve = bool(order._own_pending_line())

    def _own_pending_line(self):
        """The current user's pending approval line on this order - only once
        every lower level has approved (levels act in order)."""
        self.ensure_one()
        lines = self.approval_line_ids
        return lines.filtered(
            lambda l: l.approver_id == self.env.user and l.status == "pending"
            and not any(o.sequence < l.sequence and o.status != "approved" for o in lines)
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
    # Reject: final. The order stays Rejected (a revision request is the
    # way to send it back for changes).
    # ------------------------------------------------------------------
    approval_submitted_by = fields.Many2one(
        "res.users", string="Submitted for Approval By", copy=False, readonly=True)

    def _reject_with_reason(self, reason):
        self.ensure_one()
        line = self._own_pending_line()
        if not line:
            raise UserError(
                "You do not have a pending approval on this Purchase Order."
            )
        line.comment = reason
        line.action_reject()
        event = self._approval_log("reject", text=reason, line=line)
        # Nobody else needs to act on this order any more.
        self._close_approval_activities()
        self._approval_notify(
            self._approval_requesters(),
            "Rejected: %s" % self.name,
            Markup(
                "<p><b>%s</b> was <b>rejected</b> by %s.</p>"
                "<p><b>Reason:</b> %s</p>"
            ) % (self.name, self.env.user.name, reason),
        )
        return event

    # ------------------------------------------------------------------
    # Comment / Query / Request Revision (approver) and the reply (creator)
    # ------------------------------------------------------------------
    approval_event_ids = fields.One2many(
        "purchase.approval.event", "order_id", string="Approval History", copy=False)
    open_query_id = fields.Many2one(
        "purchase.approval.event", compute="_compute_approval_events", string="Open Query")
    query_count = fields.Integer(compute="_compute_approval_events")
    revision_count = fields.Integer(compute="_compute_approval_events")
    can_reply_query = fields.Boolean(compute="_compute_can_reply_query")

    @api.depends("approval_event_ids.kind", "approval_event_ids.state")
    def _compute_approval_events(self):
        for order in self:
            events = order.approval_event_ids
            order.open_query_id = events.filtered(lambda e: e.kind == "query" and e.state == "open")[:1]
            order.query_count = len(events.filtered(lambda e: e.kind == "query"))
            order.revision_count = len(events.filtered(lambda e: e.kind == "revision"))

    @api.depends("approval_status", "approval_submitted_by", "user_id", "approval_event_ids.state")
    @api.depends_context("uid")
    def _compute_can_reply_query(self):
        # Only the user who submitted the order for approval answers a query.
        user = self.env.user
        for order in self:
            order.can_reply_query = bool(order.open_query_id) and user == order._approval_submitter()

    def _approval_submitter(self):
        self.ensure_one()
        return self.approval_submitted_by or self.user_id

    def _approval_log(self, kind, text=False, line=None, state="closed", snapshot=False):
        self.ensure_one()
        if line is None:
            line = self._own_pending_line()
        number = len(self.approval_event_ids.filtered(lambda e: e.kind == kind)) + 1
        level_name = False
        if line:
            # The approval role of that level, e.g. "Level - 1" or "CFO".
            tier = self.approval_config_id.line_ids.filtered(lambda t: t.sequence == line.sequence)[:1]
            level_name = tier.role_id.name or False
        return self.env["purchase.approval.event"].sudo().create({
            "order_id": self.id, "kind": kind, "text": text, "level_name": level_name,
            "user_id": self.env.user.id, "level": line.sequence if line else 0,
            "state": state, "number": number,
            "snapshot": json.dumps(snapshot) if snapshot else False,
        })

    def _approval_open_action(self, kind):
        self.ensure_one()
        if not self._own_pending_line():
            raise UserError(_("You do not have a pending approval on this Purchase Order."))
        title = {"comment": _("Add Comment"), "query": _("Raise Query"),
                 "revision": _("Request Revision")}[kind]
        return {
            "type": "ir.actions.act_window", "name": title,
            "res_model": "purchase.approval.action.wizard", "view_mode": "form", "target": "new",
            "context": {"default_order_id": self.id, "default_kind": kind},
        }

    def action_approval_comment(self):
        return self._approval_open_action("comment")

    def action_approval_query(self):
        return self._approval_open_action("query")

    def action_approval_revision(self):
        return self._approval_open_action("revision")

    def _approval_comment(self, text):
        self.ensure_one()
        line = self._own_pending_line()
        if not line:
            raise UserError(_("You do not have a pending approval on this Purchase Order."))
        self._approval_log("comment", text, line)
        self._approval_notify(
            self._approval_requesters(), _("Comment on %s", self.name),
            Markup("<p><b>%s</b> commented on <b>%s</b>:</p><blockquote>%s</blockquote>") % (
                self.env.user.name, self.name, text))

    def _approval_query(self, text):
        # Approver asks a question: their line is parked until the creator replies.
        self.ensure_one()
        line = self._own_pending_line()
        if not line:
            raise UserError(_("You do not have a pending approval on this Purchase Order."))
        event = self._approval_log("query", text, line, state="open")
        line.write({"status": "query", "comment": text, "action_date": fields.Datetime.now()})
        line._close_own_activity()
        requesters = self._approval_submitter()
        for user in requesters:
            self.activity_schedule(
                "mail.mail_activity_data_todo", user_id=user.id,
                summary=_("Reply to the approver's query on %s", self.name),
                note=text)
        self._approval_notify(
            requesters, _("Query on %s", self.name),
            Markup("<p><b>%s</b> has a query on <b>%s</b>:</p><blockquote>%s</blockquote>"
                   "<p>Open the order and click <b>Reply to Query</b>. The approval waits until you do.</p>")
            % (self.env.user.name, self.name, text))
        return event

    def _approval_reply(self, query, text):
        # Creator answers the query: the same approver gets the order back.
        self.ensure_one()
        if not self.can_reply_query or query != self.open_query_id:
            raise UserError(_("There is no open query for you to answer."))
        query.sudo().write({"state": "closed", "reply": text, "replied_by": self.env.user.id,
                            "replied_on": fields.Datetime.now()})
        self._approval_log("reply", text, line=self.env["purchase.approval.line"])
        self.env["mail.activity"].search([
            ("res_model", "=", "purchase.order"), ("res_id", "=", self.id),
            ("summary", "=", _("Reply to the approver's query on %s", self.name)),
        ]).action_feedback(feedback=_("Replied."))
        line = self.approval_line_ids.filtered(
            lambda l: l.status == "query" and l.approver_id == query.user_id)[:1]
        if line:
            line.sudo().write({"status": "pending", "comment": False, "action_date": False})
            self._approval_request_tier(line)
        self._approval_notify(
            query.user_id, _("Reply on %s", self.name),
            Markup("<p><b>%s</b> replied to your query on <b>%s</b>:</p><blockquote>%s</blockquote>"
                   "<p>The order is back with you for approval.</p>") % (
                self.env.user.name, self.name, text))

    def _approval_request_revision(self, text):
        # Approver asks for changes: the order goes back to the creator.
        self.ensure_one()
        line = self._own_pending_line()
        if not line:
            raise UserError(_("You do not have a pending approval on this Purchase Order."))
        event = self._approval_log("revision", text, line, state="open")
        event._store_order_copy()
        line.write({"status": "revision", "comment": text, "action_date": fields.Datetime.now()})
        # Nobody else needs to act on this round any more.
        self._close_approval_activities()
        self._approval_notify(
            self._approval_submitter(), _("Revision requested: %s", self.name),
            Markup("<p><b>%s</b> asked for changes on <b>%s</b>:</p><blockquote>%s</blockquote>"
                   "<p>Edit the order and submit it for approval again.</p>") % (
                self.env.user.name, self.name, text))

    def action_reply_query(self):
        self.ensure_one()
        if not self.can_reply_query:
            raise UserError(_("There is no open query for you to answer."))
        return {
            "type": "ir.actions.act_window", "name": _("Reply to Query"),
            "res_model": "purchase.approval.reply.wizard", "view_mode": "form", "target": "new",
            "context": {"default_order_id": self.id, "default_query_id": self.open_query_id.id},
        }

    def action_view_approval_events(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("Approval History - %s", self.name),
            "res_model": "purchase.approval.event", "view_mode": "list,form",
            "domain": [("order_id", "=", self.id)], "context": {"create": False},
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
            if order.approval_status in ("submitted", "approved"):
                raise UserError(_("This order is already %s.",
                                  dict(order._fields["approval_status"].selection)[order.approval_status]))
            if order.approval_status == "query":
                raise UserError(_("An approver has a query on this order - reply to it first."))
            if order.approval_status == "rejected":
                raise UserError(_("This order was rejected and cannot be submitted again."))
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

            # A resubmission after a revision request: keep a full copy of
            # the order as resubmitted (the revision event holds the copy
            # from before), viewable from the Approval History.
            revisions = order.approval_event_ids.filtered(
                lambda e: e.kind == "revision" and e.state == "open")
            if revisions:
                revisions._close_revision()
                order._approval_log(
                    "resubmit", text=_("Resubmitted after revision request #%s.",
                                       ", ".join(str(n) for n in revisions.mapped("number"))),
                    line=self.env["purchase.approval.line"])._store_order_copy()

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
