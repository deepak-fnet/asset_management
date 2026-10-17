"""Which DMS events send an email, an in-app notification, both or nothing, plus the events and
daily reminders that complete the set."""
from datetime import timedelta

from odoo import api, fields, models, _


class DmsNotifyRule(models.Model):
    _name = 'dms.notify.rule'
    _description = 'DMS notification setting'
    _order = 'sequence, id'

    sequence = fields.Integer(default=10)
    kind = fields.Char(required=True, readonly=True, index=True)
    name = fields.Char("Event", required=True)
    category = fields.Selection([('approval', 'Approval'), ('access', 'Access'), ('document', 'Document'),
                                 ('folder', 'Folder & disposal'), ('reminder', 'Reminders'), ('security', 'Security')],
                                required=True, default='document')
    audience = fields.Char("Who gets it")
    email = fields.Boolean("Email", default=True)
    app = fields.Boolean("In-app", default=True)
    description = fields.Char("When")

    _kind_unique = models.Constraint('unique(kind)', "One setting per event.")

    @api.model
    def channels(self, kind):
        """(email, in-app) for an event. Unknown events send both."""
        rule = self.sudo().search([('kind', '=', kind)], limit=1)
        return (rule.email, rule.app) if rule else (True, True)

    def action_send_sample(self):
        """Send me one of these, to see how it looks."""
        self.env['dms.mail']._send_samples(self.env.user, kinds=set(self.mapped('kind')))
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'type': 'success', 'title': _("Sample sent"),
                           'message': _("Check your notifications and Settings > Technical > Emails.")}}


class DmsApprovalReminder(models.Model):
    _inherit = 'dms.approval'

    reminded_on = fields.Date(readonly=True)
    escalated = fields.Boolean(readonly=True)


class DmsRevisionReminder(models.Model):
    _inherit = 'dms.revision'

    retention_notified = fields.Boolean(readonly=True)


class DmsDocumentReminder(models.Model):
    _inherit = 'dms.document'

    checkout_reminded_on = fields.Date(readonly=True)


class DmsMailMore(models.AbstractModel):
    _inherit = 'dms.mail'

    @api.model
    def folder_audience(self, folder):
        """Everyone who can open documents in ``folder``: direct grants, groups, and the Controllers."""
        chain = self.env['dms.folder'].sudo().search([('id', 'parent_of', folder.id)])
        users = self.env.ref('dms.group_dms_controller').sudo().user_ids
        lines = self.env['dms.access'].sudo().search([('folder_id', 'in', chain.ids), ('level', '!=', 'none')])
        users |= lines.user_id | lines.group_id.user_ids
        users |= self.env['dms.group'].sudo().search([('folder_ids', 'in', chain.ids)]).user_ids
        return users

    @api.model
    def _cron_reminders(self):
        """Daily: approvals waiting too long, forgotten check-outs, archived files past retention."""
        M = self
        ICP = self.env['ir.config_parameter'].sudo()
        today = fields.Date.context_today(self)
        now = fields.Datetime.now()
        days = int(ICP.get_param('dms.alert_approval_days') or 3)
        root = self.env.ref('base.user_root')
        controllers = self.env.ref('dms.group_dms_controller').sudo().user_ids
        Approval = self.env['dms.approval'].sudo()
        for a in Approval.search([('state', '=', 'waiting'), ('revision_id.state', '=', 'in_review')]):
            r = a.revision_id
            if a != r.approval_ids.filtered(lambda x: x.state == 'waiting').sorted('sequence')[:1] or not r.submitted_date:
                continue
            waited = (now - r.submitted_date).days
            doc = r.document_id
            if waited >= days and a.reminded_on != today:
                M.with_user(root).send('approval_reminder', a.user_id, _("Reminder: %s waits for you", doc.display_name),
                                       _("Still waiting for your approval"),
                                       _("%(d)s has waited %(n)s days for your %(lv)s approval.", d=doc.display_name, n=waited, lv=a.level.upper()),
                                       doc=doc, rows=M.doc_rows(doc, r, [(_("Sent by"), r.author_id.name), (_("Waiting"), _("%s days", waited))]),
                                       cta=_("Review and decide"))
                a.reminded_on = today
            if waited >= days * 2 and not a.escalated:
                M.with_user(root).send('approval_escalation', controllers, _("Escalation: %s stuck at %s", doc.display_name, a.level.upper()),
                                       _("An approval is stuck"),
                                       _("%(u)s has not decided on %(d)s for %(n)s days.", u=a.user_id.name, d=doc.display_name, n=waited),
                                       doc=doc, rows=M.doc_rows(doc, r, [(_("Waiting for"), '%s (%s)' % (a.user_id.name, a.level.upper())),
                                                                         (_("Waiting"), _("%s days", waited))]))
                a.escalated = True
        stale = int(ICP.get_param('dms.alert_checkout_days') or 7)
        for d in self.env['dms.document'].sudo().search([('locked_by_id', '!=', False), ('locked_date', '<=', now - timedelta(days=stale))]):
            if d.checkout_reminded_on and (today - d.checkout_reminded_on).days < 7:
                continue
            held = (now - d.locked_date).days
            M.with_user(root).send('checkout_reminder', d.locked_by_id, _("%s is still checked out to you", d.display_name),
                                   _("Still checked out to you"),
                                   _("You checked this out %s days ago. Check in your new version, or cancel the check-out so others can edit.", held),
                                   doc=d, rows=M.doc_rows(d, None, [(_("Checked out"), fields.Datetime.to_string(d.locked_date)[:16]),
                                                                    (_("Days"), str(held))]), cta=_("Check in or cancel"))
            d.checkout_reminded_on = today
        due = self.env['dms.revision'].sudo().search([('state', '=', 'archived'), ('retention_end', '<=', today),
                                                       ('retention_notified', '=', False)])
        if due:
            M.with_user(root).send('retention_due', controllers, _("%s archived file(s) past retention", len(due)),
                                   _("Files ready for disposal"),
                                   _("These archived files have passed their retention period. Raise a disposal request when ready."),
                                   points=due.mapped('display_name')[:15], points_title=_("Files"),
                                   url='%s/odoo/action-dms.action_dms_disposal' % self._base_url(), cta=_("Open Disposal"))
            due.retention_notified = True


class DmsRevisionMore(models.Model):
    _inherit = 'dms.revision'

    def action_approve(self, comment=None):
        res = super().action_approve(comment)
        if self.state == 'current':
            doc = self.document_id
            M = self.env['dms.mail']
            if self.rev_no > 0:
                watchers = doc.favorite_user_ids - self.author_id - doc.owner_id
                M.send('new_version', watchers, _("New version of %s", doc.display_name), _("A document you starred has a new version"),
                       _("Revision %s is now the current version.", self.rev_label),
                       doc=doc, rows=M.doc_rows(doc, self), quote=self.change_note, quote_by=self.author_id.name)
            else:
                M.send('new_document', M.folder_audience(doc.folder_id) - self.author_id,
                       _("New in %s: %s", doc.folder_id.name, doc.display_name), _("A new document is published"),
                       _("%(d)s is now published in %(f)s.", d=doc.display_name, f=doc.folder_id.complete_name),
                       doc=doc, rows=M.doc_rows(doc, self, [(_("Author"), self.author_id.name)]))
        return res

    def action_set_cutoff(self, date):
        res = super().action_set_cutoff(date)
        doc = self.document_id
        M = self.env['dms.mail']
        M.send('cutoff_changed', doc.owner_id | self.author_id, _("Cut-off of %s brought forward", self.display_name),
               _("The cut-off date was brought forward"),
               _("%(u)s moved the cut-off of revision %(r)s to %(d)s.", u=self.env.user.name, r=self.rev_label, d=fields.Date.to_string(self.cutoff_date)),
               doc=doc, rows=M.doc_rows(doc, self, [(_("New cut-off"), fields.Date.to_string(self.cutoff_date))]))
        return res

    def _archive(self):
        res = super()._archive()
        M = self.env['dms.mail'].with_user(self.env.ref('base.user_root'))
        for r in self:
            doc = r.document_id
            M.send('revision_archived', doc.owner_id, _("%s archived", r.display_name), _("An old revision was archived"),
                   _("Revision %s passed its cut-off and moved to the Archive. The current version is unchanged.", r.rev_label),
                   doc=doc, rows=M.doc_rows(doc, r, [(_("Retention ends"), fields.Date.to_string(r.retention_end) if r.retention_end else '')]))
        return res


class DmsDocumentMore(models.Model):
    _inherit = 'dms.document'

    def action_checkout(self):
        res = super().action_checkout()
        M = self.env['dms.mail']
        for d in self:
            M.send('checked_out', d.owner_id, _("%s was checked out", d.display_name), _("Your document is being edited"),
                   _("%s checked it out to make a new version. Readers still see the approved revision.", self.env.user.name),
                   doc=d, rows=M.doc_rows(d, None, [(_("Checked out by"), self.env.user.name)]))
        return res

    def action_delete_permanently(self):
        info = [(d.owner_id, d.display_name, d.folder_id.complete_name) for d in self.sudo()]
        res = super().action_delete_permanently()
        M = self.env['dms.mail']
        for owner, name, folder in info:
            M.send('deleted', owner, _("%s was deleted", name), _("Your document was deleted"),
                   _("%s deleted it permanently, with every revision and its files.", self.env.user.name),
                   rows=[(_("Document"), name), (_("Folder"), folder), (_("Deleted by"), self.env.user.name)])
        return res


class DmsGroupMail(models.Model):
    _inherit = 'dms.group'

    def _members_mail(self, kind, users):
        M = self.env['dms.mail']
        for g in self:
            folders = ', '.join(g.folder_ids.mapped('complete_name')) or '-'
            added = kind == 'group_added'
            M.send(kind, users, _("You were %s %s", _("added to") if added else _("removed from"), g.name),
                   _("You joined the group %s", g.name) if added else _("You left the group %s", g.name),
                   _("You can now open the folders below.") if added else _("You no longer get access through this group."),
                   rows=[(_("Group"), g.name), (_("Folders"), folders), (_("Changed by"), self.env.user.name)])

    @api.model_create_multi
    def create(self, vals_list):
        groups = super().create(vals_list)
        for g in groups:
            g._members_mail('group_added', g.user_ids)
        return groups

    def write(self, vals):
        before = {g.id: g.user_ids for g in self} if 'user_ids' in vals else {}
        res = super().write(vals)
        for g in self.filtered(lambda g: g.id in before):
            g._members_mail('group_added', g.user_ids - before[g.id])
            g._members_mail('group_removed', before[g.id] - g.user_ids)
        return res
