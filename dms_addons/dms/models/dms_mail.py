"""Emails for every DMS event, in one design.

Each email is a mail.mail with its own HTML (inline styles, table layout, so it looks the same
in Outlook, Gmail and phones): a coloured band with the event, the document box, an optional
comment, optional AI points, and a button that opens the document in the DMS.
Odoo's own notification mails for the same events are switched off, so nobody gets two.
"""
import json
import logging
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)

THEMES = {  # band / button colours per DMS theme
    'navy': {'ink': '#1c2b4a', 'soft': '#eef2f8', 'accent': '#3b6fd8'},
    'brown': {'ink': '#5a3a17', 'soft': '#f5eee0', 'accent': '#a5652a'},
    'gold': {'ink': '#2a2316', 'soft': '#f8f3e3', 'accent': '#b08a2e'},
}
NOTIFY_KIND = {  # DMS email kind -> in-app notification kind
    'approval_needed': 'approval', 'folder_delete_request': 'approval', 'disposal_request': 'approval',
    'published': 'success', 'step_approved': 'success', 'access_granted': 'success', 'folder_delete_done': 'success',
    'disposal_done': 'success', 'folder_delete_refused': 'success',
    'refused': 'danger', 'ai_failed': 'danger', 'access_refused': 'danger', 'disposal_refused': 'danger', 'force_unlock': 'danger',
    'access_request': 'access', 'shared': 'access', 'access_removed': 'access',
    'expiry': 'reminder', 'alert_digest': 'alert',
    'new_version': 'info', 'new_document': 'info', 'checked_out': 'info', 'cutoff_changed': 'warning',
    'revision_archived': 'info', 'deleted': 'danger', 'approval_reminder': 'reminder', 'approval_escalation': 'alert',
    'checkout_reminder': 'reminder', 'retention_due': 'reminder', 'group_added': 'access', 'group_removed': 'access',
}
HIGH = {'approval_needed', 'ai_failed', 'refused', 'alert_digest', 'disposal_request', 'approval_escalation', 'deleted'}

KINDS = {
    # kind: (label on the band, symbol, colour of the symbol circle)
    'approval_needed': ("Approval needed", "&#9998;", '#d08a12'),
    'submitted': ("Sent for approval", "&#10148;", '#3b6fd8'),
    'step_approved': ("Approved, moving on", "&#10003;", '#2f7d4f'),
    'published': ("Published", "&#9733;", '#2f7d4f'),
    'refused': ("Refused", "&#10007;", '#b4372f'),
    'ai_failed': ("Needs a corrected file", "&#9888;", '#b4372f'),
    'access_request': ("Access requested", "&#128273;", '#d08a12'),
    'access_granted': ("Access granted", "&#128275;", '#2f7d4f'),
    'access_refused': ("Access refused", "&#128274;", '#b4372f'),
    'shared': ("Shared with you", "&#128279;", '#3b6fd8'),
    'access_removed': ("Access removed", "&#128274;", '#66728a'),
    'folder_delete_request': ("Folder deletion requested", "&#128465;", '#d08a12'),
    'folder_delete_done': ("Folder deleted", "&#128465;", '#66728a'),
    'folder_delete_refused': ("Folder kept", "&#128193;", '#2f7d4f'),
    'disposal_request': ("Disposal approval needed", "&#128465;", '#d08a12'),
    'disposal_done': ("Files disposed of", "&#128465;", '#66728a'),
    'disposal_refused': ("Disposal refused", "&#10007;", '#b4372f'),
    'expiry': ("Expiring soon", "&#9200;", '#d08a12'),
    'force_unlock': ("Check-out cancelled", "&#128275;", '#b4372f'),
    'moved': ("Document moved", "&#10140;", '#3b6fd8'),
    'retired': ("Retired to the Archive", "&#128451;", '#66728a'),
    'alert_digest': ("Unusual activity", "&#128737;", '#b4372f'),
    'new_version': ("New version", "&#9733;", '#3b6fd8'),
    'new_document': ("New document", "&#128196;", '#3b6fd8'),
    'checked_out': ("Being edited", "&#9998;", '#3b6fd8'),
    'cutoff_changed': ("Cut-off changed", "&#9986;", '#d08a12'),
    'revision_archived': ("Revision archived", "&#128451;", '#66728a'),
    'deleted': ("Deleted", "&#128465;", '#b4372f'),
    'approval_reminder': ("Reminder", "&#9200;", '#d08a12'),
    'approval_escalation': ("Escalation", "&#9888;", '#b4372f'),
    'checkout_reminder': ("Reminder", "&#9200;", '#d08a12'),
    'retention_due': ("Retention ended", "&#128465;", '#d08a12'),
    'group_added': ("Group access", "&#128101;", '#2f7d4f'),
    'group_removed': ("Group access", "&#128101;", '#66728a'),
}


class DmsMail(models.AbstractModel):
    _name = 'dms.mail'
    _description = 'DMS emails'

    # ------------------------------------------------------------------ building blocks
    def _enabled(self):
        return self.env['ir.config_parameter'].sudo().get_param('dms.mail_enabled', 'True') not in ('False', '0', '')

    def _base_url(self):
        return self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')

    def _theme(self):
        return THEMES.get(self.env['ir.config_parameter'].sudo().get_param('dms.theme') or 'navy', THEMES['navy'])

    def _doc_url(self, doc):
        return '%s/dms/open/%s' % (self._base_url(), doc.id) if doc else '%s/odoo/action-dms.action_dms_app' % self._base_url()

    @api.model
    def doc_rows(self, doc, rev=None, extra=None):
        """The usual facts about a document, as (label, value) pairs."""
        rows = [(_("Document"), '%s  %s' % (doc.code or '', doc.name)),
                (_("Folder"), doc.folder_id.complete_name or ''),
                (_("Type"), doc.type_id.name or '')]
        if rev:
            rows.append((_("Revision"), rev.rev_label if rev.rev_no >= 0 else _("New (not numbered until approved)")))
            if rev.file_name:
                rows.append((_("File"), rev.file_name))
        rows += list(extra or [])
        return rows

    def _render(self, kind, title, intro, rows=None, quote=None, quote_by=None, points=None,
                points_title=None, cta=None, url=None, note=None):
        t = self._theme()
        label, symbol, colour = KINDS.get(kind, ("DMS", "&#9679;", t['accent']))
        company = self.env.company
        e = escape
        parts = [Markup(
            '<div style="margin:0;padding:24px 12px;background:#f3f1ec;font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#1f2633">'
            '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;'
            'background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e6e1d6">'
            '<tr><td style="background:%s;padding:18px 24px">'
            '<table role="presentation" cellpadding="0" cellspacing="0"><tr>'
            '<td style="width:40px;height:40px;border-radius:20px;background:%s;color:#ffffff;font-size:20px;'
            'text-align:center;vertical-align:middle;line-height:40px">%s</td>'
            '<td style="padding-left:12px;color:#ffffff;font-size:12px;letter-spacing:.12em;text-transform:uppercase;font-weight:700">%s'
            '<div style="font-size:11px;letter-spacing:0;text-transform:none;font-weight:400;opacity:.75">%s &middot; Document Management</div></td>'
            '</tr></table></td></tr>'
            '<tr><td style="padding:24px 24px 4px">'
            '<h1 style="margin:0 0 8px;font-size:21px;line-height:1.3;color:#1f2633;font-weight:700">%s</h1>'
            '<p style="margin:0;font-size:15px;line-height:1.55;color:#4a5263">%s</p></td></tr>'
        ) % (t['ink'], colour, Markup(symbol), e(label), e(company.name), e(title), e(intro))]
        if rows:
            cells = Markup('').join(Markup(
                '<tr><td style="padding:6px 12px 6px 0;color:#7a8293;font-size:13px;white-space:nowrap;vertical-align:top;width:110px">%s</td>'
                '<td style="padding:6px 0;font-size:14px;color:#1f2633;font-weight:600">%s</td></tr>') % (e(k), e(v))
                for k, v in rows if v not in (None, False, ''))
            parts.append(Markup(
                '<tr><td style="padding:16px 24px 0"><table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
                'style="background:%s;border-radius:10px;border-left:4px solid %s"><tr><td style="padding:12px 16px">'
                '<table role="presentation" cellpadding="0" cellspacing="0">%s</table></td></tr></table></td></tr>'
            ) % (t['soft'], t['ink'], cells))
        if quote:
            parts.append(Markup(
                '<tr><td style="padding:16px 24px 0"><div style="border-left:3px solid %s;padding:8px 14px;background:#fbfaf7;'
                'border-radius:0 8px 8px 0;font-size:14px;line-height:1.5;color:#33394a;font-style:italic">&ldquo;%s&rdquo;'
                '%s</div></td></tr>'
            ) % (colour, e(quote), Markup('<div style="font-style:normal;font-size:12px;color:#7a8293;margin-top:4px">&mdash; %s</div>') % e(quote_by) if quote_by else ''))
        if points:
            dot = {'high': '#b4372f', 'medium': '#d08a12', 'low': '#7a8293'}
            items = Markup('').join(Markup(
                '<tr><td style="vertical-align:top;padding:5px 10px 5px 0"><div style="width:9px;height:9px;border-radius:5px;'
                'background:%s;margin-top:6px"></div></td><td style="padding:4px 0;font-size:14px;line-height:1.45;color:#33394a">%s</td></tr>'
            ) % (dot.get(p[0], t['accent']) if isinstance(p, tuple) else t['accent'], e(p[1] if isinstance(p, tuple) else p))
                for p in points)
            parts.append(Markup(
                '<tr><td style="padding:16px 24px 0"><div style="font-size:12px;font-weight:700;color:#7a8293;text-transform:uppercase;'
                'letter-spacing:.08em;margin-bottom:4px">%s</div><table role="presentation" cellpadding="0" cellspacing="0">%s</table></td></tr>'
            ) % (e(points_title or _("Points")), items))
        if note:
            parts.append(Markup('<tr><td style="padding:14px 24px 0;font-size:13px;color:#7a8293;line-height:1.5">%s</td></tr>') % e(note))
        parts.append(Markup(
            '<tr><td style="padding:22px 24px 24px"><a href="%s" style="display:inline-block;background:%s;color:#ffffff;'
            'text-decoration:none;font-size:14px;font-weight:700;padding:12px 22px;border-radius:8px">%s &rarr;</a></td></tr>'
            '<tr><td style="padding:14px 24px;background:#faf8f3;border-top:1px solid #eee8dc;font-size:11px;color:#9097a5;line-height:1.5">'
            'Sent by the Document Management System of %s. Every view and download is recorded against your name.'
            '</td></tr></table></div>'
        ) % (e(url or self._doc_url(None)), t['ink'], e(cta or _("Open in DMS")), e(company.name)))
        return Markup('').join(parts)

    # ------------------------------------------------------------------ send
    def _notify_body(self, intro, rows=None, quote=None, quote_by=None):
        """Short HTML for the in-app notification: the sentence, the key facts, the comment."""
        e = escape
        html = Markup('<p>%s</p>') % e(intro)
        if rows:
            html += Markup('<table style="border-collapse:collapse;margin-top:4px">%s</table>') % Markup('').join(
                Markup('<tr><td style="color:#7a8293;padding:1px 10px 1px 0;white-space:nowrap">%s</td><td><b>%s</b></td></tr>') % (e(k), e(v))
                for k, v in rows[:6] if v not in (None, False, ''))
        if quote:
            html += Markup('<p style="margin-top:6px;font-style:italic">&ldquo;%s&rdquo;%s</p>') % (
                e(quote), Markup(' &mdash; %s') % e(quote_by) if quote_by else '')
        return html

    @api.model
    def send(self, kind, users, subject, title, intro, doc=None, **kw):
        """One email and one in-app notification per person. Nobody inactive, no portal users,
        and not the person who acted."""
        actor = self.env.user
        to_self = kw.pop('to_self', False)
        notify = kw.pop('notify', None) or {}
        users = users.filtered(lambda u: u.active and not u.share and (u != actor or to_self))
        if not users:
            return self.env['mail.mail']
        url = kw.pop('url', None) or self._doc_url(doc)
        by_mail, in_app = self.env['dms.notify.rule'].channels(kind)
        if self.env.context.get('dms_mail_sample'):
            by_mail = in_app = True  # samples always show both
        SN = self.env.get('smart.notification')
        if SN is not None and in_app:
            spec = dict(notify)
            record = spec.pop('record', None) or doc
            sender = actor if actor != self.env.ref('base.user_root') else self.env.ref('base.user_admin')
            try:
                SN.sudo().notify(record, users, title if not record else subject,
                                 body=self._notify_body(intro, kw.get('rows'), kw.get('quote'), kw.get('quote_by')),
                                 kind=NOTIFY_KIND.get(kind, 'info'), priority='high' if kind in HIGH else 'normal',
                                 url=spec.pop('url', url if (doc or not record) else None), sender=sender, **spec)
            except Exception:  # noqa: BLE001 - a notification never blocks the work itself
                _logger.exception("DMS: in-app notification failed for %s", kind)
        if not self._enabled() or not by_mail:
            return self.env['mail.mail']
        body = self._render(kind, title, intro, url=url, **kw)
        Mail = self.env['mail.mail'].sudo()
        email_from = self.env.company.email_formatted or self.env.ref('base.partner_root').email_formatted \
            or 'dms@localhost'
        out = Mail
        for u in users:
            out |= Mail.create({
                'subject': '[DMS] %s' % subject, 'body_html': body, 'email_from': email_from,
                'email_to': u.email_formatted if u.email else False,
                'recipient_ids': [(4, u.partner_id.id)] if not u.email else [],
                'auto_delete': False, 'model': doc._name if doc else False, 'res_id': doc.id if doc else False,
            })
        return out

    # ------------------------------------------------------------------ one sample of each, to check the design
    @api.model
    def _send_samples(self, to_user, kinds=None):
        """Every kind of DMS email, built from real documents, sent to one person only."""
        Doc = self.env['dms.document'].sudo()
        doc = Doc.search([('state', '=', 'published')], limit=1) or Doc.search([], limit=1)
        rev = doc.current_revision_id or doc.revision_ids[:1]
        inv = Doc.search([('type_id.ai_enabled', '=', True)], limit=1) or doc
        irev = inv.revision_ids[:1]
        review = json.loads(irev.ai_review or 'null') or {}
        risks = [(r.get('level'), r.get('point')) for r in review.get('risks', [])]
        folder = doc.folder_id
        me = self.env.user
        S = lambda kind, *a, **k: self.with_context(dms_mail_sample=True).send(kind, *a, to_self=True, **k) \
            if not kinds or kind in kinds else self.env['mail.mail']
        sent = self.env['mail.mail']
        sent |= S('approval_needed', to_user, _("Approve %s", inv.display_name), _("Your approval is needed"),
                  _("%(u)s sent %(d)s for approval. You are the L1 reviewer.", u=me.name, d=inv.display_name),
                  doc=inv, rows=self.doc_rows(inv, irev, [(_("Sent by"), me.name), (_("Step"), "L1 of 2")]),
                  quote=irev.change_note, quote_by=me.name, points=risks or None, points_title=_("The AI noticed"),
                  note=review.get('summary'), cta=_("Review and decide"))
        sent |= S('submitted', to_user, _("%s sent for approval", inv.display_name), _("Sent for approval"),
                  _("Your document is with the first approver now. You will hear from us at each step."),
                  doc=inv, rows=self.doc_rows(inv, irev, [(_("Now with"), "Dheepa (L1)"), (_("Then"), "Deepak (L2)")]))
        sent |= S('step_approved', to_user, _("%s approved at L1", doc.display_name), _("L1 approved, now with L2"),
                  _("Dheepa approved your document. It has moved to Deepak for L2 approval."),
                  doc=doc, rows=self.doc_rows(doc, rev, [(_("Approved by"), "Dheepa (L1)"), (_("Now with"), "Deepak (L2)")]),
                  quote=_("Checked the totals and GST, all good."), quote_by="Dheepa")
        sent |= S('published', to_user, _("%s is published", doc.display_name), _("Your document is published"),
                  _("Every approver said yes. Revision %s is now the current version everyone sees.", rev.rev_label),
                  doc=doc, rows=self.doc_rows(doc, rev, [(_("Published on"), fields.Date.to_string(fields.Date.today()))]),
                  points=[_("L1 approved by Dheepa"), _("L2 approved by Deepak")], points_title=_("Approvals"))
        sent |= S('refused', to_user, _("%s was refused", inv.display_name), _("Your document was refused"),
                  _("Deepak refused it at L2. Read the reason, fix the file and upload it again."),
                  doc=inv, rows=self.doc_rows(inv, irev, [(_("Refused by"), "Deepak (L2)")]),
                  quote=_("Authorised signatory is missing and the GST rate looks wrong."), quote_by="Deepak",
                  cta=_("Upload a corrected file"))
        sent |= S('ai_failed', to_user, _("%s needs a corrected file", inv.display_name), _("The AI check found problems"),
                  _("Some required rules did not pass, so it cannot go for approval yet."),
                  doc=inv, rows=self.doc_rows(inv, irev),
                  points=[('high', _("The invoice contains the authorised signatory: not found")),
                          ('high', _("Totals add up: 70,000 + 7,000 GST is not 75,000"))],
                  points_title=_("Rules not passed"), cta=_("Upload a corrected file"))
        sent |= S('access_request', to_user, _("Gopi asks for access to %s", doc.display_name), _("Someone asks for access"),
                  _("Gopi would like View & Download access to a document you look after."),
                  doc=doc, rows=self.doc_rows(doc, None, [(_("Asked by"), "Gopi"), (_("Level"), "View & Download")]),
                  quote=_("Need the latest NCR format for the audit."), quote_by="Gopi", cta=_("Give or refuse access"))
        sent |= S('access_granted', to_user, _("Access to %s granted", doc.display_name), _("You have access now"),
                  _("%s approved your request. Open the document whenever you need it.", me.name),
                  doc=doc, rows=self.doc_rows(doc, None, [(_("Level"), "View & Download"), (_("Granted by"), me.name)]))
        sent |= S('access_refused', to_user, _("Access to %s refused", doc.display_name), _("Your access request was refused"),
                  _("%s did not give access this time.", me.name),
                  doc=doc, rows=self.doc_rows(doc), quote=_("Please ask your manager to request it."), quote_by=me.name)
        sent |= S('shared', to_user, _("%s shared with you", folder.complete_name), _("A folder was shared with you"),
                  _("%(u)s gave you View access to %(f)s and everything in it.", u=me.name, f=folder.complete_name),
                  url=self._doc_url(None), rows=[(_("Folder"), folder.complete_name), (_("Level"), "View"), (_("Shared by"), me.name)])
        sent |= S('access_removed', to_user, _("Access to %s removed", folder.complete_name), _("Your access was removed"),
                  _("You can no longer open %s. Ask the Document Controller if you still need it.", folder.complete_name),
                  url=self._doc_url(None), rows=[(_("Folder"), folder.complete_name), (_("Removed by"), me.name)])
        sent |= S('folder_delete_request', to_user, _("Delete folder %s?", folder.complete_name), _("Folder deletion requested"),
                  _("Gopi asks to delete a folder. It is deleted only when you approve and it is empty."),
                  url=self._doc_url(None), rows=[(_("Folder"), folder.complete_name), (_("Asked by"), "Gopi")],
                  quote=_("Project closed, files moved to Archive."), quote_by="Gopi", cta=_("Review the request"))
        sent |= S('folder_delete_done', to_user, _("Folder %s deleted", folder.complete_name), _("The folder was deleted"),
                  _("%s approved your request and the folder is gone.", me.name),
                  url=self._doc_url(None), rows=[(_("Folder"), folder.complete_name), (_("Approved by"), me.name)])
        sent |= S('folder_delete_refused', to_user, _("Folder %s kept", folder.complete_name), _("The folder stays"),
                  _("%s refused the deletion.", me.name), url=self._doc_url(None),
                  rows=[(_("Folder"), folder.complete_name)], quote=_("It still holds live documents."), quote_by=me.name)
        sent |= S('disposal_request', to_user, _("Approve disposal DISP/0001"), _("Disposal approval needed"),
                  _("Archived files have passed their retention period and are ready to be destroyed."),
                  url=self._doc_url(None), rows=[(_("Request"), "DISP/0001"), (_("Folder"), folder.complete_name),
                                                 (_("Files"), "3"), (_("Asked by"), me.name)], cta=_("Review the disposal"))
        sent |= S('disposal_done', to_user, _("DISP/0001: files disposed of"), _("Files disposed of"),
                  _("Both approvals are in. The files were deleted from the DMS server and the log is kept."),
                  url=self._doc_url(None), rows=[(_("Request"), "DISP/0001"), (_("Files"), "3")])
        sent |= S('disposal_refused', to_user, _("DISP/0001 refused"), _("Disposal refused"),
                  _("The Department Head refused the disposal. The files stay in the Archive."),
                  url=self._doc_url(None), rows=[(_("Request"), "DISP/0001")],
                  quote=_("Keep these for the tax audit."), quote_by="Dheepa")
        sent |= S('expiry', to_user, _("%s: due in 12 days", inv.display_name), _("Due in 12 days"),
                  _("The AI found a date in this document that is coming up. Renew or replace it in time."),
                  doc=inv, rows=self.doc_rows(inv, None, [(_("Due date"), inv.expiry_date and fields.Date.to_string(inv.expiry_date) or "2026-11-16"),
                                                          (_("What"), inv.expiry_label or _("Due Date"))]))
        sent |= S('force_unlock', to_user, _("Your check-out of %s was cancelled", doc.display_name), _("Your check-out was cancelled"),
                  _("%s force-unlocked the document. Any edits you have not checked in are not in the DMS.", me.name),
                  doc=doc, rows=self.doc_rows(doc, None, [(_("Unlocked by"), me.name)]))
        sent |= S('moved', to_user, _("%s was moved", doc.display_name), _("Your document was moved"),
                  _("%s moved it to another folder. Its number and history stay the same.", me.name),
                  doc=doc, rows=self.doc_rows(doc, None, [(_("From"), "ACCOUNTS"), (_("To"), folder.complete_name)]))
        sent |= S('retired', to_user, _("%s retired", doc.display_name), _("Your document was retired"),
                  _("It is read only in the Archive now, and can be disposed of after its retention period."),
                  doc=doc, rows=self.doc_rows(doc, None, [(_("Retired by"), me.name)]),
                  quote=_("Replaced by the 2027 format."), quote_by=me.name)
        sent |= S('new_version', to_user, _("New version of %s", doc.display_name), _("A document you starred has a new version"),
                  _("Revision 1 is now the current version."), doc=doc, rows=self.doc_rows(doc, rev),
                  quote=_("Updated the defect codes list."), quote_by="Gopi")
        sent |= S('new_document', to_user, _("New in %s: %s", folder.name, doc.display_name), _("A new document is published"),
                  _("%(d)s is now published in %(f)s.", d=doc.display_name, f=folder.complete_name), doc=doc,
                  rows=self.doc_rows(doc, rev, [(_("Author"), "Gopi")]))
        sent |= S('checked_out', to_user, _("%s was checked out", doc.display_name), _("Your document is being edited"),
                  _("Gopi checked it out to make a new version. Readers still see the approved revision."), doc=doc,
                  rows=self.doc_rows(doc, None, [(_("Checked out by"), "Gopi")]))
        sent |= S('cutoff_changed', to_user, _("Cut-off of %s brought forward", doc.display_name), _("The cut-off date was brought forward"),
                  _("Dheepa moved the cut-off of revision 0 to tomorrow."), doc=doc, rows=self.doc_rows(doc, rev))
        sent |= S('revision_archived', to_user, _("%s Rev 0 archived", doc.display_name), _("An old revision was archived"),
                  _("Revision 0 passed its cut-off and moved to the Archive. The current version is unchanged."), doc=doc, rows=self.doc_rows(doc, rev))
        sent |= S('deleted', to_user, _("%s was deleted", doc.display_name), _("Your document was deleted"),
                  _("%s deleted it permanently, with every revision and its files.", me.name),
                  rows=[(_("Document"), doc.display_name), (_("Folder"), folder.complete_name), (_("Deleted by"), me.name)])
        sent |= S('approval_reminder', to_user, _("Reminder: %s waits for you", inv.display_name), _("Still waiting for your approval"),
                  _("%s has waited 4 days for your L1 approval.", inv.display_name), doc=inv,
                  rows=self.doc_rows(inv, irev, [(_("Sent by"), "Gopi"), (_("Waiting"), _("4 days"))]), cta=_("Review and decide"))
        sent |= S('approval_escalation', to_user, _("Escalation: %s stuck at L1", inv.display_name), _("An approval is stuck"),
                  _("Dheepa has not decided on %s for 7 days.", inv.display_name), doc=inv,
                  rows=self.doc_rows(inv, irev, [(_("Waiting for"), "Dheepa (L1)"), (_("Waiting"), _("7 days"))]))
        sent |= S('checkout_reminder', to_user, _("%s is still checked out to you", doc.display_name), _("Still checked out to you"),
                  _("You checked this out 9 days ago. Check in your new version, or cancel the check-out so others can edit."),
                  doc=doc, rows=self.doc_rows(doc, None, [(_("Days"), "9")]), cta=_("Check in or cancel"))
        sent |= S('retention_due', to_user, _("3 archived file(s) past retention"), _("Files ready for disposal"),
                  _("These archived files have passed their retention period. Raise a disposal request when ready."),
                  points=["ACC-INV-004 Rev 0", "ACC-INV-007 Rev 1", "QUA-SOP-002 Rev 2"], points_title=_("Files"),
                  url='%s/odoo/action-dms.action_dms_disposal' % self._base_url(), cta=_("Open Disposal"))
        sent |= S('group_added', to_user, _("You were added to Accounts team"), _("You joined the group Accounts team"),
                  _("You can now open the folders below."), rows=[(_("Group"), "Accounts team"), (_("Folders"), "ACCOUNTS"), (_("Changed by"), me.name)])
        sent |= S('group_removed', to_user, _("You were removed from Software team"), _("You left the group Software team"),
                  _("You no longer get access through this group."), rows=[(_("Group"), "Software team"), (_("Folders"), "SOFTWARE")])
        if not kinds or 'alert_digest' in kinds:
            alerts = self.env['dms.dashboard'].alerts(days=7)[:6]
            sent |= self.with_context(dms_mail_sample=True)._alert_digest_mail(to_user, alerts, to_self=True)
        return sent

    # ------------------------------------------------------------------ unusual activity, once a day
    def _alert_digest_mail(self, users, alerts, **kw):
        if not alerts:
            return self.env['mail.mail']
        high = sum(1 for a in alerts if a['level'] == 'high')
        return self.send('alert_digest', users, _("%(n)s unusual activit%(s)s", n=len(alerts), s='y' if len(alerts) == 1 else 'ies'),
                         _("Unusual activity to look at"),
                         _("%(n)s pattern(s) in the audit log need a second look, %(h)s of them high.", n=len(alerts), h=high),
                         points=[(a['level'], '%s: %s' % (a['title'], a['detail'])) for a in alerts],
                         points_title=_("What happened"), url='%s/odoo/action-dms.action_dms_app' % self._base_url(),
                         cta=_("Open the dashboard"), **kw)

    @api.model
    def _cron_alert_digest(self):
        """Morning mail to the Document Controllers with yesterday's unusual activity."""
        since = fields.Datetime.to_string(fields.Datetime.now() - timedelta(days=1))
        alerts = [a for a in self.env['dms.dashboard'].alerts(days=2) if a['level'] in ('high', 'medium') and a['date'] >= since]
        self._alert_digest_mail(self.env.ref('dms.group_dms_controller').sudo().user_ids, alerts)


# ---------------------------------------------------------------------- where each email starts
class DmsRevisionMail(models.Model):
    _inherit = 'dms.revision'

    def _ai_points(self):
        review = json.loads(self.ai_review or 'null') or {}
        changes = json.loads(self.ai_changes or 'null') or {}
        points = [(r.get('level'), r.get('point')) for r in review.get('risks', []) if r.get('point')]
        points += [('low', _("Changed: %s", c)) for c in changes.get('items', [])[:5]]
        return points, review.get('summary')

    def _notify_next_approver(self):
        super()._notify_next_approver()
        nxt = self.approval_ids.filtered(lambda a: a.state == 'waiting').sorted('sequence')[:1]
        if not nxt:
            return
        doc = self.document_id
        steps = len(self.approval_ids)
        points, summary = self._ai_points()
        self.env['dms.mail'].send(
            'approval_needed', nxt.user_id, _("Approve %s", doc.display_name), _("Your approval is needed"),
            _("%(u)s sent %(d)s for approval. You are the %(lv)s approver.", u=self.author_id.name, d=doc.display_name, lv=nxt.level.upper()),
            doc=doc, rows=self.env['dms.mail'].doc_rows(doc, self, [
                (_("Sent by"), self.author_id.name), (_("Step"), _("%(a)s of %(b)s", a=nxt.sequence + 1, b=steps))]),
            quote=self.change_note, quote_by=self.author_id.name, points=points or None, points_title=_("The AI noticed"),
            note=summary, cta=_("Review and decide"),
            notify={'record': self, 'policy': 'any', 'action_method': 'action_approve', 'action_label': _("Approve"),
                    'close_methods': 'action_refuse', 'reject_method': 'action_refuse', 'reject_label': _("Refuse")})

    def action_submit(self):
        res = super().action_submit()
        for r in self.filtered(lambda r: r.state == 'in_review'):
            nxt = r.approval_ids.sorted('sequence')
            self.env['dms.mail'].send(
                'submitted', r.author_id, _("%s sent for approval", r.document_id.display_name), _("Sent for approval"),
                _("Your document is with the first approver now. You will hear from us at each step."),
                doc=r.document_id, to_self=True, rows=self.env['dms.mail'].doc_rows(r.document_id, r, [
                    (_("Approvers"), ' → '.join('%s (%s)' % (a.user_id.name, a.level.upper()) for a in nxt))]))
        return res

    def action_approve(self, comment=None):
        line = self.approval_ids.filtered(lambda a: a.state == 'waiting').sorted('sequence')[:1]
        res = super().action_approve(comment)
        self.env['smart.notification'].sudo()._on_action(self._name, self.ids, 'action_approve', self.env.user)
        doc = self.document_id
        M = self.env['dms.mail']
        if self.state == 'current':
            M.send('published', self.author_id | doc.owner_id, _("%s is published", doc.display_name), _("Your document is published"),
                   _("Every approver said yes. Revision %s is now the current version everyone sees.", self.rev_label),
                   doc=doc, rows=M.doc_rows(doc, self, [(_("Published on"), fields.Date.to_string(self.approved_date))]),
                   points=['%s %s %s' % (a.level.upper(), _("approved by"), a.user_id.name) for a in self.approval_ids.sorted('sequence')
                           if a.state == 'approved'], points_title=_("Approvals"))
        else:
            nxt = self.approval_ids.filtered(lambda a: a.state == 'waiting').sorted('sequence')[:1]
            M.send('step_approved', self.author_id, _("%(d)s approved at %(lv)s", d=doc.display_name, lv=line.level.upper()),
                   _("%(lv)s approved, now with %(n)s", lv=line.level.upper(), n=nxt.level.upper()),
                   _("%(u)s approved your document. It has moved to %(n)s for %(lv)s approval.",
                     u=self.env.user.name, n=nxt.user_id.name, lv=nxt.level.upper()),
                   doc=doc, rows=M.doc_rows(doc, self, [(_("Approved by"), '%s (%s)' % (self.env.user.name, line.level.upper())),
                                                        (_("Now with"), '%s (%s)' % (nxt.user_id.name, nxt.level.upper()))]),
                   quote=comment, quote_by=self.env.user.name)
        return res

    def action_refuse(self, comment):
        line = self.approval_ids.filtered(lambda a: a.state == 'waiting').sorted('sequence')[:1]
        res = super().action_refuse(comment)
        self.env['smart.notification'].sudo()._on_action(self._name, self.ids, 'action_refuse', self.env.user)
        doc = self.document_id
        M = self.env['dms.mail']
        M.send('refused', self.author_id, _("%s was refused", doc.display_name), _("Your document was refused"),
               _("%(u)s refused it at %(lv)s. Read the reason, fix the file and upload it again.", u=self.env.user.name, lv=line.level.upper()),
               doc=doc, rows=M.doc_rows(doc, self, [(_("Refused by"), '%s (%s)' % (self.env.user.name, line.level.upper()))]),
               quote=comment, quote_by=self.env.user.name, cta=_("Upload a corrected file"))
        return res

    def _ai_run_and_follow_up(self):
        res = super()._ai_run_and_follow_up()
        if self.ai_blocked and self.ai_state == 'done':
            failed = [('high', '%s: %s' % (x['rule'], x.get('reason') or ''))
                      for x in json.loads(self.ai_results or '[]') if not x.get('passed') and x.get('blocking')]
            M = self.env['dms.mail']
            M.with_user(self.env.ref('base.user_root')).send(
                'ai_failed', self.author_id, _("%s needs a corrected file", self.document_id.display_name),
                _("The AI check found problems"), _("Some required rules did not pass, so it cannot go for approval yet."),
                doc=self.document_id, rows=M.doc_rows(self.document_id, self), points=failed[:8],
                points_title=_("Rules not passed"), cta=_("Upload a corrected file"))
        return res


class DmsDocumentMail(models.Model):
    _inherit = 'dms.document'

    def action_unlock(self, force=False):
        holders = {d.id: d.locked_by_id for d in self}
        res = super().action_unlock(force=force)
        M = self.env['dms.mail']
        for d in self:
            holder = holders.get(d.id)
            if holder and holder != self.env.user:
                M.send('force_unlock', holder, _("Your check-out of %s was cancelled", d.display_name),
                       _("Your check-out was cancelled"),
                       _("%s force-unlocked the document. Edits you have not checked in are not in the DMS.", self.env.user.name),
                       doc=d, rows=M.doc_rows(d, None, [(_("Unlocked by"), self.env.user.name)]))
        return res

    def action_move(self, folder):
        before = {d.id: d.folder_id.complete_name for d in self}
        res = super().action_move(folder)
        M = self.env['dms.mail']
        for d in self:
            M.send('moved', d.owner_id, _("%s was moved", d.display_name), _("Your document was moved"),
                   _("%s moved it to another folder. Its number and history stay the same.", self.env.user.name),
                   doc=d, rows=M.doc_rows(d, None, [(_("From"), before.get(d.id)), (_("To"), d.folder_id.complete_name)]))
        return res

    def action_retire(self, reason=None):
        res = super().action_retire(reason)
        M = self.env['dms.mail']
        for d in self:
            M.send('retired', d.owner_id, _("%s retired", d.display_name), _("Your document was retired"),
                   _("It is read only in the Archive now, and can be disposed of after its retention period."),
                   doc=d, rows=M.doc_rows(d, None, [(_("Retired by"), self.env.user.name)]), quote=reason,
                   quote_by=self.env.user.name)
        return res

    def _expiry_mail(self, left):
        self.ensure_one()
        M = self.env['dms.mail']
        when = _("today") if left == 0 else (_("in %s days", left) if left > 0 else _("%s days ago", -left))
        M.with_user(self.env.ref('base.user_root')).send(
            'expiry', self.owner_id, _("%(d)s: %(w)s %(when)s", d=self.display_name, w=(self.expiry_label or _("expires")).lower(), when=when),
            _("%(w)s %(when)s", w=self.expiry_label or _("Expires"), when=when),
            _("The AI found a date in this document that is coming up. Renew or replace it in time."),
            doc=self, rows=M.doc_rows(self, None, [(_("Date"), fields.Date.to_string(self.expiry_date)),
                                                   (_("What"), self.expiry_label or '')]))


class DmsAccessRequestMail(models.Model):
    _inherit = 'dms.access.request'

    @api.model
    def ask(self, doc_id, level, reason=None):
        req = super().ask(doc_id, level, reason)
        doc = req.document_id
        M = self.env['dms.mail']
        from .dms_folder import ACCESS_LEVELS
        M.send('access_request', self._deciders(doc), _("%(u)s asks for access to %(d)s", u=req.user_id.name, d=doc.display_name),
               _("Someone asks for access"),
               _("%(u)s would like %(l)s access to a document you look after.", u=req.user_id.name, l=dict(ACCESS_LEVELS)[req.level]),
               doc=doc, rows=M.doc_rows(doc, None, [(_("Asked by"), req.user_id.name), (_("Level"), dict(ACCESS_LEVELS)[req.level])]),
               quote=reason, quote_by=req.user_id.name, cta=_("Give or refuse access"),
               notify={'record': req, 'url': M._doc_url(doc), 'policy': 'any', 'action_method': 'action_grant',
                       'action_label': _("Grant access"), 'done_methods': 'decide,action_grant,action_refuse_access',
                       'reject_method': 'action_refuse_access', 'reject_label': _("Refuse")})
        return req

    def action_grant(self):
        """Give the access asked for (the Grant button of the notification)."""
        return self.decide(True)

    def action_refuse_access(self, reason=None):
        """Refuse with the reason typed in the notification."""
        return self.decide(False, note=reason)

    def decide(self, approve, level=None, note=None):
        res = super(DmsAccessRequestMail, self.with_context(dms_no_share_mail=True)).decide(approve, level=level, note=note)
        self.env['smart.notification'].sudo()._on_action(self._name, self.ids, 'decide', self.env.user)
        M = self.env['dms.mail']
        from .dms_folder import ACCESS_LEVELS
        for req in self.sudo():
            doc = req.document_id
            if approve:
                M.send('access_granted', req.user_id, _("Access to %s granted", doc.display_name), _("You have access now"),
                       _("%s approved your request. Open the document whenever you need it.", self.env.user.name),
                       doc=doc, rows=M.doc_rows(doc, None, [(_("Level"), dict(ACCESS_LEVELS)[req.level]),
                                                            (_("Granted by"), self.env.user.name)]))
            else:
                M.send('access_refused', req.user_id, _("Access to %s refused", doc.display_name), _("Your access request was refused"),
                       _("%s did not give access this time.", self.env.user.name),
                       doc=doc, rows=M.doc_rows(doc), quote=note, quote_by=self.env.user.name)
        return res


class DmsAccessMail(models.Model):
    _inherit = 'dms.access'

    def _people(self):
        self.ensure_one()
        return self.user_id or self.group_id.user_ids

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        if self.env.context.get('dms_no_share_mail'):
            return recs
        M = self.env['dms.mail']
        from .dms_folder import LINE_LEVELS
        for a in recs:
            if a.level == 'none':
                continue
            what = a.folder_id.complete_name if a.folder_id else a.document_id.display_name
            M.send('shared', a._people(), _("%s shared with you", what),
                   _("A folder was shared with you") if a.folder_id else _("A document was shared with you"),
                   _("%(u)s gave you %(l)s access to %(w)s.", u=self.env.user.name, l=dict(LINE_LEVELS)[a.level], w=what),
                   doc=a.document_id or None,
                   rows=[(_("Folder") if a.folder_id else _("Document"), what), (_("Level"), dict(LINE_LEVELS)[a.level]),
                         (_("Shared by"), self.env.user.name)])
        return recs

    def unlink(self):
        if not self.env.context.get('dms_no_share_mail'):
            M = self.env['dms.mail']
            for a in self:
                what = a.folder_id.complete_name if a.folder_id else a.document_id.display_name
                M.send('access_removed', a._people(), _("Access to %s removed", what), _("Your access was removed"),
                       _("You can no longer open %s. Ask the Document Controller if you still need it.", what),
                       rows=[(_("Folder") if a.folder_id else _("Document"), what), (_("Removed by"), self.env.user.name)])
        return super().unlink()


class DmsFolderMail(models.Model):
    _inherit = 'dms.folder'

    def _request_delete(self, user, reason):
        res = super()._request_delete(user, reason)
        self.env['dms.mail'].send(
            'folder_delete_request', self.env.ref('dms.group_dms_controller').sudo().user_ids,
            _("Delete folder %s?", self.complete_name), _("Folder deletion requested"),
            _("%s asks to delete a folder. It is deleted only when you approve and it is empty.", user.name),
            rows=[(_("Folder"), self.complete_name), (_("Asked by"), user.name)], quote=reason, quote_by=user.name,
            cta=_("Review the request"),
            notify={'record': self, 'url': '%s/odoo/action-dms.action_dms_app' % self.env['dms.mail']._base_url(),
                    'policy': 'any', 'action_method': 'action_approve_delete', 'action_label': _("Delete folder"),
                    'done_methods': '_decide_delete,action_approve_delete,action_keep_folder',
                    'reject_method': 'action_keep_folder', 'reject_label': _("Keep folder")})
        return res

    def _check_delete_decider(self):
        if not self.env.user.has_group('dms.group_dms_controller'):
            from odoo.exceptions import AccessError
            raise AccessError(_("Only the Document Controller approves folder deletion."))

    def action_approve_delete(self):
        self.ensure_one()
        self._check_delete_decider()
        return self.sudo()._decide_delete(self.env.user, True)

    def action_keep_folder(self, reason=None):
        self.ensure_one()
        self._check_delete_decider()
        return self.sudo()._decide_delete(self.env.user, False, reason)

    def _decide_delete(self, user, approve, note=None):
        requester, name = self.delete_requested_by_id, self.complete_name
        self.env['smart.notification'].sudo()._on_action(self._name, self.ids, '_decide_delete', user)
        res = super()._decide_delete(user, approve, note)
        M = self.env['dms.mail']
        if approve:
            M.send('folder_delete_done', requester, _("Folder %s deleted", name), _("The folder was deleted"),
                   _("%s approved your request and the folder is gone.", user.name),
                   rows=[(_("Folder"), name), (_("Approved by"), user.name)])
        else:
            M.send('folder_delete_refused', requester, _("Folder %s kept", name), _("The folder stays"),
                   _("%s refused the deletion.", user.name), rows=[(_("Folder"), name)], quote=note, quote_by=user.name)
        return res


class DmsDisposalMail(models.Model):
    _inherit = 'dms.disposal'

    def _mail_rows(self):
        return [(_("Request"), self.name), (_("Folder"), self.folder_id.complete_name),
                (_("Files"), str(len(self.revision_ids))), (_("Asked by"), self.requested_by_id.name)]

    def action_submit(self):
        res = super().action_submit()
        M = self.env['dms.mail']
        for d in self:
            M.send('disposal_request', d.dept_head_id | self.env.ref('dms.group_dms_controller').sudo().user_ids,
                   _("Approve disposal %s", d.name), _("Disposal approval needed"),
                   _("Archived files have passed their retention period and are ready to be destroyed."),
                   rows=d._mail_rows(), points=d.revision_ids.mapped('display_name')[:10], points_title=_("Files"),
                   cta=_("Review the disposal"),
                   notify={'record': d, 'url': False, 'policy': 'all', 'action_method': 'action_approve',
                           'action_label': _("Approve"), 'close_methods': 'action_refuse',
                           'reject_method': 'action_refuse', 'reject_label': _("Refuse")})
        return res

    def action_approve(self):
        res = super().action_approve()
        self.env['smart.notification'].sudo()._on_action(self._name, self.ids, 'action_approve', self.env.user)
        for d in self.filtered(lambda d: d.state == 'done'):
            self.env['dms.mail'].send('disposal_done', d.requested_by_id | d.dept_head_id, _("%s: files disposed of", d.name),
                                      _("Files disposed of"),
                                      _("Both approvals are in. The files were deleted from the DMS server and the log is kept."),
                                      rows=d._mail_rows())
        return res

    def action_refuse(self, reason=None):
        res = super().action_refuse(reason)
        self.env['smart.notification'].sudo()._on_action(self._name, self.ids, 'action_refuse', self.env.user)
        for d in self:
            self.env['dms.mail'].send('disposal_refused', d.requested_by_id, _("%s refused", d.name), _("Disposal refused"),
                                      _("%s refused the disposal. The files stay in the Archive.", self.env.user.name),
                                      rows=d._mail_rows(), quote=reason, quote_by=self.env.user.name)
        return res
