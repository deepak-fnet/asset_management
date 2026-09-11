import secrets

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.http import request


def _mint_doc_view_token(model, res_id):
    """Bind a /doc/view or /doc/download URL to the current browser
    session, so a copied link stops working once opened elsewhere."""
    token = secrets.token_urlsafe(24)
    tokens = dict(request.session.get('doc_view_tokens') or {})
    tokens[token] = '%s,%s' % (model, res_id)
    request.session['doc_view_tokens'] = tokens
    return token


class DocumentManagement(models.Model):
    _name = 'document.management'
    _description = 'Document'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'
    _rec_name = 'title'

    name = fields.Char(string='Reference', readonly=True, copy=False, default=lambda self: _('New'))
    title = fields.Char(string='Title', required=True, tracking=True)
    description = fields.Text(string='Description')
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company, required=True)
    owner_id = fields.Many2one(
        'res.users', string='Owner', default=lambda self: self.env.user,
        readonly=True, tracking=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('released', 'Released'),
        ('under_revision', 'Under Revision'),
    ], string='Status', default='draft', tracking=True, copy=False, required=True)

    rev_no = fields.Integer(string='Revision No.', default=0, copy=False, tracking=True)
    rev_label = fields.Char(string='Revision', compute='_compute_rev_label')
    is_locked = fields.Boolean(string='Locked', compute='_compute_is_locked')

    document_line_ids = fields.One2many('document.management.line', 'document_id', string='Files')
    document_count = fields.Integer(string='File Count', compute='_compute_counts')
    access_ids = fields.One2many('document.management.access', 'document_id', string='User Access')
    access_count = fields.Integer(string='Users', compute='_compute_counts')
    revision_ids = fields.One2many('document.management.revision', 'document_id', string='Revision History')
    revision_count = fields.Integer(string='Revisions', compute='_compute_counts')
    revision_request_ids = fields.One2many(
        'document.management.revision.request', 'document_id', string='Revision Requests')
    revision_request_count = fields.Integer(string='Revision Request Count', compute='_compute_counts')
    active_revision_request_id = fields.Many2one(
        'document.management.revision.request', string='Active Revision Request',
        compute='_compute_active_revision_request')

    is_view = fields.Boolean(string='Can View', compute='_compute_permission_flags')
    is_upload = fields.Boolean(string='Can Upload', compute='_compute_permission_flags')
    is_download = fields.Boolean(string='Can Download', compute='_compute_permission_flags')
    can_manage_access = fields.Boolean(string='Can Manage Access', compute='_compute_can_manage_access')

    @api.depends('document_line_ids', 'access_ids', 'revision_ids', 'revision_request_ids')
    def _compute_counts(self):
        for rec in self:
            rec.document_count = len(rec.document_line_ids)
            rec.access_count = len(rec.access_ids)
            rec.revision_count = len(rec.revision_ids)
            rec.revision_request_count = len(rec.revision_request_ids)

    @api.depends('rev_no')
    def _compute_rev_label(self):
        for rec in self:
            rec.rev_label = 'Rev %02d' % rec.rev_no

    @api.depends('state')
    def _compute_is_locked(self):
        for rec in self:
            rec.is_locked = rec.state in ('submitted', 'released', 'under_revision')

    @api.depends('revision_request_ids.state')
    def _compute_active_revision_request(self):
        for rec in self:
            rec.active_revision_request_id = rec.revision_request_ids.filtered(
                lambda r: r.state not in ('applied', 'rejected', 'cancelled'))[:1]

    def _compute_permission_flags(self):
        user = self.env.user
        is_manager = user.has_group('document_management.group_document_manager')
        is_approver = user.has_group('document_management.group_document_approver')
        for rec in self:
            line = rec.access_ids.filtered(lambda l: l.user_id == user)
            if line:
                rec.is_view = bool(line.is_view)
                rec.is_upload = bool(line.is_upload)
                rec.is_download = bool(line.is_download)
            elif is_manager or rec.owner_id == user:
                rec.is_view = rec.is_upload = rec.is_download = True
            elif is_approver:
                rec.is_view = rec.is_download = True
                rec.is_upload = False
            else:
                rec.is_view = rec.is_upload = rec.is_download = False

    def _compute_can_manage_access(self):
        user = self.env.user
        is_manager = user.has_group('document_management.group_document_manager')
        for rec in self:
            rec.can_manage_access = is_manager or rec.owner_id == user

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('document.management') or _('New')
        records = super().create(vals_list)
        for rec in records:
            if not rec.access_ids:
                rec.access_ids = [(0, 0, {
                    'user_id': rec.owner_id.id,
                    'is_view': True,
                    'is_upload': True,
                    'is_download': True,
                })]
        return records

    def action_upload_document(self):
        self.ensure_one()
        if self.is_locked:
            raise UserError(_('This document is submitted. Create a revision before uploading new files.'))
        if not self.is_upload:
            raise UserError(_("You don't have Upload access on this document."))
        return {
            'name': _('Upload File'),
            'type': 'ir.actions.act_window',
            'res_model': 'document.management.line',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_document_id': self.id,
                'default_rev_no': self.rev_no,
            },
        }

    def action_submit(self):
        for rec in self:
            if not rec.document_line_ids:
                raise ValidationError(_('Please upload at least one file before submitting.'))
            if not rec.access_ids:
                raise ValidationError(_('Please grant user access before submitting.'))
            rec.state = 'submitted'

    def action_reset_to_draft(self):
        for rec in self:
            rec.state = 'draft'

    def action_release(self):
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(_('Only a submitted document can be released.'))
            rec.state = 'released'

    def action_create_revision_request(self):
        self.ensure_one()
        is_manager = self.env.user.has_group('document_management.group_document_manager')
        if self.state != 'released':
            raise UserError(_('Only a released document can be revised.'))
        if not (self.can_manage_access or is_manager):
            raise UserError(_("You don't have rights to raise a revision on this document."))
        request = self.env['document.management.revision.request'].create({
            'document_id': self.id,
        })
        return {
            'name': _('Revision Request'),
            'type': 'ir.actions.act_window',
            'res_model': 'document.management.revision.request',
            'res_id': request.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_active_revision_request(self):
        self.ensure_one()
        if not self.active_revision_request_id:
            raise UserError(_('There is no active revision request on this document.'))
        return {
            'name': _('Revision Request'),
            'type': 'ir.actions.act_window',
            'res_model': 'document.management.revision.request',
            'res_id': self.active_revision_request_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_revision_requests(self):
        """Smart button: every revision request ever raised on this
        document, not just the one currently in progress."""
        self.ensure_one()
        action = {
            'name': _('Revision Requests'),
            'type': 'ir.actions.act_window',
            'res_model': 'document.management.revision.request',
            'view_mode': 'list,form',
            'domain': [('document_id', '=', self.id)],
            'context': {'default_document_id': self.id},
        }
        if self.revision_request_count == 1:
            action.update({'view_mode': 'form', 'res_id': self.revision_request_ids.id})
        return action

    def action_view_all_files(self):
        """Open every current file on this document in the multi-tab
        viewer, for a quick look straight from the kanban card."""
        self.ensure_one()
        if not self.is_view:
            raise UserError(_("You don't have View access on this document."))
        lines = self.document_line_ids
        if not lines:
            raise UserError(_('This document has no files yet.'))
        ids = ','.join(str(i) for i in lines.ids)
        token = _mint_doc_view_token('document.management.line', ids)
        return {
            'type': 'ir.actions.act_url',
            'url': '/doc/view/multi?model=document.management.line&ids=%s&field=file&fname=file_name&token=%s' % (
                ids, token),
            'target': 'new',
        }

    def action_view_document_lines_kanban(self):
        """Smart button: browse this document's own files as a gallery of
        cards (View/Download/Upload per card), instead of the plain list."""
        self.ensure_one()
        return {
            'name': _('Documents'),
            'type': 'ir.actions.act_window',
            'res_model': 'document.management.line',
            'view_mode': 'kanban,list,form',
            'domain': [('document_id', '=', self.id)],
            'context': {'default_document_id': self.id, 'default_rev_no': self.rev_no},
        }


class DocumentManagementLine(models.Model):
    _name = 'document.management.line'
    _description = 'Document File'
    _order = 'id desc'
    _rec_name = 'name'

    document_id = fields.Many2one('document.management', string='Document', required=True, ondelete='cascade')
    name = fields.Char(string='Title', required=True)
    file = fields.Binary(string='File', required=True, attachment=True)
    file_name = fields.Char(string='File Name')
    file_type = fields.Char(string='Type', compute='_compute_file_type', store=True)
    description = fields.Char(string='Notes')
    rev_no = fields.Integer(string='Revision', default=lambda self: self.env.context.get('default_rev_no', 0))
    upload_date = fields.Datetime(string='Uploaded On', default=fields.Datetime.now, readonly=True)
    uploaded_by = fields.Many2one('res.users', string='Uploaded By', default=lambda self: self.env.uid, readonly=True)
    active = fields.Boolean(default=True)
    replaces_line_id = fields.Many2one(
        'document.management.line', string='Replaces',
        help='Previous version of this file, superseded by this one via a revision.')
    source_revision_line_id = fields.Many2one(
        'document.management.revision.line', string='Added/Changed By',
        help='Revision request line that created or replaced this file.')

    is_view = fields.Boolean(string='Can View', related='document_id.is_view')
    is_download = fields.Boolean(string='Can Download', related='document_id.is_download')

    @api.depends('file_name')
    def _compute_file_type(self):
        type_map = {
            'pdf': 'PDF', 'ppt': 'PPT', 'pptx': 'PPTX',
            'doc': 'DOC', 'docx': 'DOCX', 'xls': 'XLS', 'xlsx': 'XLSX',
            'png': 'Image', 'jpg': 'Image', 'jpeg': 'Image',
            'gif': 'Image', 'bmp': 'Image', 'webp': 'Image',
            'mp4': 'Video', 'avi': 'Video', 'mov': 'Video', 'webm': 'Video',
            'csv': 'CSV', 'txt': 'Text',
        }
        for rec in self:
            if rec.file_name and '.' in rec.file_name:
                ext = rec.file_name.rsplit('.', 1)[-1].lower()
                rec.file_type = type_map.get(ext, ext.upper())
            else:
                rec.file_type = 'Unknown'

    def action_view_document(self):
        """Open a view-only preview in a new tab (no download, screenshot guarded)."""
        self.ensure_one()
        if not self.is_view:
            raise UserError(_("You don't have View access on this document."))
        token = _mint_doc_view_token(self._name, self.id)
        return {
            'type': 'ir.actions.act_url',
            'url': '/doc/view/%s?model=%s&field=file&fname=file_name&token=%s' % (
                self.id, self._name, token),
            'target': 'new',
        }

    def action_download(self):
        self.ensure_one()
        if not self.is_download:
            raise UserError(_("You don't have Download access on this document."))
        token = _mint_doc_view_token(self._name, self.id)
        return {
            'type': 'ir.actions.act_url',
            'url': '/dms/document/download/%s?token=%s' % (self.id, token),
            'target': 'self',
        }

    def _check_not_locked(self):
        if self.env.context.get('revision_apply'):
            return
        for rec in self:
            if rec.document_id.is_locked:
                raise UserError(_(
                    'This document is locked. Files can only be changed through '
                    'an approved revision request.'))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get('revision_apply'):
            documents = self.env['document.management'].browse(
                {vals['document_id'] for vals in vals_list if vals.get('document_id')})
            locked = documents.filtered('is_locked')
            if locked:
                raise UserError(_(
                    'This document is locked. Files can only be changed through '
                    'an approved revision request.'))
        return super().create(vals_list)

    def write(self, vals):
        self._check_not_locked()
        return super().write(vals)

    def unlink(self):
        self._check_not_locked()
        return super().unlink()


class DocumentManagementAccess(models.Model):
    _name = 'document.management.access'
    _description = 'Document User Access'
    _order = 'id desc'

    document_id = fields.Many2one('document.management', string='Document', required=True, ondelete='cascade')
    user_id = fields.Many2one('res.users', string='User', required=True)
    is_view = fields.Boolean(string='View')
    is_upload = fields.Boolean(string='Upload')
    is_download = fields.Boolean(string='Download')

    _uniq_doc_user = models.Constraint(
        'unique(document_id, user_id)',
        'This user already has an access line on this document.',
    )


class DocumentManagementRevision(models.Model):
    _name = 'document.management.revision'
    _description = 'Document Revision History'
    _order = 'rev_no desc, id desc'

    document_id = fields.Many2one('document.management', string='Document', required=True, ondelete='cascade')
    request_id = fields.Many2one(
        'document.management.revision.request', string='Revision Request', ondelete='set null')
    rev_no = fields.Integer(string='Revision No.')
    description = fields.Text(string='Change Description', required=True)
    user_id = fields.Many2one('res.users', string='Revised By', default=lambda self: self.env.uid, readonly=True)
    date = fields.Datetime(string='Date', default=fields.Datetime.now, readonly=True)
