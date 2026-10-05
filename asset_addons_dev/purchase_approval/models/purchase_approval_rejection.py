import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PurchaseApprovalRejection(models.Model):
    """One rejection of an RFQ/PO in the approval flow, and what the
    purchase user changed before submitting it again.

    At rejection the order's reviewable values are snapshotted
    (purchase.order._approval_snapshot); on the next "Submit for Approval"
    they are compared against that snapshot and every difference is
    recorded in change_summary - so the approver sees exactly what was
    changed in response to their rejection."""
    _name = "purchase.approval.rejection"
    _description = "Purchase Approval Rejection"
    _order = "cycle desc, id desc"

    order_id = fields.Many2one(
        "purchase.order", required=True, ondelete="cascade", index=True)
    cycle = fields.Integer(string="Rejection #", readonly=True)
    rejected_by = fields.Many2one("res.users", string="Rejected By", readonly=True)
    rejected_on = fields.Datetime(string="Rejected On", readonly=True)
    reason = fields.Text(string="Reason", required=True, readonly=True)
    state = fields.Selection([
        ("open", "Awaiting Resubmission"),
        ("resubmitted", "Resubmitted"),
    ], default="open", readonly=True)
    resubmitted_by = fields.Many2one("res.users", string="Resubmitted By", readonly=True)
    resubmitted_on = fields.Datetime(string="Resubmitted On", readonly=True)
    change_summary = fields.Text(string="Changes Made", readonly=True)
    change_count = fields.Integer(string="Changes", readonly=True)
    snapshot = fields.Text(readonly=True)  # technical: JSON of _approval_snapshot()

    def _record_resubmission(self):
        """Diff the order against the snapshot taken at rejection."""
        for rec in self:
            before = json.loads(rec.snapshot or "{}")
            after = rec.order_id._approval_snapshot()
            changes = rec.order_id._approval_diff(before, after)
            rec.write({
                "state": "resubmitted",
                "resubmitted_by": self.env.user.id,
                "resubmitted_on": fields.Datetime.now(),
                "change_count": len(changes),
                "change_summary": "\n".join(changes) or _("No changes were made."),
            })
        return self


class PurchaseApprovalRejectWizard(models.TransientModel):
    _name = "purchase.approval.reject.wizard"
    _description = "Reject Purchase Order"

    order_id = fields.Many2one("purchase.order", required=True)
    reason = fields.Text(string="Reason for Rejection", required=True)

    def action_confirm_reject(self):
        self.ensure_one()
        if not (self.reason or "").strip():
            raise UserError(_("Please enter a reason for the rejection."))
        self.order_id._reject_with_reason(self.reason.strip())
        return {"type": "ir.actions.act_window_close"}
