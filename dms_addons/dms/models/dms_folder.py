import logging

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

from .dms_storage import safe_name

_logger = logging.getLogger(__name__)

ACCESS_LEVELS = [('view', 'View'), ('download', 'View and Download'), ('edit', 'Edit')]
LEVEL_RANK = {False: 0, None: 0, 'none': 0, 'view': 1, 'download': 2, 'edit': 3}
# 'none' exists only on a single file: it takes someone's folder access away for that file
LINE_LEVELS = ACCESS_LEVELS + [('none', 'No access')]


class DmsFolder(models.Model):
    _name = 'dms.folder'
    _description = 'DMS Folder'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _parent_store = True
    _order = 'sequence, complete_name'
    _rec_name = 'complete_name'

    name = fields.Char(required=True, tracking=True)
    code = fields.Char("Code", help="Department code used in document numbers, e.g. QA, HR, ACC.", tracking=True)
    complete_name = fields.Char(compute='_compute_complete_name', store=True, recursive=True)
    sequence = fields.Integer(default=10)
    parent_id = fields.Many2one('dms.folder', ondelete='restrict', index=True)
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many('dms.folder', 'parent_id')
    department_id = fields.Many2one('hr.department', index=True, tracking=True)
    is_archive = fields.Boolean("Archive Folder", help="Holds obsolete revisions; view-only.")
    color = fields.Integer()
    active = fields.Boolean(default=True)
    server_path = fields.Char("Path on DMS Server", readonly=True, copy=False)
    delete_state = fields.Selection([('requested', 'Deletion requested')], copy=False, tracking=True)
    delete_requested_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    delete_requested_on = fields.Datetime(readonly=True, copy=False)
    delete_reason = fields.Text(readonly=True, copy=False)
    description = fields.Text()

    # approval chain (slide 10) — defaults come from the department
    reviewer_id = fields.Many2one('res.users', "L1 Reviewer", help="Department lead: technical content and accuracy.")
    approver_id = fields.Many2one('res.users', "L2 Approver", help="QA / Compliance: format, regulatory and policy fit.")
    final_approver_id = fields.Many2one('res.users', "L3 Final Authority", help="Director: business sign-off for policies and contracts.")
    dept_head_id = fields.Many2one('res.users', "Department Head", help="Co-approves disposal of archived files.")

    access_ids = fields.One2many('dms.access', 'folder_id', "Folder Access")
    document_ids = fields.One2many('dms.document', 'folder_id')
    document_count = fields.Integer(compute='_compute_document_count')

    _code_uniq = models.Constraint('UNIQUE(code)', 'Folder code must be unique.')

    @api.depends('name', 'parent_id.complete_name')
    def _compute_complete_name(self):
        for f in self:
            f.complete_name = f"{f.parent_id.complete_name} / {f.name}" if f.parent_id else f.name

    def _compute_document_count(self):
        data = dict(self.env['dms.document']._read_group(
            [('folder_id', 'in', self.ids)], ['folder_id'], ['__count']))
        for f in self:
            f.document_count = data.get(f, 0)

    @api.constrains('parent_id')
    def _check_recursion_parent(self):
        if self._has_cycle():
            raise ValidationError(_("A folder cannot be its own parent."))

    def _relative_path(self):
        self.ensure_one()
        parts = [safe_name(f.name).upper() for f in self.search([('id', 'parent_of', self.id)], order='parent_path')]
        return '/'.join(parts)

    def _ensure_server_dir(self):
        storage = self.env['dms.storage']
        for f in self:
            rel = f._relative_path()
            try:
                storage.make_dir(rel)
                f.sudo().server_path = rel
            except UserError as e:
                _logger.warning("DMS: could not create folder %s on server: %s", rel, e)

    @api.model_create_multi
    def create(self, vals_list):
        folders = super().create(vals_list)
        folders._ensure_server_dir()
        return folders

    def write(self, vals):
        res = super().write(vals)
        if 'name' in vals or 'parent_id' in vals:
            # new path for new uploads; existing files keep their stored path
            self.search([('id', 'child_of', self.ids)])._ensure_server_dir()
        return res

    # ------------------------------------------------------------------
    # Who may share, rename, delete
    # ------------------------------------------------------------------
    def _can_share(self, user):
        self.ensure_one()
        return not self.is_archive and (user.has_group('dms.group_dms_controller') or self._access_for_user(user) == 'edit')

    def _is_department_root(self):
        return bool(self.department_id and not self.parent_id) or (self.is_archive and not self.parent_id)

    def _can_rename(self, user):
        self.ensure_one()
        if self._is_department_root():
            return False
        return user.has_group('dms.group_dms_controller') or (
            self.create_uid == user and self._access_for_user(user) == 'edit')

    def _can_request_delete(self, user):
        self.ensure_one()
        return not self._is_department_root() and self._access_for_user(user) == 'edit'

    def _live_document_count(self):
        self.ensure_one()
        return self.env['dms.document'].sudo().search_count([
            ('folder_id', 'child_of', self.id), ('state', 'not in', ('discarded', 'rejected'))])

    def _contents(self):
        self.ensure_one()
        return self.env['dms.document'].sudo().search([
            ('folder_id', 'child_of', self.id), ('state', 'not in', ('discarded', 'rejected'))])

    def _request_delete(self, user, reason):
        self.ensure_one()
        if not self._can_request_delete(user):
            raise AccessError(_("You need Edit access on %s to ask for its deletion.", self.complete_name))
        self.write({'delete_state': 'requested', 'delete_requested_by_id': user.id,
                    'delete_requested_on': fields.Datetime.now(), 'delete_reason': reason})
        self.env['dms.log'].log('disposal_request', folder=self, details=f"Delete folder: {reason or ''}")
        controllers = self.env.ref('dms.group_dms_controller').user_ids
        for u in controllers:
            self.with_context(mail_activity_quick_update=True).activity_schedule('mail.mail_activity_data_todo', user_id=u.id,
                                   summary=_("DMS: delete folder %s?", self.complete_name), note=reason or '')

    def _decide_delete(self, user, approve, note=None):
        self.ensure_one()
        requester = self.delete_requested_by_id
        if not approve:
            self.write({'delete_state': False})
            self.activity_ids.unlink()
            self.env['dms.log'].log('disposal_refuse', folder=self, details=f"Keep folder: {note or ''}")
            if requester.partner_id:
                self.message_post(body=_("Deletion of %(f)s refused: %(n)s", f=self.complete_name, n=note or ''),
                                  subtype_xmlid='mail.mt_note')
            return
        live = self._live_document_count()
        if live:
            raise UserError(_("%(f)s still holds %(n)s document(s). Move them, or archive and dispose of them, "
                              "before the folder can be deleted.", f=self.complete_name, n=live))
        tree = self.search([('id', 'child_of', self.id)], order='parent_path desc')
        storage = self.env['dms.storage']
        # leftover refused / discarded documents go with the folder
        leftovers = self.env['dms.document'].sudo().with_context(active_test=False).search([('folder_id', 'in', tree.ids)])
        for rev in leftovers.revision_ids.filtered('storage_path'):
            storage.delete_file(rev.storage_path)
        leftovers.activity_ids.unlink()
        leftovers.unlink()
        for f in tree:
            if f.server_path:
                storage.remove_dir(f.server_path)
        self.env['dms.log'].log('discard', details=f"Folder deleted: {self.complete_name} "
                                                  f"(asked by {requester.name or user.name}, approved by {user.name})")
        tree.activity_ids.unlink()
        tree.unlink()

    # ------------------------------------------------------------------
    @api.model
    def _get_archive_folder(self):
        folder = self.search([('is_archive', '=', True), ('parent_id', '=', False)], limit=1)
        if not folder:
            folder = self.sudo().create({'name': 'Archive', 'code': 'ARC', 'is_archive': True, 'sequence': 999})
        return folder

    @api.model
    def _sync_department_folders(self):
        """One folder per department (slide 3), created on the DMS server too."""
        Folder = self.sudo().with_context(active_test=False)
        for dept in self.env['hr.department'].sudo().search([]):
            folder = Folder.search([('department_id', '=', dept.id), ('parent_id', '=', False)], limit=1)
            if not folder:
                Folder.create(dept._dms_folder_vals())
            elif not folder.server_path:
                folder._ensure_server_dir()
        archive = self._get_archive_folder()
        if not archive.server_path:
            archive._ensure_server_dir()

    def action_open_documents(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': self.complete_name, 'res_model': 'dms.document',
            'view_mode': 'list,form', 'domain': [('folder_id', 'child_of', self.id)],
            'context': {'default_folder_id': self.id},
        }

    # ------------------------------------------------------------------
    # Access resolution (slide 5): file level overrides folder level.
    # ------------------------------------------------------------------
    def _access_for_user(self, user):
        """Folder-level access of `user` on this folder (inherits from parents)."""
        self.ensure_one()
        if user.has_group('dms.group_dms_controller'):
            return 'edit'
        chain = self.search([('id', 'parent_of', self.id)])
        lines = chain.access_ids.filtered(lambda a: a._matches(user))
        levels = lines.mapped('level')
        groups = self.env['dms.group'].sudo().search([('user_ids', 'in', user.id), ('folder_ids', 'in', chain.ids)])
        levels += groups.mapped('level')
        best = max(levels, key=lambda lv: LEVEL_RANK[lv], default=False)
        if self.is_archive or any(f.is_archive for f in chain):
            return 'view' if best else False
        return best

    @api.model
    def _visible_folders(self, user):
        """Folders the user may open: has access on it, on a parent, or on a document inside."""
        all_folders = self.sudo().search([])
        if user.has_group('dms.group_dms_controller'):
            return all_folders
        Access = self.env['dms.access'].sudo()
        lines = Access.search(['|', ('user_id', '=', user.id), ('group_id', 'in', user.all_group_ids.ids)])
        granted = lines.folder_id | self.env['dms.group'].sudo().search([('user_ids', 'in', user.id)]).folder_ids
        visible = self.sudo().search([('id', 'child_of', granted.ids)]) if granted else self.browse()
        # a file shared on its own makes its folder reachable (showing only that file);
        # so do documents the user approves or owns, unless "No access" was set on them
        Doc = self.env['dms.document'].sudo()
        docs = lines.filtered(lambda a: a.level != 'none').document_id
        docs |= self.env['dms.revision'].sudo().search([('approval_ids.user_id', '=', user.id)]).document_id
        docs |= Doc.search([('owner_id', '=', user.id)])
        doc_folders = docs.filtered(lambda d: not d._blocked_for(user)).folder_id
        extra = self.sudo().search([('id', 'parent_of', doc_folders.ids)]) if doc_folders else self.browse()
        return (visible | extra) & all_folders


class DmsAccess(models.Model):
    _name = 'dms.access'
    _description = 'DMS Access Grant'
    _order = 'id desc'

    folder_id = fields.Many2one('dms.folder', ondelete='cascade', index=True)
    document_id = fields.Many2one('dms.document', ondelete='cascade', index=True)
    user_id = fields.Many2one('res.users', "User", index=True)
    group_id = fields.Many2one('res.groups', "Group")
    level = fields.Selection(LINE_LEVELS, required=True, default='view')
    granted_by_id = fields.Many2one('res.users', default=lambda s: s.env.user, readonly=True)
    create_date = fields.Datetime("Granted On", readonly=True)

    @api.constrains('user_id', 'group_id', 'folder_id', 'document_id')
    def _check_target(self):
        for a in self:
            if bool(a.user_id) == bool(a.group_id):
                raise ValidationError(_("Grant access to either a user or a group."))
            if bool(a.folder_id) == bool(a.document_id):
                raise ValidationError(_("Access applies to either a folder or a document."))

    def _matches(self, user):
        self.ensure_one()
        return self.user_id == user or (self.group_id and self.group_id in user.all_group_ids)

    def _target_label(self):
        return self.folder_id.complete_name if self.folder_id else self.document_id.display_name

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        for a in recs:
            who = a.user_id.name or a.group_id.name
            self.env['dms.log'].log('access_grant', folder=a.folder_id, document=a.document_id,
                                    details=f"{dict(LINE_LEVELS)[a.level]} → {who}")
            if a.user_id and a.user_id.partner_id:
                target = a.folder_id or a.document_id
                target.sudo().message_post(
                    body=_("%(user)s was granted %(level)s access to %(target)s.",
                           user=a.user_id.name, level=dict(LINE_LEVELS)[a.level], target=a._target_label()),
                    message_type='notification', subtype_xmlid='mail.mt_note')
        return recs

    def unlink(self):
        for a in self:
            self.env['dms.log'].log('access_revoke', folder=a.folder_id, document=a.document_id,
                                    details=f"{dict(LINE_LEVELS)[a.level]} ✕ {a.user_id.name or a.group_id.name}")
        return super().unlink()


class DmsLog(models.Model):
    _name = 'dms.log'
    _description = 'DMS Audit Log'
    _order = 'id desc'

    date = fields.Datetime(default=fields.Datetime.now, readonly=True)
    user_id = fields.Many2one('res.users', default=lambda s: s.env.user, readonly=True)
    action = fields.Selection([
        ('login', 'DMS Login'), ('upload', 'Upload'), ('submit', 'Submit for Approval'),
        ('approve', 'Approve'), ('refuse', 'Refuse'), ('publish', 'Published'),
        ('checkout', 'Check Out'), ('checkin', 'Check In'), ('unlock', 'Unlock'),
        ('force_unlock', 'Force Unlock'), ('view', 'View'), ('download', 'Download'),
        ('cutoff', 'Cut-off Changed'), ('archive', 'Archived'),
        ('access_grant', 'Access Granted'), ('access_revoke', 'Access Revoked'),
        ('disposal_request', 'Disposal Requested'), ('disposal_approve', 'Disposal Approved'),
        ('disposal_refuse', 'Disposal Refused'), ('discard', 'Discarded'),
    ], required=True, readonly=True)
    folder_id = fields.Many2one('dms.folder', readonly=True, ondelete='set null')
    document_id = fields.Many2one('dms.document', readonly=True, ondelete='set null')
    revision_id = fields.Many2one('dms.revision', readonly=True, ondelete='set null')
    document_ref = fields.Char("Document Ref", readonly=True, help="Kept even after the document is discarded.")
    details = fields.Char(readonly=True)

    @api.model
    def log(self, action, folder=None, document=None, revision=None, details=None):
        document = document or (revision.document_id if revision else None)
        folder = folder or (document.folder_id if document else None)
        return self.sudo().create({
            'action': action, 'user_id': self.env.user.id,
            'folder_id': folder.id if folder else False,
            'document_id': document.id if document else False,
            'revision_id': revision.id if revision else False,
            'document_ref': (document.display_name if document else '') +
                            (f" Rev {revision.rev_label}" if revision else ''),
            'details': details,
        })


class DmsGroup(models.Model):
    """A named bundle of folders: add a person to the group instead of granting folder by folder."""
    _name = 'dms.group'
    _description = 'DMS Access Group'
    _order = 'name'

    name = fields.Char(required=True)
    folder_ids = fields.Many2many('dms.folder', string="Folders", help="Sub-folders are included.")
    user_ids = fields.Many2many('res.users', 'dms_group_user_rel', string="Members", domain=[('share', '=', False)])
    level = fields.Selection(ACCESS_LEVELS, required=True, default='view')
    color = fields.Integer()
    description = fields.Char()

    def _log(self, details):
        for g in self:
            self.env['dms.log'].log('access_grant', details=f"Group {g.name}: {details}")

    @api.model_create_multi
    def create(self, vals_list):
        groups = super().create(vals_list)
        groups._ensure_dms_users()
        groups._log("created")
        return groups

    def write(self, vals):
        res = super().write(vals)
        if 'user_ids' in vals:
            self._ensure_dms_users()
        if {'user_ids', 'folder_ids', 'level'} & set(vals):
            self._log("members / folders / level changed")
        return res

    def _ensure_dms_users(self):
        grp = self.env.ref('dms.group_dms_user').sudo()
        missing = self.user_ids - grp.user_ids
        if missing:
            grp.write({'user_ids': [(4, u.id) for u in missing]})
