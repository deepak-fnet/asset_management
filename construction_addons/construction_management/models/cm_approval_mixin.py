# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ConstructionApprovalMixin(models.AbstractModel):
    _name = 'cm.approval.mixin'
    _description = 'Prepare -> Verify -> Approve Workflow'

    # "Prepare" is simply record creation, already tracked for free by create_uid/create_date -
    # this mixin only needs to add the Verify and Approve stages on top of that, plus the
    # segregation-of-duty checks that stop one person from doing all three alone.
    approval_state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('verified', 'Verified'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], default='draft', tracking=True, copy=False)
    verified_by = fields.Many2one('res.users', readonly=True, copy=False)
    verified_date = fields.Datetime(readonly=True, copy=False)
    approved_by = fields.Many2one('res.users', readonly=True, copy=False)
    approved_date = fields.Datetime(readonly=True, copy=False)
    rejection_reason = fields.Text(help="Why this was rejected - required when rejecting.")

    def action_submit(self):
        for rec in self:
            if rec.approval_state != 'draft':
                raise UserError(_("Only a draft record can be submitted for verification."))
            rec.approval_state = 'submitted'

    def _cm_bypasses_segregation_of_duty(self):
        # A full Administrator (Settings access) is exempt from the "someone else must
        # verify/approve it" checks below - needed for demo/dev environments where only one
        # admin login exists. Once real per-role users are set up, this simply never applies
        # to them, since they won't hold the Administrator group.
        return self.env.user.has_group('base.group_system')

    def action_verify(self):
        for rec in self:
            if rec.approval_state != 'submitted':
                raise UserError(_("Only a submitted record can be verified."))
            if rec.create_uid.id == self.env.user.id and not rec._cm_bypasses_segregation_of_duty():
                raise UserError(_("You prepared this record - someone else must verify it."))
            rec.write({
                'approval_state': 'verified',
                'verified_by': self.env.user.id,
                'verified_date': fields.Datetime.now(),
            })

    def action_approve(self):
        for rec in self:
            if rec.approval_state != 'verified':
                raise UserError(_("Only a verified record can be approved."))
            if not rec._cm_bypasses_segregation_of_duty():
                if rec.verified_by.id == self.env.user.id:
                    raise UserError(_("You verified this record - someone else must approve it."))
                if rec.create_uid.id == self.env.user.id:
                    raise UserError(_("You prepared this record - someone else must approve it."))
            rec.write({
                'approval_state': 'approved',
                'approved_by': self.env.user.id,
                'approved_date': fields.Datetime.now(),
            })

    def action_reject(self):
        for rec in self:
            if rec.approval_state not in ('submitted', 'verified'):
                raise UserError(_("Only a submitted or verified record can be rejected."))
            if not rec.rejection_reason:
                raise UserError(_("Enter a Rejection Reason before rejecting."))
            rec.approval_state = 'rejected'

    def action_reset_to_draft(self):
        for rec in self:
            if rec.approval_state == 'approved':
                raise UserError(_(
                    "An approved record cannot be reset to draft - it has already been acted on."))
            rec.write({
                'approval_state': 'draft',
                'verified_by': False,
                'verified_date': False,
                'approved_by': False,
                'approved_date': False,
                'rejection_reason': False,
            })
