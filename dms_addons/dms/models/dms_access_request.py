from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

from .dms_folder import ACCESS_LEVELS


class DmsAccessRequest(models.Model):
    """Someone asks for access to a document they can see in "All documents" but cannot open."""
    _name = 'dms.access.request'
    _description = 'DMS Access Request'
    _order = 'id desc'

    document_id = fields.Many2one('dms.document', required=True, ondelete='cascade', index=True)
    user_id = fields.Many2one('res.users', "Requested by", required=True, default=lambda s: s.env.user, index=True)
    level = fields.Selection(ACCESS_LEVELS, required=True, default='view')
    reason = fields.Text()
    state = fields.Selection([('pending', 'Waiting'), ('approved', 'Approved'), ('refused', 'Refused')],
                             default='pending', required=True, index=True)
    decided_by_id = fields.Many2one('res.users', readonly=True)
    decided_on = fields.Datetime(readonly=True)
    decision_note = fields.Text(readonly=True)

    @api.depends('user_id', 'document_id', 'level')
    def _compute_display_name(self):
        labels = dict(ACCESS_LEVELS)
        for r in self:
            r.display_name = "%s → %s · %s" % (r.user_id.name or '', r.document_id.code or r.document_id.name or '', labels.get(r.level, ''))

    # ------------------------------------------------------------------
    @api.model
    def _deciders(self, doc):
        """Document Controllers, and anyone with Edit on the document's folder."""
        users = self.env.ref('dms.group_dms_controller').sudo().user_ids
        Access = self.env['dms.access'].sudo()
        chain = self.env['dms.folder'].sudo().search([('id', 'parent_of', doc.folder_id.id)])
        users |= Access.search([('folder_id', 'in', chain.ids), ('level', '=', 'edit'), ('user_id', '!=', False)]).user_id
        users |= self.env['dms.group'].sudo().search([('folder_ids', 'in', chain.ids), ('level', '=', 'edit')]).user_ids
        return users.filtered(lambda u: u.active and not u.share)

    def _can_decide(self, user):
        self.ensure_one()
        return user.has_group('dms.group_dms_controller') or self.document_id.sudo()._access_for_user(user) == 'edit'

    @api.model
    def ask(self, doc_id, level, reason=None):
        doc = self.env['dms.document'].sudo().browse(int(doc_id)).exists()
        user = self.env.user
        if not doc:
            raise UserError(_("This document no longer exists."))
        if level not in dict(ACCESS_LEVELS):
            raise UserError(_("Choose View, View and Download or Edit."))
        if doc._blocked_for(user):
            raise AccessError(_("Your access to this document was removed. Talk to the Document Controller."))
        pending = self.sudo().search([('document_id', '=', doc.id), ('user_id', '=', user.id), ('state', '=', 'pending')])
        if pending:
            pending.write({'level': level, 'reason': reason})
            return pending
        req = self.sudo().create({'document_id': doc.id, 'user_id': user.id, 'level': level, 'reason': reason})
        for u in self._deciders(doc):
            doc.with_context(mail_activity_quick_update=True).activity_schedule('mail.mail_activity_data_todo', user_id=u.id,
                                  summary=_("DMS access request: %(u)s wants %(l)s on %(d)s",
                                            u=user.name, l=dict(ACCESS_LEVELS)[level], d=doc.display_name),
                                  note=reason or '')
        self.env['dms.log'].sudo().log('access_grant', document=doc,
                                       details=_("Access requested: %s", dict(ACCESS_LEVELS)[level]))
        return req

    def decide(self, approve, level=None, note=None):
        user = self.env.user
        for req in self.sudo():
            if req.state != 'pending':
                raise UserError(_("This request was already decided."))
            if not req._can_decide(user):
                raise AccessError(_("Only the Document Controller or someone with Edit on this folder can decide."))
            doc = req.document_id
            vals = {'state': 'approved' if approve else 'refused', 'decided_by_id': user.id,
                    'decided_on': fields.Datetime.now(), 'decision_note': note}
            if approve:
                vals['level'] = level or req.level
                Access = self.env['dms.access'].sudo()
                Access.search([('document_id', '=', doc.id), ('user_id', '=', req.user_id.id)]).unlink()
                Access.create({'document_id': doc.id, 'user_id': req.user_id.id, 'level': vals['level']})
                grp = self.env.ref('dms.group_dms_user').sudo()
                if req.user_id not in grp.user_ids:
                    grp.write({'user_ids': [(4, req.user_id.id)]})
            req.write(vals)
            doc.activity_ids.filtered(lambda a: a.summary and 'access request' in a.summary
                                      and req.user_id.name in a.summary).unlink()
            if req.user_id.partner_id:
                doc.message_post(
                    body=(_("Your request for %(l)s access to %(d)s was approved.",
                            l=dict(ACCESS_LEVELS)[vals.get('level', req.level)], d=doc.display_name) if approve else
                          _("Your request for access to %(d)s was refused. %(n)s", d=doc.display_name, n=note or '')), subtype_xmlid='mail.mt_note')


class DmsDocumentHardDelete(models.Model):
    _inherit = 'dms.document'

    def action_delete_permanently(self):
        """Document Controller only: remove the document, every revision, and the files on the DMS server."""
        if not self.env.user.has_group('dms.group_dms_controller'):
            raise AccessError(_("Only the Document Controller can delete documents permanently."))
        import glob
        import os
        storage = self.env['dms.storage']
        for doc in self.sudo():
            for rev in doc.revision_ids:
                if rev.storage_path:
                    storage.delete_file(rev.storage_path)
                try:
                    base = rev._cache_base()
                    for f in glob.glob(base + '*') + glob.glob(base.rsplit('_', 1)[0] + '_*'):
                        os.remove(f)
                except Exception:  # noqa: BLE001 - the preview cache is only a copy
                    pass
            code = doc.code
            self.env['dms.log'].sudo().log('discard', document=doc, details=_("Deleted permanently by %s", self.env.user.name))
            doc.activity_ids.unlink()
            doc.revision_ids.unlink()
            doc.unlink()
            # every folder named after this number goes too (normal and Archive copies, empty parents)
            keep = self.env['dms.folder'].sudo().search([]).mapped('server_path')
            try:
                storage.purge_document_dirs(code, keep=keep)
            except Exception as e:  # noqa: BLE001 - the record is gone; a leftover empty folder is harmless
                import logging
                logging.getLogger(__name__).warning("DMS: folders of %s not removed: %s", code, e)
        return True
