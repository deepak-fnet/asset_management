import time
from urllib.parse import quote

from odoo import fields, http, _
from odoo.exceptions import AccessDenied, AccessError, UserError
from odoo.http import request

from ..models.dms_folder import ACCESS_LEVELS, LEVEL_RANK



def _unlocked():
    # The separate DMS login was removed: being logged in to Odoo is enough.
    return True


class DmsLocked(UserError):
    """The DMS login expired; the client shows the DMS login screen again."""


def _require_unlock():
    if not _unlocked():
        raise DmsLocked(_("Your DMS session has expired. Log in to the DMS again."))


def _env():
    _require_unlock()
    if not request.env.user.has_group('dms.group_dms_user'):
        raise AccessError(_("You are not a DMS user."))
    return request.env


def _user_dict(u):
    return {'id': u.id, 'name': u.name} if u else False


class DmsController(http.Controller):

    # ------------------------------------------------------------------
    # DMS login (separate unlock on top of the Odoo session)
    # ------------------------------------------------------------------
    @http.route('/dms/session', type='jsonrpc', auth='user')
    def session(self):
        user = request.env.user
        return {'unlocked': _unlocked(), 'login': user.login, 'name': user.name,
                'is_dms_user': user.has_group('dms.group_dms_user')}

    @http.route('/dms/login', type='jsonrpc', auth='user')
    def login(self, login, password):
        user = request.env.user
        if not user.has_group('dms.group_dms_user'):
            return {'ok': False, 'error': _("You do not have DMS access. Ask the Document Controller.")}
        if (login or '').strip().lower() != (user.login or '').lower():
            return {'ok': False, 'error': _("User name or password is wrong.")}
        try:
            user._check_credentials({'type': 'password', 'login': login, 'password': password},
                                    {'interactive': True})
        except AccessDenied:
            return {'ok': False, 'error': _("User name or password is wrong.")}
        minutes = int(request.env['ir.config_parameter'].sudo().get_param('dms.session_minutes') or 60)
        request.session['dms_unlock_until'] = time.time() + minutes * 60
        request.session['dms_unlock_uid'] = user.id
        request.env['dms.log'].log('login')
        return {'ok': True}

    @http.route('/dms/logout', type='jsonrpc', auth='user')
    def logout(self):
        request.session.pop('dms_unlock_until', None)
        request.session.pop('dms_unlock_uid', None)
        return True

    # ------------------------------------------------------------------
    @http.route('/dms/bootstrap', type='jsonrpc', auth='user')
    def bootstrap(self):
        env = _env()
        user = env.user
        Folder = env['dms.folder']
        folders = Folder._visible_folders(user)
        counts = dict(env['dms.document'].sudo()._read_group(
            [('folder_id', 'in', folders.ids), ('state', 'not in', ('discarded',))], ['folder_id'], ['__count']))
        out = []
        for f in folders.sorted(lambda f: (f.is_archive, f.sequence, f.name.lower())):
            level = f._access_for_user(user)
            out.append({
                'id': f.id, 'name': f.name, 'code': f.code, 'parent_id': f.parent_id.id if f.parent_id in folders else False,
                'level': level or False, 'is_archive': f.is_archive, 'color': f.color,
                'doc_count': counts.get(f, 0), 'department': f.department_id.name or False,
                'created_by': f.create_uid.name,
                'can_share': f._can_share(user),
                'can_rename': f._can_rename(user),
                'can_delete': f._can_request_delete(user),
                'delete_requested': f.delete_state == 'requested',
            })
        return {
            'theme': env['ir.config_parameter'].sudo().get_param('dms.theme') or 'navy',
            'user': {'id': user.id, 'name': user.name, 'is_controller': user.has_group('dms.group_dms_controller')},
            'folders': out,
            'types': [{'id': t.id, 'name': t.name, 'code': t.code, 'l3': t.requires_final_approval}
                      for t in env['dms.document.type'].search([])],
            'levels': ACCESS_LEVELS,
            'counts': self._counts(env),
        }

    def _counts(self, env):
        user = env.user
        Approval = env['dms.approval'].sudo()
        waiting = Approval.search([('state', '=', 'waiting'), ('revision_id.state', '=', 'in_review')])
        mine = waiting.filtered(lambda a: a == a.revision_id.approval_ids.filtered(lambda x: x.state == 'waiting').sorted('sequence')[:1]
                                and a.user_id == user)
        Doc = env['dms.document'].sudo()
        return {
            'approvals': len(mine),
            'checked_out': Doc.search_count([('locked_by_id', '=', user.id)]),
            'my_uploads': Doc.search_count([('owner_id', '=', user.id), ('state', 'in', ('draft', 'in_review', 'rejected'))]),
            'starred': Doc.search_count([('favorite_user_ids', 'in', user.id)]),
            'folder_deletes': env['dms.folder'].sudo().search_count([('delete_state', '=', 'requested')])
            if user.has_group('dms.group_dms_controller') else 0,
            'disposals': len(env['dms.disposal'].sudo().search([('state', '=', 'submitted')]).filtered(
                lambda d: (d.dept_head_id == user and not d.head_approved_id)
                or (user.has_group('dms.group_dms_controller') and not d.controller_approved_id))),
        }

    # ------------------------------------------------------------------
    def _doc_card(self, d, user):
        level = d._access_for_user(user)
        cur = d.current_revision_id
        pending = d.revision_ids.filtered(lambda r: r.state in ('draft', 'in_review'))[:1]
        show = cur or pending or d.revision_ids[:1]
        return {
            'id': d.id, 'code': d.code, 'name': d.name, 'state': d.state,
            'folder_id': d.folder_id.id, 'folder': d.folder_id.complete_name,
            'type': d.type_id.name, 'rev': cur.rev_no if cur else None,
            'file_name': show.file_name or '', 'mimetype': show.mimetype or '', 'size': show.file_size or 0,
            'owner': _user_dict(d.owner_id), 'locked_by': _user_dict(d.locked_by_id),
            'pending_state': pending.state if pending else False,
            'level': level or False, 'starred': user in d.favorite_user_ids,
            'updated': fields.Datetime.to_string(d.write_date),
            'approved_date': fields.Date.to_string(cur.approved_date) if cur else False,
        }

    def _can_see_doc(self, d, user):
        if d._blocked_for(user):
            return False
        if d._access_for_user(user):
            return True
        if d.owner_id == user or user in d.revision_ids.approval_ids.user_id:
            return True
        return False

    @http.route('/dms/documents', type='jsonrpc', auth='user')
    def documents(self, folder_id=None, view='folder', search=None):
        env = _env()
        user = env.user
        Doc = env['dms.document'].sudo()
        domain = [('state', '!=', 'discarded')]
        if search:
            domain += ['|', '|', ('name', 'ilike', search), ('code', 'ilike', search), ('description', 'ilike', search)]
        if view == 'folder' and folder_id:
            domain += [('folder_id', '=', int(folder_id))]
        elif view == 'starred':
            domain += [('favorite_user_ids', 'in', user.id)]
        elif view == 'checked_out':
            domain += [('locked_by_id', '=', user.id)]
        elif view == 'my_uploads':
            domain += [('owner_id', '=', user.id)]
        elif view == 'approvals':
            revs = env['dms.revision'].sudo().search([('state', '=', 'in_review'), ('approval_ids.user_id', '=', user.id)])
            revs = revs.filtered(lambda r: r.approval_ids.filtered(lambda a: a.state == 'waiting').sorted('sequence')[:1].user_id == user)
            domain += [('id', 'in', revs.document_id.ids)]
        elif view == 'recent':
            pass
        docs = Doc.search(domain, limit=500 if view != 'recent' else 30, order='write_date desc')
        # documents that are only drafts/rejected are private to author, approvers & controller
        controller = user.has_group('dms.group_dms_controller')
        result = []
        for d in docs:
            if not self._can_see_doc(d, user):
                continue
            if d.state in ('draft', 'rejected', 'in_review') and not d.current_revision_id and not controller \
                    and d.owner_id != user and user not in d.revision_ids.approval_ids.user_id:
                continue
            result.append(self._doc_card(d, user))
        return result

    @http.route('/dms/document', type='jsonrpc', auth='user')
    def document(self, doc_id):
        env = _env()
        user = env.user
        d = env['dms.document'].sudo().browse(int(doc_id)).exists()
        if not d or not self._can_see_doc(d, user):
            raise AccessError(_("You have no access to this document."))
        controller = user.has_group('dms.group_dms_controller')
        folder = d.folder_id
        is_approver = user in (folder.reviewer_id | folder.approver_id | folder.final_approver_id)
        sel = dict(env['dms.revision']._fields['state'].selection)
        revs = []
        for r in d._visible_revisions(user):
            nxt = r.approval_ids.filtered(lambda a: a.state == 'waiting').sorted('sequence')[:1]
            revs.append({
                'id': r.id, 'rev': r.rev_label, 'state': r.state, 'state_label': sel.get(r.state),
                'file_name': r.file_name, 'mimetype': r.mimetype, 'size': r.file_size,
                'author': _user_dict(r.author_id), 'note': r.change_note,
                'approved_date': fields.Date.to_string(r.approved_date) if r.approved_date else False,
                'cutoff_date': fields.Date.to_string(r.cutoff_date) if r.cutoff_date else False,
                'submitted_date': fields.Datetime.to_string(r.submitted_date) if r.submitted_date else False,
                'can_preview': self._can_open(r, user, False),
                'can_download': self._can_open(r, user, True),
                'can_submit': r.state == 'draft' and (r.author_id == user or controller),
                'my_turn': bool(nxt and nxt.user_id == user and r.state == 'in_review'),
                'can_cutoff': r.state == 'superseded' and (is_approver or controller),
                'approvals': [{'level': a.level.upper(), 'user': a.user_id.name, 'user_id': a.user_id.id,
                               'state': a.state, 'comment': a.comment,
                               'date': fields.Datetime.to_string(a.date) if a.date else False}
                              for a in r.approval_ids.sorted('sequence')],
            })
        card = self._doc_card(d, user)
        card.update({
            'description': d.description or '',
            'revisions': revs,
            'can_checkout': card['level'] == 'edit' and d.state == 'published' and not d.locked_by_id
                            and not d.revision_ids.filtered(lambda r: r.state in ('draft', 'in_review')),
            'can_checkin': d.locked_by_id == user and not d.revision_ids.filtered(lambda r: r.state == 'in_review'),
            'can_unlock': bool(d.locked_by_id) and (d.locked_by_id == user or controller)
                          and not d.revision_ids.filtered(lambda r: r.state == 'in_review'),
            'can_manage_access': card['level'] == 'edit',
            'can_move': card['level'] == 'edit' and d.state not in ('archived', 'discarded', 'in_review')
                        and not d.locked_by_id and not d.revision_ids.filtered(lambda r: r.state == 'in_review'),
            'can_retire': controller and d.state in ('published', 'draft')
                          and not d.locked_by_id and not d.revision_ids.filtered(lambda r: r.state == 'in_review'),
            'origin_folder': d.origin_folder or False,
            'approvers': {'l1': folder._resolve('reviewer_id').name, 'l2': folder._resolve('approver_id').name,
                          'l3': folder._resolve('final_approver_id').name if d.type_id.requires_final_approval else False},
            'access': [{'id': a.id, 'who': a.user_id.name or a.group_id.name, 'is_group': bool(a.group_id),
                        'level': a.level} for a in d.access_ids] if card['level'] == 'edit' else [],
            'log': [{'date': fields.Datetime.to_string(l.date), 'user': l.user_id.name,
                     'action': dict(l._fields['action'].selection)[l.action], 'details': l.details or ''}
                    for l in env['dms.log'].sudo().search([('document_id', '=', d.id)], limit=40)]
                   if (controller or d.owner_id == user) else [],
        })
        return card

    def _can_open(self, rev, user, download):
        d = rev.document_id
        if d._blocked_for(user):
            return False
        level = d._access_for_user(user)
        controller = user.has_group('dms.group_dms_controller')
        involved = rev.author_id == user or user in rev.approval_ids.user_id
        if rev.state in ('disposed', 'dropped') or not rev.storage_path:
            return False
        if rev.state in ('draft', 'in_review', 'rejected'):
            ok = controller or involved
        elif rev.state == 'archived':
            ok = controller or d.owner_id == user
            if download:
                return controller
        else:
            ok = bool(level) or involved
        if not ok:
            return False
        if not download:
            return True
        return controller or involved or LEVEL_RANK[level] >= LEVEL_RANK['download']

    # ------------------------------------------------------------------
    @http.route('/dms/file/<int:rev_id>', type='http', auth='user', methods=['GET'])
    def file(self, rev_id, download=None, **kw):
        env = _env()
        user = env.user
        rev = env['dms.revision'].sudo().browse(rev_id).exists()
        want_dl = bool(download)
        if not rev or not self._can_open(rev, user, want_dl):
            raise AccessError(_("You have no %s access to this file.", 'download' if want_dl else 'view'))
        if not want_dl:
            # the original bytes are sent inline only for media the browser plays itself;
            # documents are always shown as rendered pages, so View-only users never get the file
            kind = rev._preview_kind()
            if kind not in ('image', 'video', 'audio') and not (kind == 'pdf' and self._can_open(rev, user, True)):
                raise AccessError(_("This file is shown page by page; it cannot be opened directly."))
        data = rev._read_data()
        env['dms.log'].log('download' if want_dl else 'view', revision=rev)
        disp = 'attachment' if want_dl else 'inline'
        headers = [
            ('Content-Type', rev.mimetype or 'application/octet-stream'),
            ('Content-Length', len(data)),
            ('Content-Disposition', f"{disp}; filename*=UTF-8''{quote(rev.file_name)}"),
            ('Cache-Control', 'no-store'),
            ('X-Content-Type-Options', 'nosniff'),
        ]
        return request.make_response(data, headers)

    @http.route('/dms/file/<int:rev_id>/pdf', type='http', auth='user', methods=['GET'])
    def file_pdf(self, rev_id, **kw):
        """The rendered PDF, only for people allowed to download."""
        env = _env()
        rev = env['dms.revision'].sudo().browse(rev_id).exists()
        if not rev or not self._can_open(rev, env.user, True):
            raise AccessError(_("You need View and Download access for this."))
        pdf = rev._preview_pdf()
        env['dms.log'].log('view', revision=rev)
        return self._bytes(pdf, 'application/pdf')

    # ------------------------------------------------------------------
    # Viewer: everything a person with View access may receive
    # ------------------------------------------------------------------
    def _viewable(self, env, rev_id):
        rev = env['dms.revision'].sudo().browse(int(rev_id)).exists()
        if not rev or not self._can_open(rev, env.user, False):
            raise AccessError(_("You have no view access to this file."))
        return rev

    def _bytes(self, data, ctype):
        return request.make_response(data, [
            ('Content-Type', ctype), ('Content-Length', len(data)),
            ('Content-Disposition', 'inline'),
            ('Cache-Control', 'private, no-store'), ('X-Content-Type-Options', 'nosniff'),
            ('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline' https://fonts.googleapis.com; "
                                        "font-src https://fonts.gstatic.com; img-src data:"),
        ])

    @http.route('/dms/view/info', type='jsonrpc', auth='user')
    def view_info(self, rev_id):
        env = _env()
        rev = self._viewable(env, rev_id)
        kind = rev._preview_kind()
        out = {'kind': kind, 'can_download': self._can_open(rev, env.user, True), 'name': rev.file_name}
        if kind in ('pdf', 'doc'):
            out['pages'] = rev._page_count()
        elif kind == 'text':
            out['text'] = rev._text()
        elif kind == 'zip':
            out['entries'] = rev._zip_listing()
        env['dms.log'].log('view', revision=rev)
        return out

    @http.route('/dms/view/<int:rev_id>/page/<int:page>', type='http', auth='user', methods=['GET'])
    def view_page(self, rev_id, page, **kw):
        env = _env()
        return self._bytes(self._viewable(env, rev_id)._page_png(page), 'image/png')

    @http.route('/dms/view/<int:rev_id>/sheet', type='http', auth='user', methods=['GET'])
    def view_sheet(self, rev_id, **kw):
        env = _env()
        return self._bytes(self._viewable(env, rev_id)._sheet_html(), 'text/html; charset=utf-8')

    @http.route('/dms/view/<int:rev_id>/image', type='http', auth='user', methods=['GET'])
    def view_image(self, rev_id, **kw):
        env = _env()
        return self._bytes(self._viewable(env, rev_id)._image_png(), 'image/png')

    @http.route('/dms/view/capture', type='jsonrpc', auth='user')
    def view_capture(self, rev_id, how):
        """A screenshot / print / copy attempt while a read-only file was open."""
        env = _env()
        rev = self._viewable(env, rev_id)
        env['dms.log'].log('view', revision=rev, details=f"Capture attempt: {how}")
        return True

    @http.route('/dms/upload', type='http', auth='user', methods=['POST'], csrf=True)
    def upload(self, folder_id, type_id, file, title=None, description=None, submit=None, **kw):
        return self._json_call(lambda env: {'doc_id': env['dms.document'].upload_new(
            int(folder_id), int(type_id), title, description, file.filename, file.read(),
            submit=submit in ('1', 'true')).id})

    @http.route('/dms/checkin', type='http', auth='user', methods=['POST'], csrf=True)
    def checkin(self, doc_id, file, note=None, submit=None, **kw):
        def run(env):
            doc = env['dms.document'].browse(int(doc_id))
            rev = doc.action_checkin(file.filename, file.read(), note)
            if submit in ('1', 'true'):
                rev.action_submit()
            return {'rev_id': rev.id}
        return self._json_call(run)

    def _json_call(self, fn):
        try:
            env = _env()
            res = fn(env)
            return request.make_json_response({'ok': True, **res})
        except (UserError, AccessError, AccessDenied) as e:
            request.env.cr.rollback()
            return request.make_json_response({'ok': False, 'error': str(e.args[0] if e.args else e)})

    @http.route('/dms/action', type='jsonrpc', auth='user')
    def action(self, action, doc_id=None, rev_id=None, comment=None, date=None):
        env = _env()
        doc = env['dms.document'].browse(int(doc_id)) if doc_id else None
        rev = env['dms.revision'].browse(int(rev_id)) if rev_id else None
        if action == 'checkout':
            doc.action_checkout()
        elif action == 'unlock':
            doc.action_unlock(force=env.user != doc.sudo().locked_by_id)
        elif action == 'star':
            if not self._can_see_doc(doc.sudo(), env.user):
                raise AccessError(_("No access."))
            doc.action_toggle_favorite()
        elif action == 'submit':
            rev.action_submit()
        elif action == 'approve':
            rev.action_approve(comment)
        elif action == 'refuse':
            rev.action_refuse(comment)
        elif action == 'cutoff':
            rev.action_set_cutoff(date)
        elif action == 'resubmit_new':
            raise UserError(_("Upload the corrected file as a new revision."))
        else:
            raise UserError(_("Unknown action %s", action))
        return {'counts': self._counts(env)}

    # ------------------------------------------------------------------
    # Access management: the Controller, or anyone with Edit on the folder/file
    # ------------------------------------------------------------------
    def _share_target(self, env, folder_id=None, doc_id=None):
        """Return (folder, document) and check the current user may share it."""
        user = env.user
        if folder_id:
            folder = env['dms.folder'].sudo().browse(int(folder_id)).exists()
            if not folder or not folder._can_share(user):
                raise AccessError(_("You need Edit access on this folder to share it."))
            return folder, env['dms.document']
        doc = env['dms.document'].sudo().browse(int(doc_id)).exists()
        if not doc or doc._access_for_user(user) != 'edit':
            raise AccessError(_("You need Edit access on this file to share it."))
        return env['dms.folder'], doc

    def _line(self, a):
        return {'id': a.id, 'who': a.user_id.name or a.group_id.name, 'user_id': a.user_id.id,
                'is_group': bool(a.group_id), 'level': a.level, 'by': a.granted_by_id.name,
                'date': fields.Datetime.to_string(a.create_date)}

    @http.route('/dms/access', type='jsonrpc', auth='user')
    def access(self, folder_id=None, doc_id=None):
        """Everyone who can reach this folder/file, and where their access comes from."""
        env = _env()
        folder, doc = self._share_target(env, folder_id, doc_id)
        Access = env['dms.access'].sudo()
        direct = Access.search([('folder_id', '=', folder.id)] if folder else [('document_id', '=', doc.id)])
        inherited, groups = [], []
        base_folder = folder or doc.folder_id
        parents = env['dms.folder'].sudo().search([('id', 'parent_of', base_folder.id)])
        if doc:
            parents |= base_folder
        else:
            parents -= base_folder
        for a in Access.search([('folder_id', 'in', parents.ids)]):
            inherited.append({**self._line(a), 'from': a.folder_id.complete_name})
        chain = env['dms.folder'].sudo().search([('id', 'parent_of', base_folder.id)])
        for g in env['dms.group'].sudo().search([('folder_ids', 'in', chain.ids)]):
            via = (g.folder_ids & chain)[:1].complete_name
            groups.append({'id': g.id, 'name': g.name, 'level': g.level, 'via': via,
                           'members': [u.name for u in g.user_ids]})
        me = env.user
        my_level = (folder._access_for_user(me) if folder else doc._access_for_user(me))
        return {'direct': [self._line(a) for a in direct], 'inherited': inherited, 'groups': groups,
                'my_level': my_level, 'is_controller': me.has_group('dms.group_dms_controller')}

    @http.route('/dms/access/grant', type='jsonrpc', auth='user')
    def grant(self, level, folder_id=None, doc_id=None, user_id=None, group_id=None):
        env = _env()
        folder, doc = self._share_target(env, folder_id, doc_id)
        if level not in dict(ACCESS_LEVELS):
            raise UserError(_("Unknown access level."))
        if not user_id:
            raise UserError(_("Pick a person."))
        Access = env['dms.access'].sudo()
        target = [('folder_id', '=', folder.id)] if folder else [('document_id', '=', doc.id)]
        Access.search(target + [('user_id', '=', int(user_id))]).unlink()
        Access.create({'folder_id': folder.id or False, 'document_id': doc.id or False,
                       'user_id': int(user_id), 'level': level})
        u = env['res.users'].sudo().browse(int(user_id))
        if not u.has_group('dms.group_dms_user'):
            env.ref('dms.group_dms_user').sudo().write({'user_ids': [(4, u.id)]})
        return True

    @http.route('/dms/access/revoke', type='jsonrpc', auth='user')
    def revoke(self, access_id):
        env = _env()
        a = env['dms.access'].sudo().browse(int(access_id)).exists()
        if a:
            self._share_target(env, a.folder_id.id or None, a.document_id.id or None)
            a.unlink()
        return True

    # ------------------------------------------------------------------
    # File access map: everyone who reaches one file, and from where
    # ------------------------------------------------------------------
    @http.route('/dms/file_access', type='jsonrpc', auth='user')
    def file_access(self, doc_id):
        env = _env()
        _folder, doc = self._share_target(env, None, doc_id)
        Access = env['dms.access'].sudo()
        chain = env['dms.folder'].sudo().search([('id', 'parent_of', doc.folder_id.id)])
        sources = {}  # user -> list of "where from"
        for a in Access.search([('folder_id', 'in', chain.ids), ('user_id', '!=', False)]):
            sources.setdefault(a.user_id, []).append(
                {'kind': 'folder', 'label': a.folder_id.complete_name, 'level': a.level})
        for g in env['dms.group'].sudo().search([('folder_ids', 'in', chain.ids)]):
            for u in g.user_ids:
                sources.setdefault(u, []).append({'kind': 'group', 'label': g.name, 'level': g.level})
        file_lines = {a.user_id: a for a in doc.access_ids if a.user_id}
        for u in file_lines:
            sources.setdefault(u, [])
        sources.setdefault(doc.owner_id, [])  # the owner can be taken off too
        rows = []
        for u, src in sources.items():
            line = file_lines.get(u)
            best = max(src, key=lambda x: LEVEL_RANK[x['level']]) if src else None
            rows.append({
                'user_id': u.id, 'name': u.name,
                'level': doc._access_for_user(u) or False,
                'from': best,
                'file_level': line.level if line else False,
                'line_id': line.id if line else False,
                'is_owner': u == doc.owner_id,
            })
        rows.sort(key=lambda r: (r['file_level'] == 'none', r['name']))
        return {'rows': rows, 'owner': doc.owner_id.name, 'folder': doc.folder_id.complete_name}

    @http.route('/dms/file_access/set', type='jsonrpc', auth='user')
    def file_access_set(self, doc_id, user_id, level):
        """level: view/download/edit sets a file-only level, 'none' removes the person from this file,
        False goes back to what the folder gives."""
        env = _env()
        _folder, doc = self._share_target(env, None, doc_id)
        if level and level not in dict(ACCESS_LEVELS) and level != 'none':
            raise UserError(_("Unknown access level."))
        Access = env['dms.access'].sudo()
        Access.search([('document_id', '=', doc.id), ('user_id', '=', int(user_id))]).unlink()
        if level:
            Access.create({'document_id': doc.id, 'user_id': int(user_id), 'level': level})
        return True

    # ------------------------------------------------------------------
    # Folder deletion: requested by an Edit user, approved by the Controller
    # ------------------------------------------------------------------
    @http.route('/dms/folder/delete_request', type='jsonrpc', auth='user')
    def folder_delete_request(self, folder_id, reason=None):
        env = _env()
        folder = env['dms.folder'].sudo().browse(int(folder_id)).exists()
        folder._request_delete(env.user, reason)
        if env.user.has_group('dms.group_dms_controller'):
            folder._decide_delete(env.user, True)
            return {'deleted': True}
        return {'deleted': False}

    @http.route('/dms/folder/delete_decide', type='jsonrpc', auth='user')
    def folder_delete_decide(self, folder_id, approve, note=None):
        env = _env()
        if not env.user.has_group('dms.group_dms_controller'):
            raise AccessError(_("Only the Document Controller approves folder deletion."))
        env['dms.folder'].sudo().browse(int(folder_id)).exists()._decide_delete(env.user, approve, note)
        return True

    @http.route('/dms/folder/delete_requests', type='jsonrpc', auth='user')
    def folder_delete_requests(self):
        env = _env()
        if not env.user.has_group('dms.group_dms_controller'):
            raise AccessError(_("Only the Document Controller sees deletion requests."))
        out = []
        for f in env['dms.folder'].sudo().search([('delete_state', '=', 'requested')]):
            out.append({'id': f.id, 'name': f.complete_name, 'by': f.delete_requested_by_id.name,
                        'reason': f.delete_reason or '', 'docs': f._live_document_count(),
                        'parent_id': f.parent_id.id,
                        'items': [{'id': d.id, 'code': d.code, 'name': d.name, 'state': d.state,
                                   'folder': d.folder_id.complete_name} for d in f._contents()],
                        'subfolders': len(f.search([('id', 'child_of', f.id)])) - 1,
                        'date': fields.Datetime.to_string(f.delete_requested_on)})
        return out

    # ------------------------------------------------------------------
    # Move / retire documents, empty a folder
    # ------------------------------------------------------------------
    @http.route('/dms/document/move', type='jsonrpc', auth='user')
    def document_move(self, doc_ids, folder_id):
        env = _env()
        folder = env['dms.folder'].sudo().browse(int(folder_id)).exists()
        docs = env['dms.document'].browse([int(i) for i in doc_ids]).exists()
        docs.action_move(folder)
        return {'counts': self._counts(env)}

    @http.route('/dms/document/retire', type='jsonrpc', auth='user')
    def document_retire(self, doc_ids, reason=None):
        env = _env()
        env['dms.document'].browse([int(i) for i in doc_ids]).exists().action_retire(reason)
        return {'counts': self._counts(env)}

    @http.route('/dms/principals', type='jsonrpc', auth='user')
    def principals(self, term=''):
        env = _env()
        users = env['res.users'].sudo().search(
            [('share', '=', False), '|', ('name', 'ilike', term), ('login', 'ilike', term)], limit=8)
        return [{'kind': 'user', 'id': u.id, 'name': u.name, 'sub': u.login} for u in users]

    # ------------------------------------------------------------------
    # Folders: anyone with Edit on a folder can add sub-folders in it
    # ------------------------------------------------------------------
    @http.route('/dms/folder/create', type='jsonrpc', auth='user')
    def folder_create(self, name, parent_id=None):
        env = _env()
        user = env.user
        name = (name or '').strip()
        if not name:
            raise UserError(_("Give the folder a name."))
        Folder = env['dms.folder'].sudo()
        parent = Folder.browse(int(parent_id)) if parent_id else Folder
        if parent:
            if parent.is_archive or parent._access_for_user(user) != 'edit':
                raise AccessError(_("You need Edit access on %s to create a folder in it.", parent.complete_name))
        elif not user.has_group('dms.group_dms_controller'):
            raise AccessError(_("Only the Document Controller creates top-level folders."))
        if Folder.search_count([('parent_id', '=', parent.id or False), ('name', '=ilike', name)]):
            raise UserError(_("A folder named %s already exists here.", name))
        folder = Folder.create({'name': name, 'parent_id': parent.id or False})
        env['dms.log'].log('access_grant', folder=folder, details=f"Folder created by {user.name}")
        return {'id': folder.id}

    @http.route('/dms/folder/rename', type='jsonrpc', auth='user')
    def folder_rename(self, folder_id, name):
        env = _env()
        folder = env['dms.folder'].sudo().browse(int(folder_id))
        user = env.user
        if not folder._can_rename(user):
            raise AccessError(_("Only the Document Controller or the folder creator can rename it."))
        folder.name = (name or '').strip() or folder.name
        return True

    # ------------------------------------------------------------------
    # DMS groups (Controller)
    # ------------------------------------------------------------------
    def _check_controller(self, env):
        if not env.user.has_group('dms.group_dms_controller'):
            raise AccessError(_("Only the Document Controller manages groups."))

    @http.route('/dms/groups', type='jsonrpc', auth='user')
    def groups(self):
        env = _env()
        self._check_controller(env)
        return [{
            'id': g.id, 'name': g.name, 'level': g.level, 'description': g.description or '',
            'folders': [{'id': f.id, 'name': f.complete_name} for f in g.folder_ids],
            'users': [{'id': u.id, 'name': u.name, 'login': u.login} for u in g.user_ids],
        } for g in env['dms.group'].sudo().search([])]

    @http.route('/dms/group/save', type='jsonrpc', auth='user')
    def group_save(self, name, level, folder_ids, user_ids, group_id=None, description=None):
        env = _env()
        self._check_controller(env)
        if not (name or '').strip():
            raise UserError(_("Give the group a name."))
        vals = {'name': name.strip(), 'level': level, 'description': description,
                'folder_ids': [(6, 0, [int(i) for i in folder_ids])],
                'user_ids': [(6, 0, [int(i) for i in user_ids])]}
        Group = env['dms.group'].sudo()
        if group_id:
            Group.browse(int(group_id)).write(vals)
        else:
            group_id = Group.create(vals).id
        return {'id': group_id}

    @http.route('/dms/group/delete', type='jsonrpc', auth='user')
    def group_delete(self, group_id):
        env = _env()
        self._check_controller(env)
        env['dms.group'].sudo().browse(int(group_id)).unlink()
        return True
