import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from .document_management import _mint_doc_view_token

_logger = logging.getLogger(__name__)


class DocumentManagementRevisionRequest(models.Model):
    _name = 'document.management.revision.request'
    _description = 'Document Revision Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    # Fields only these action methods may change (always, regardless of
    # state) - blocks a Contributor with plain write access from setting
    # state='approved' directly and skipping the approver check entirely.
    _TRANSITION_ONLY_FIELDS = {'state', 'approver_id', 'approved_date', 'rejection_reason'}
    # Fields the requester may edit freely while still drafting, locked
    # once submitted so a reviewed line can't change under the approver.
    _DRAFT_ONLY_FIELDS = {'reason', 'cutoff_date', 'line_ids'}

    name = fields.Char(string='Reference', readonly=True, copy=False, default=lambda self: _('New'))
    document_id = fields.Many2one('document.management', string='Document', required=True, ondelete='cascade')
    requested_by = fields.Many2one(
        'res.users', string='Requested By', default=lambda self: self.env.user, readonly=True)
    request_date = fields.Date(string='Request Date', default=fields.Date.context_today, readonly=True)
    reason = fields.Text(string='Reason', tracking=True)
    cutoff_date = fields.Date(string='Cutoff Date', tracking=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('applied', 'Applied'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='draft', copy=False, required=True, tracking=True)

    approver_id = fields.Many2one('res.users', string='Approved/Rejected By', readonly=True, tracking=True)
    approved_date = fields.Datetime(string='Approved/Rejected On', readonly=True)
    rejection_reason = fields.Text(string='Rejection Reason', readonly=True, tracking=True)

    line_ids = fields.One2many('document.management.revision.line', 'request_id', string='Changes')
    can_manage_access = fields.Boolean(related='document_id.can_manage_access')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('document_id'):
                document = self.env['document.management'].browse(vals['document_id'])
                if document.active_revision_request_id:
                    raise ValidationError(_(
                        'This document already has an active revision request (%s). '
                        'Finish or cancel it before starting another one.'
                    ) % document.active_revision_request_id.name)
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'document.management.revision.request') or _('New')
        records = super().create(vals_list)
        records.document_id.write({'state': 'under_revision'})
        return records

    def write(self, vals):
        if not self.env.context.get('_revision_transition'):
            if self._TRANSITION_ONLY_FIELDS & set(vals):
                raise UserError(_('Revision requests can only be changed through their action buttons.'))
            if self._DRAFT_ONLY_FIELDS & set(vals):
                for rec in self:
                    if rec.state != 'draft':
                        raise UserError(_('This revision request can no longer be edited once submitted.'))
        return super().write(vals)

    def unlink(self):
        raise UserError(_('Revision requests cannot be deleted. Cancel it instead.'))

    def action_submit(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_('Only a draft revision request can be submitted.'))
            if not rec.reason:
                raise ValidationError(_('Please state a reason for this revision before submitting.'))
            if not rec.line_ids:
                raise ValidationError(_('Add at least one change before submitting for approval.'))
            if not rec.cutoff_date:
                raise ValidationError(_('Please set a cutoff date before submitting.'))
            rec.with_context(_revision_transition=True).write({'state': 'submitted'})

    def action_approve(self):
        if not self.env.user.has_group('document_management.group_document_approver'):
            raise UserError(_('Only an approver can approve a revision request.'))
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(_('Only a submitted revision request can be approved.'))
            rec.sudo().with_context(_revision_transition=True).write({
                'state': 'approved',
                'approver_id': self.env.user.id,
                'approved_date': fields.Datetime.now(),
            })
            if rec.cutoff_date and rec.cutoff_date <= fields.Date.context_today(rec):
                rec.sudo()._apply()

    def action_reject(self, reason=False):
        if not self.env.user.has_group('document_management.group_document_approver'):
            raise UserError(_('Only an approver can reject a revision request.'))
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(_('Only a submitted revision request can be rejected.'))
            if not reason:
                raise UserError(_('Please state a reason for rejection.'))
            rec.sudo().with_context(_revision_transition=True).write({
                'state': 'rejected',
                'approver_id': self.env.user.id,
                'approved_date': fields.Datetime.now(),
                'rejection_reason': reason,
            })
            rec.document_id.sudo().write({'state': 'released'})

    def action_open_reject_wizard(self):
        self.ensure_one()
        return {
            'name': _('Reject Revision Request'),
            'type': 'ir.actions.act_window',
            'res_model': 'document.management.revision.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_request_id': self.id},
        }

    def action_cancel(self):
        is_manager = self.env.user.has_group('document_management.group_document_manager')
        for rec in self:
            if rec.state in ('applied', 'cancelled'):
                raise UserError(_('This revision request can no longer be cancelled.'))
            if not (rec.can_manage_access or is_manager):
                raise UserError(_("You don't have rights to cancel this revision request."))
            rec.sudo().with_context(_revision_transition=True).write({'state': 'cancelled'})
            rec.document_id.sudo().write({'state': 'released'})

    def _apply(self):
        """Materialize an approved revision request's staged changes onto
        its document. Called immediately by action_approve when the cutoff
        date has already arrived, or later by the daily cron once it does.
        """
        Line = self.env['document.management.line']
        op_order = {'add': 0, 'modify': 1, 'delete': 2}
        for rec in self:
            if rec.state != 'approved':
                continue
            document = rec.document_id
            new_rev_no = document.rev_no + 1
            counts = {'add': 0, 'modify': 0, 'delete': 0}
            for line in rec.line_ids.sorted(key=lambda l: op_order[l.operation]):
                counts[line.operation] += 1
                if line.operation == 'add':
                    Line.with_context(revision_apply=True).create({
                        'document_id': document.id,
                        'name': line.name,
                        'file': line.new_file,
                        'file_name': line.new_file_name,
                        'rev_no': new_rev_no,
                        'source_revision_line_id': line.id,
                    })
                elif line.operation == 'modify':
                    line.target_line_id.with_context(revision_apply=True).write({'active': False})
                    Line.with_context(revision_apply=True).create({
                        'document_id': document.id,
                        'name': line.name or line.target_line_id.name,
                        'file': line.new_file,
                        'file_name': line.new_file_name,
                        'rev_no': new_rev_no,
                        'replaces_line_id': line.target_line_id.id,
                        'source_revision_line_id': line.id,
                    })
                elif line.operation == 'delete':
                    line.target_line_id.with_context(revision_apply=True).write({'active': False})

            document.write({'rev_no': new_rev_no, 'state': 'released'})
            self.env['document.management.revision'].create({
                'document_id': document.id,
                'request_id': rec.id,
                'rev_no': new_rev_no,
                'description': _(
                    '%(reason)s (Added: %(add)s, Modified: %(modify)s, Deleted: %(delete)s)'
                ) % {
                    'reason': rec.reason,
                    'add': counts['add'],
                    'modify': counts['modify'],
                    'delete': counts['delete'],
                },
            })
            rec.with_context(_revision_transition=True).write({'state': 'applied'})

    def cron_apply_due_revisions(self):
        today = fields.Date.context_today(self)
        due = self.search([('state', '=', 'approved'), ('cutoff_date', '<=', today)])
        for rec in due:
            try:
                rec.sudo()._apply()
                self.env.cr.commit()
            except Exception:
                self.env.cr.rollback()
                _logger.exception('Failed to apply document revision request %s', rec.name)


class DocumentManagementRevisionLine(models.Model):
    _name = 'document.management.revision.line'
    _description = 'Document Revision Request Line'
    _order = 'id asc'

    request_id = fields.Many2one(
        'document.management.revision.request', string='Revision Request',
        required=True, ondelete='cascade')
    document_id = fields.Many2one(
        'document.management', string='Document', related='request_id.document_id', store=True)
    request_state = fields.Selection(related='request_id.state')

    operation = fields.Selection([
        ('add', 'Add New File'),
        ('modify', 'Replace Existing File'),
        ('delete', 'Delete Existing File'),
    ], string='Change Type', required=True, default='add')

    target_line_id = fields.Many2one(
        'document.management.line', string='Existing File',
        domain="[('document_id', '=', document_id), ('active', '=', True)]")
    name = fields.Char(string='Title')
    new_file = fields.Binary(string='New File')
    new_file_name = fields.Char(string='New File Name')
    description = fields.Char(string='Notes')

    _uniq_request_target = models.Constraint(
        'unique(request_id, target_line_id)',
        'This file is already targeted by another line in this revision request.',
    )

    @api.constrains('operation', 'target_line_id', 'new_file')
    def _check_operation_fields(self):
        for rec in self:
            if rec.operation == 'add':
                if rec.target_line_id:
                    raise ValidationError(_('An "Add New File" line cannot target an existing file.'))
                if not rec.new_file:
                    raise ValidationError(_('Please attach the new file to add.'))
            elif rec.operation == 'modify':
                if not rec.target_line_id:
                    raise ValidationError(_('Please select the existing file to replace.'))
                if not rec.new_file:
                    raise ValidationError(_('Please attach the replacement file.'))
            elif rec.operation == 'delete':
                if not rec.target_line_id:
                    raise ValidationError(_('Please select the existing file to delete.'))
                if rec.new_file:
                    raise ValidationError(_('A "Delete Existing File" line cannot carry a new file.'))

    @api.onchange('operation', 'target_line_id')
    def _onchange_prefill_name(self):
        for rec in self:
            if rec.target_line_id and not rec.name:
                rec.name = rec.target_line_id.name
            if rec.operation == 'delete':
                rec.new_file = False
                rec.new_file_name = False
            if rec.operation == 'add':
                rec.target_line_id = False

    def write(self, vals):
        if not self.env.context.get('_revision_transition'):
            for rec in self:
                if rec.request_id.state != 'draft':
                    raise UserError(_('This revision request can no longer be edited once submitted.'))
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.request_id.state != 'draft':
                raise UserError(_('This revision request can no longer be edited once submitted.'))
        return super().unlink()

    def action_view_old_file(self):
        """Open a view-only preview of the existing file being replaced or
        deleted, via that line's own view action."""
        self.ensure_one()
        if not self.target_line_id:
            raise UserError(_('There is no existing file on this line.'))
        return self.target_line_id.action_view_document()

    def action_view_new_file(self):
        """Open a view-only preview of the proposed new file, same secure
        viewer used for a document.management.line's current file."""
        self.ensure_one()
        if not self.new_file:
            raise UserError(_('There is no new file on this line.'))
        token = _mint_doc_view_token(self._name, self.id)
        return {
            'type': 'ir.actions.act_url',
            'url': '/doc/view/%s?model=%s&field=new_file&fname=new_file_name&token=%s' % (
                self.id, self._name, token),
            'target': 'new',
        }


class DocumentManagementRevisionRejectWizard(models.TransientModel):
    _name = 'document.management.revision.reject.wizard'
    _description = 'Reject Revision Request'

    request_id = fields.Many2one('document.management.revision.request', required=True)
    reason = fields.Text(string='Rejection Reason', required=True)

    def action_confirm(self):
        self.ensure_one()
        self.request_id.action_reject(self.reason)
        return {'type': 'ir.actions.act_window_close'}
