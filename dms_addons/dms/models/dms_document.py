import mimetypes
import os
import posixpath
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

from .dms_folder import ACCESS_LEVELS, LEVEL_RANK
from .dms_storage import safe_name


class DmsDocumentType(models.Model):
    _name = 'dms.document.type'
    _description = 'DMS Document Type'
    _order = 'name'

    name = fields.Char(required=True)
    code = fields.Char(required=True, help="Used in the document number, e.g. SOP → QA-SOP-014")
    requires_final_approval = fields.Boolean(
        "Needs L3 (Director)", help="Policies and contracts need Director sign-off (slide 10).")
    retention_days = fields.Integer("Retention after archive (days)", default=365 * 3)
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('UNIQUE(code)', 'Document type code must be unique.')


class DmsDocument(models.Model):
    _name = 'dms.document'
    _description = 'DMS Document'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'write_date desc'

    name = fields.Char("Title", required=True, tracking=True)
    code = fields.Char("Document No.", readonly=True, copy=False, index=True)
    folder_id = fields.Many2one('dms.folder', required=True, index=True, tracking=True, ondelete='restrict')
    type_id = fields.Many2one('dms.document.type', "Document Type", required=True, tracking=True)
    description = fields.Text()
    owner_id = fields.Many2one('res.users', "Owner / Author", default=lambda s: s.env.user, tracking=True)
    state = fields.Selection([
        ('draft', 'Draft'), ('in_review', 'In Review'), ('published', 'Published'),
        ('rejected', 'Rejected'), ('archived', 'Archived'), ('discarded', 'Discarded'),
    ], default='draft', tracking=True, index=True)
    revision_ids = fields.One2many('dms.revision', 'document_id')
    current_revision_id = fields.Many2one('dms.revision', compute='_compute_current', store=True)
    current_rev = fields.Integer("Rev", related='current_revision_id.rev_no', store=True)
    locked_by_id = fields.Many2one('res.users', "Checked out by", readonly=True, tracking=True)
    locked_date = fields.Datetime(readonly=True)
    access_ids = fields.One2many('dms.access', 'document_id', "File Access")
    favorite_user_ids = fields.Many2many('res.users', 'dms_document_favorite_rel', string="Starred by")
    origin_folder = fields.Char("Retired from", readonly=True, help="Folder the document lived in before it was retired.")
    origin_folder_id = fields.Many2one('dms.folder', "Retired from folder", readonly=True, index=True, ondelete='set null')
    retired_reason = fields.Text(readonly=True)

    def _compute_display_name(self):
        for d in self:
            d.display_name = f"{d.code} {d.name}" if d.code else d.name

    @api.depends('revision_ids.state')
    def _compute_current(self):
        for d in self:
            d.current_revision_id = d.revision_ids.filtered(lambda r: r.state == 'current')[:1]

    @api.model_create_multi
    def create(self, vals_list):
        docs = super().create(vals_list)
        for d in docs.filtered(lambda d: not d.code):
            d.code = d._next_code()
        return docs

    def _next_code(self):
        """DEPT-TYPE-NNN (slide 3). Revision is never part of the number."""
        self.ensure_one()
        prefix = f"{self.folder_id._root_code()}-{self.type_id.code}-"
        last = self.sudo().with_context(active_test=False).search(
            [('code', '=like', prefix + '%')], order='code desc', limit=1)
        n = int(last.code[len(prefix):]) + 1 if last and last.code[len(prefix):].isdigit() else 1
        return f"{prefix}{n:03d}"

    # ------------------------------------------------------------------
    # Access (slide 5)
    # ------------------------------------------------------------------
    def _access_for_user(self, user=None):
        self.ensure_one()
        user = user or self.env.user
        if user.has_group('dms.group_dms_controller'):
            return 'edit'
        file_lines = self.access_ids.filtered(lambda a: a._matches(user))
        if file_lines:  # file level overrides folder level
            level = max(file_lines.mapped('level'), key=lambda lv: LEVEL_RANK[lv])
            if level == 'none':
                level = False
        else:
            level = self.folder_id._access_for_user(user)
        if self.state == 'archived' or self.folder_id.is_archive:
            return 'view' if (level or self.owner_id == user) else False
        return level

    def _blocked_for(self, user):
        """"No access" set on this file wins over everything except the Document Controller."""
        self.ensure_one()
        return not user.has_group('dms.group_dms_controller') and bool(
            self.access_ids.filtered(lambda a: a.level == 'none' and a._matches(user)))

    def _check_level(self, needed, user=None):
        level = self._access_for_user(user)
        if LEVEL_RANK[level] < LEVEL_RANK[needed]:
            raise AccessError(_("You need %(need)s access on %(doc)s.",
                                need=dict(ACCESS_LEVELS)[needed], doc=self.display_name))
        return level

    def _visible_revisions(self, user=None):
        """Slide 9: who sees which revision."""
        self.ensure_one()
        user = user or self.env.user
        controller = user.has_group('dms.group_dms_controller')
        level = self._access_for_user(user)
        revs = self.env['dms.revision']
        for r in self.revision_ids.sorted('id', reverse=True):
            if r.state in ('current', 'superseded'):
                if level or r.author_id == user or user in r.approval_ids.user_id:
                    revs |= r
            elif r.state == 'archived':
                if controller or self.owner_id == user:
                    revs |= r
            elif r.state in ('draft', 'in_review'):
                if controller or r.author_id == user or user in r.approval_ids.user_id:
                    revs |= r
            elif r.state in ('rejected', 'dropped'):
                if controller or r.author_id == user:
                    revs |= r
        return revs

    # ------------------------------------------------------------------
    # Check out / check in (slide 7)
    # ------------------------------------------------------------------
    def action_checkout(self):
        for d in self:
            d._check_level('edit')
            if d.state != 'published':
                raise UserError(_("Only a published document can be checked out."))
            if d.locked_by_id and d.locked_by_id != self.env.user:
                raise UserError(_("%(doc)s is checked out by %(user)s.", doc=d.display_name, user=d.locked_by_id.name))
            if d.revision_ids.filtered(lambda r: r.state in ('draft', 'in_review')):
                raise UserError(_("A revision of this document is already pending."))
            d.sudo().write({'locked_by_id': self.env.user.id, 'locked_date': fields.Datetime.now()})
            self.env['dms.log'].log('checkout', document=d)

    def action_unlock(self, force=False):
        for d in self:
            mine = d.locked_by_id == self.env.user
            if not mine and not (force and self.env.user.has_group('dms.group_dms_controller')):
                raise AccessError(_("Only the user who checked it out, or the Document Controller, can unlock it."))
            if d.revision_ids.filtered(lambda r: r.state == 'in_review'):
                raise UserError(_("Nobody edits during review: the document unlocks when the review ends."))
            d.revision_ids.filtered(lambda r: r.state == 'draft').sudo().write({'state': 'dropped'})
            d.sudo().write({'locked_by_id': False, 'locked_date': False})
            self.env['dms.log'].log('force_unlock' if not mine else 'unlock', document=d)

    def action_checkin(self, filename, data, change_note):
        self.ensure_one()
        if self.locked_by_id != self.env.user:
            raise UserError(_("Check the document out before checking in a new version."))
        if not change_note:
            raise UserError(_("A change note is required."))
        cur = self.current_revision_id
        if cur and os.path.splitext(filename)[1].lower() != os.path.splitext(cur.file_name)[1].lower():
            raise UserError(_("Keep the same file type as the current revision (%s).", cur.file_name))
        self.revision_ids.filtered(lambda r: r.state == 'draft').sudo().write({'state': 'dropped'})
        rev = self.env['dms.revision'].sudo()._create_with_file(self, filename, data, change_note)
        self.env['dms.log'].log('checkin', revision=rev)
        return rev

    # ------------------------------------------------------------------
    # Move and retire (needed to empty a folder before deleting it)
    # ------------------------------------------------------------------
    def _check_movable(self):
        for d in self:
            if d.revision_ids.filtered(lambda r: r.state == 'in_review'):
                raise UserError(_("%s is in review. Wait for the decision before moving it.", d.display_name))
            if d.locked_by_id:
                raise UserError(_("%(d)s is checked out by %(u)s.", d=d.display_name, u=d.locked_by_id.name))

    def action_move(self, folder):
        """Move to another folder; the document number stays the same."""
        user = self.env.user
        if folder.is_archive:
            raise UserError(_("Use Retire to send a document to the Archive."))
        if LEVEL_RANK[folder._access_for_user(user)] < LEVEL_RANK['edit']:
            raise AccessError(_("You need Edit access on %s to move documents into it.", folder.complete_name))
        storage = self.env['dms.storage']
        for d in self:
            d._check_level('edit')
            d._check_movable()
            if d.folder_id == folder:
                continue
            old = d.folder_id.complete_name
            base = posixpath.join(folder._relative_path(), safe_name(d.code))
            for r in d.revision_ids.filtered(lambda r: r.storage_path and r.state not in ('archived', 'disposed')):
                r.sudo().storage_path = storage.move_file(r.storage_path, posixpath.join(base, posixpath.basename(r.storage_path)))
            d.sudo().folder_id = folder
            self.env['dms.log'].log('checkin', document=d, details=f"Moved: {old} → {folder.complete_name}")
            d.sudo().message_post(body=_("Moved from %(a)s to %(b)s.", a=old, b=folder.complete_name))

    def action_retire(self, reason=None):
        """Controller sends a whole document to the Archive. It can be disposed of after its retention."""
        if not self.env.user.has_group('dms.group_dms_controller'):
            raise AccessError(_("Only the Document Controller retires documents."))
        archive = self.env['dms.folder']._get_archive_folder()
        for d in self.sudo():
            d._check_movable()
            if d.state in ('archived', 'discarded'):
                continue
            # unapproved drafts are not records: drop them and remove their files
            storage = self.env['dms.storage']
            for r in d.revision_ids.filtered(lambda r: r.state in ('draft', 'rejected', 'dropped') and r.storage_path):
                storage.delete_file(r.storage_path)
                r.write({'state': 'dropped', 'storage_path': False})
            origin = d.folder_id.complete_name
            d.origin_folder_id = d.folder_id
            approved = d.revision_ids.filtered(lambda r: r.state in ('current', 'superseded'))
            if not approved:
                d.write({'state': 'discarded', 'origin_folder': origin, 'retired_reason': reason, 'locked_by_id': False})
                self.env['dms.log'].log('discard', document=d, details=f"Never approved, discarded from {origin}")
                continue
            approved._archive()
            d.write({'state': 'archived', 'origin_folder': origin, 'folder_id': archive.id,
                     'retired_reason': reason, 'locked_by_id': False})
            self.env['dms.log'].log('archive', document=d, details=f"Retired from {origin}: {reason or ''}")
            d.message_post(body=_("Retired to the Archive from %(f)s. %(r)s", f=origin, r=reason or ''))

    def action_toggle_favorite(self):
        for d in self.sudo():
            if self.env.user in d.favorite_user_ids:
                d.favorite_user_ids = [(3, self.env.uid)]
            else:
                d.favorite_user_ids = [(4, self.env.uid)]

    # ------------------------------------------------------------------
    @api.model
    def upload_new(self, folder_id, type_id, title, description, filename, data, submit=False):
        """New › Upload (slide 4). Uploading does not grant access."""
        folder = self.env['dms.folder'].browse(folder_id)
        if LEVEL_RANK[folder._access_for_user(self.env.user)] < LEVEL_RANK['edit']:
            raise AccessError(_("You need Edit access on folder %s to upload.", folder.complete_name))
        if folder.is_archive:
            raise UserError(_("The Archive folder is view-only."))
        if submit:
            folder._approval_chain(self.env['dms.document.type'].browse(type_id))
        doc = self.sudo().create({
            'name': title or os.path.splitext(filename)[0], 'folder_id': folder.id, 'type_id': type_id,
            'description': description, 'owner_id': self.env.uid,
        })
        rev = self.env['dms.revision'].sudo()._create_with_file(doc, filename, data, _("Initial upload"))
        self.env['dms.log'].log('upload', revision=rev)
        if submit:
            rev.with_user(self.env.user).action_submit()
        return doc


class DmsRevision(models.Model):
    _name = 'dms.revision'
    _description = 'DMS Document Revision'
    _order = 'id desc'

    document_id = fields.Many2one('dms.document', required=True, ondelete='cascade', index=True)
    rev_no = fields.Integer("Rev", readonly=True, default=-1, help="-1 until approved.")
    rev_label = fields.Char(compute='_compute_rev_label')
    state = fields.Selection([
        ('draft', 'Pending (not submitted)'), ('in_review', 'Pending, in review'),
        ('current', 'Current'), ('superseded', 'Superseded'), ('archived', 'Archived'),
        ('rejected', 'Rejected'), ('dropped', 'Dropped'), ('disposed', 'Discarded'),
    ], default='draft', index=True, required=True)
    author_id = fields.Many2one('res.users', default=lambda s: s.env.user, readonly=True)
    change_note = fields.Text()
    file_name = fields.Char(required=True)
    file_size = fields.Integer()
    mimetype = fields.Char()
    storage_path = fields.Char("Path on DMS Server", readonly=True)
    submitted_date = fields.Datetime(readonly=True)
    approved_date = fields.Date(readonly=True)
    cutoff_date = fields.Date("Cut-off", help="Stays on screen as superseded until this date, then archived.")
    archived_date = fields.Date(readonly=True)
    retention_end = fields.Date(compute='_compute_retention_end', store=True)
    approval_ids = fields.One2many('dms.approval', 'revision_id')
    current_level = fields.Char(compute='_compute_current_level')

    @api.depends('rev_no', 'state')
    def _compute_rev_label(self):
        for r in self:
            r.rev_label = str(r.rev_no) if r.rev_no >= 0 else '…'

    def _compute_display_name(self):
        for r in self:
            r.display_name = f"{r.document_id.display_name} · Rev {r.rev_label}"

    @api.depends('archived_date', 'document_id.type_id.retention_days')
    def _compute_retention_end(self):
        for r in self:
            r.retention_end = r.archived_date and r.archived_date + timedelta(days=r.document_id.type_id.retention_days or 0)

    def _compute_current_level(self):
        for r in self:
            r.current_level = r.approval_ids.filtered(lambda a: a.state == 'waiting')[:1].level or False

    @api.model
    def _create_with_file(self, doc, filename, data, note):
        if not data:
            raise UserError(_("The file is empty."))
        filename = safe_name(filename)
        rev = self.create({
            'document_id': doc.id, 'file_name': filename, 'file_size': len(data), 'change_note': note,
            'mimetype': mimetypes.guess_type(filename)[0] or 'application/octet-stream',
            'author_id': self.env.uid,
        })
        rel = posixpath.join(doc.folder_id._relative_path(), safe_name(doc.code),
                             f"pending_{rev.id}_{filename}")
        storage = self.env['dms.storage']
        rev.storage_path = storage.write_file(rel, data)
        # if the transaction fails afterwards, do not leave an orphan file on the server
        self.env.cr.postrollback.add(lambda: storage.delete_file(rel))
        return rev

    def _read_data(self):
        self.ensure_one()
        return self.env['dms.storage'].read_file(self.storage_path)

    # ------------------------------------------------------------------
    # Approval (slides 10-11)
    # ------------------------------------------------------------------
    def action_submit(self):
        for r in self:
            doc = r.document_id
            if r.author_id != self.env.user and not self.env.user.has_group('dms.group_dms_controller'):
                raise AccessError(_("Only the author can submit this revision."))
            if r.state != 'draft':
                raise UserError(_("Only a pending revision can be submitted."))
            folder = doc.folder_id
            chain = folder._approval_chain(doc.type_id)
            r.approval_ids.sudo().unlink()
            r.sudo().write({
                'state': 'in_review', 'submitted_date': fields.Datetime.now(),
                'approval_ids': [(0, 0, {'level': lv, 'user_id': u.id, 'sequence': i})
                                 for i, (lv, u) in enumerate(chain)],
            })
            vals = {'state': 'in_review'} if doc.state in ('draft', 'rejected') else {}
            if doc.state == 'published':
                vals['locked_by_id'] = r.author_id.id  # document locks during review
            doc.sudo().write(vals)
            self.env['dms.log'].log('submit', revision=r)
            r._notify_next_approver()

    def _notify_next_approver(self):
        self.ensure_one()
        nxt = self.approval_ids.filtered(lambda a: a.state == 'waiting').sorted('sequence')[:1]
        if nxt:
            self.document_id.sudo().activity_schedule(
                'mail.mail_activity_data_todo', user_id=nxt.user_id.id,
                summary=_("DMS %(lv)s approval: %(rev)s", lv=nxt.level.upper(), rev=self.display_name),
                note=self.change_note or '')

    def _approval_for(self, user):
        self.ensure_one()
        nxt = self.approval_ids.filtered(lambda a: a.state == 'waiting').sorted('sequence')[:1]
        if self.state != 'in_review' or not nxt:
            raise UserError(_("This revision is not waiting for approval."))
        if nxt.user_id != user:
            raise AccessError(_("Waiting for %(lv)s approval by %(u)s.", lv=nxt.level.upper(), u=nxt.user_id.name))
        return nxt

    def _close_activities(self, feedback):
        acts = self.document_id.sudo().activity_ids.filtered(
            lambda a: a.user_id == self.env.user and a.summary and 'DMS' in a.summary)
        if acts:
            acts.action_feedback(feedback=feedback)

    def action_approve(self, comment=None):
        self.ensure_one()
        line = self._approval_for(self.env.user)
        line.sudo().write({'state': 'approved', 'comment': comment, 'date': fields.Datetime.now()})
        self._close_activities(_("Approved"))
        self.env['dms.log'].log('approve', revision=self, details=f"{line.level.upper()}: {comment or ''}")
        if self.approval_ids.filtered(lambda a: a.state == 'waiting'):
            self._notify_next_approver()
        else:
            self.sudo()._publish()

    def action_refuse(self, comment):
        self.ensure_one()
        if not comment:
            raise UserError(_("Give a reason for refusing."))
        line = self._approval_for(self.env.user)
        line.sudo().write({'state': 'refused', 'comment': comment, 'date': fields.Datetime.now()})
        self.approval_ids.filtered(lambda a: a.state == 'waiting').sudo().write({'state': 'skipped'})
        self._close_activities(_("Refused: %s", comment))
        self.env['dms.log'].log('refuse', revision=self, details=f"{line.level.upper()}: {comment}")
        doc = self.document_id.sudo()
        if doc.state == 'in_review':  # first upload → back to author
            self.sudo().state = 'rejected'
            doc.state = 'rejected'
        else:  # pending revision is dropped, document unlocked
            self.sudo().state = 'dropped'
            doc.write({'locked_by_id': False, 'locked_date': False})
        doc.message_post(
            body=_("%(rev)s refused at %(lv)s by %(u)s: %(c)s", rev=self.display_name,
                   lv=line.level.upper(), u=self.env.user.name, c=comment),
            partner_ids=self.author_id.partner_id.ids, subtype_xmlid='mail.mt_comment')

    def _publish(self):
        self.ensure_one()
        doc = self.document_id
        prev = doc.revision_ids.filtered(lambda r: r.state == 'current')
        rev_no = max(doc.revision_ids.mapped('rev_no') + [-1]) + 1
        today = fields.Date.context_today(self)
        days = int(self.env['ir.config_parameter'].sudo().get_param('dms.cutoff_days') or 30)
        prev.write({'state': 'superseded', 'cutoff_date': today + timedelta(days=days)})
        new_rel = posixpath.join(posixpath.dirname(self.storage_path), f"Rev{rev_no}_{self.file_name}")
        self.write({
            'state': 'current', 'rev_no': rev_no, 'approved_date': today,
            'storage_path': self.env['dms.storage'].move_file(self.storage_path, new_rel),
        })
        doc.write({'state': 'published', 'locked_by_id': False, 'locked_date': False})
        self.env['dms.log'].log('publish', revision=self)
        doc.message_post(body=_("Rev %s approved and published.", rev_no),
                         partner_ids=self.author_id.partner_id.ids, subtype_xmlid='mail.mt_comment')

    def action_set_cutoff(self, date):
        """An approver can bring the cut-off forward for an urgent change (slide 9)."""
        self.ensure_one()
        user = self.env.user
        doc = self.document_id
        is_approver = user in (doc.folder_id.reviewer_id | doc.folder_id.approver_id | doc.folder_id.final_approver_id)
        if not (is_approver or user.has_group('dms.group_dms_controller')):
            raise AccessError(_("Only an approver or the Document Controller can change the cut-off."))
        if self.state != 'superseded':
            raise UserError(_("Cut-off applies to superseded revisions."))
        date = fields.Date.to_date(date)
        if date > self.cutoff_date:
            raise UserError(_("The cut-off can only be brought forward."))
        old = self.cutoff_date
        self.sudo().cutoff_date = date
        self.env['dms.log'].log('cutoff', revision=self, details=f"{old} → {date}")
        if date <= fields.Date.context_today(self):
            self.sudo()._archive()

    # ------------------------------------------------------------------
    def _archive(self):
        archive = self.env['dms.folder']._get_archive_folder()
        for r in self:
            doc = r.document_id
            rel = posixpath.join(archive._relative_path(), doc.folder_id._relative_path(),
                                 safe_name(doc.code), posixpath.basename(r.storage_path))
            r.write({'state': 'archived', 'archived_date': fields.Date.context_today(r),
                     'storage_path': self.env['dms.storage'].move_file(r.storage_path, rel)})
            self.env['dms.log'].log('archive', revision=r)

    @api.model
    def _cron_cutoff(self):
        due = self.search([('state', '=', 'superseded'), ('cutoff_date', '<=', fields.Date.context_today(self))])
        due._archive()


class DmsFolderApprovers(models.Model):
    _inherit = 'dms.folder'

    def _resolve(self, field):
        """Approver from this folder or the nearest parent that has one."""
        self.ensure_one()
        f = self
        while f:
            if f[field]:
                return f[field]
            f = f.parent_id
        return self.env['res.users']

    def _approval_chain(self, doc_type):
        """[(level, user)] for slide 10; raises when an approver is missing."""
        self.ensure_one()
        chain = [('l1', self._resolve('reviewer_id')), ('l2', self._resolve('approver_id'))]
        if doc_type.requires_final_approval:
            chain.append(('l3', self._resolve('final_approver_id')))
        missing = [lv.upper() for lv, u in chain if not u]
        if missing:
            raise UserError(_("Folder %(f)s has no %(lv)s approver. Ask the Document Controller to set it.",
                              f=self.complete_name, lv=', '.join(missing)))
        return chain

    def _root_code(self):
        f = self
        while f.parent_id and not f.code:
            f = f.parent_id
        return f.code or safe_name(f.name)[:3].upper()


class DmsApproval(models.Model):
    _name = 'dms.approval'
    _description = 'DMS Approval Step'
    _order = 'sequence, id'

    revision_id = fields.Many2one('dms.revision', required=True, ondelete='cascade', index=True)
    document_id = fields.Many2one(related='revision_id.document_id', store=True)
    sequence = fields.Integer()
    level = fields.Selection([('l1', 'L1 Reviewer'), ('l2', 'L2 Approver'), ('l3', 'L3 Final Authority')], required=True)
    user_id = fields.Many2one('res.users', required=True, index=True)
    state = fields.Selection([('waiting', 'Waiting'), ('approved', 'Approved'),
                              ('refused', 'Refused'), ('skipped', 'Skipped')], default='waiting')
    comment = fields.Text()
    date = fields.Datetime()
