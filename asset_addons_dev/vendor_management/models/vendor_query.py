# -*- coding: utf-8 -*-
"""Query back to the vendor.

A reviewer who finds something wrong or missing in a vendor's onboarding
raises a Query (what is wrong, which documents). The vendor is emailed, sees
the query on the portal, fixes the flagged documents and Resubmits with a
reply; the application goes back to review. Every round is kept as history.
"""
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .vendor_compat_utils import group_members


class VendorQuery(models.Model):
    _name = 'vendor.query'
    _description = 'Vendor Onboarding Query'
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char(compute='_compute_name', store=True)
    vendor_id = fields.Many2one('res.partner', string='Vendor', required=True, ondelete='cascade', index=True)
    query = fields.Text(string='Query', required=True)
    document_ids = fields.Many2many('vendor.document', 'vendor_query_document_rel', 'query_id', 'document_id',
                                    string='Documents to Correct')
    raised_by = fields.Many2one('res.users', string='Raised By', default=lambda self: self.env.user, readonly=True)
    raised_on = fields.Datetime(default=fields.Datetime.now, readonly=True)
    state = fields.Selection([('open', 'Waiting for Vendor'), ('answered', 'Answered')],
                             default='open', required=True, index=True)
    response = fields.Text(string='Vendor Reply', readonly=True)
    answered_on = fields.Datetime(readonly=True)
    answered_by = fields.Many2one('res.users', readonly=True)
    round_no = fields.Integer(string='Round', readonly=True)

    @api.depends('vendor_id.name', 'round_no')
    def _compute_name(self):
        for rec in self:
            rec.name = _("%(vendor)s - query %(n)s", vendor=rec.vendor_id.name or '', n=rec.round_no or '')


class ResPartnerQuery(models.Model):
    _inherit = 'res.partner'

    vendor_query_ids = fields.One2many('vendor.query', 'vendor_id', string='Queries')
    vendor_query_count = fields.Integer(compute='_compute_vendor_query')
    open_query_id = fields.Many2one('vendor.query', compute='_compute_vendor_query', string='Open Query')

    @api.depends('vendor_query_ids.state')
    def _compute_vendor_query(self):
        for partner in self:
            queries = partner.vendor_query_ids
            partner.vendor_query_count = len(queries)
            partner.open_query_id = queries.filtered(lambda q: q.state == 'open')[:1]

    def action_open_query_wizard(self):
        self.ensure_one()
        self._check_group('vendor_management.group_vendor_manager')
        if self.vendor_state not in ('submitted', 'manager_review'):
            raise UserError(_("A query can be raised while the application is Submitted or in Manager Review."))
        return {
            'type': 'ir.actions.act_window',
            'name': _("Query to Vendor"),
            'res_model': 'vendor.query.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_vendor_id': self.id},
        }

    def _raise_query(self, text, documents):
        self.ensure_one()
        self._check_group('vendor_management.group_vendor_manager')
        old_state = self.vendor_state
        query = self.env['vendor.query'].sudo().create({
            'vendor_id': self.id, 'query': text, 'document_ids': [(6, 0, documents.ids)],
            'round_no': self.vendor_query_count + 1, 'raised_by': self.env.user.id,
        })
        documents.sudo().write({'query_flag': True})
        self.sudo().vendor_state = 'query'
        self._log_audit('state_change', old_value=old_state, new_value='query', action=_('Query raised'), note=text)
        docs_html = Markup('').join(Markup('<li>%s</li>') % d.doc_type_id.name for d in documents)
        body = Markup(
            '<p>Dear %(vendor)s,</p><p>We reviewed your vendor application and need some corrections '
            'before we can approve it:</p><blockquote>%(query)s</blockquote>%(docs)s'
            '<p>Please log in to the vendor portal, fix the points above and click <b>Resubmit</b>.</p>'
        ) % {'vendor': self.name, 'query': text,
             'docs': (Markup('<p>Documents to correct:</p><ul>%s</ul>') % docs_html) if documents else Markup('')}
        self.message_post(body=body, subject=_("Action needed on your vendor application"),
                          partner_ids=self.ids, message_type='comment', subtype_xmlid='mail.mt_comment')
        return query

    def action_resubmit_query(self, response=None):
        """Vendor (portal) or an internal user on the vendor's behalf answers
        the open query: the application goes back to review."""
        self.ensure_one()
        query = self.open_query_id
        if self.vendor_state != 'query' or not query:
            raise UserError(_("There is no open query to answer."))
        pending = query.document_ids.filtered('query_flag')
        if pending:
            raise UserError(_("Upload the corrected documents first: %s.",
                              ", ".join(pending.mapped('doc_type_id.name'))))
        on_behalf = self.env.user.partner_id != self.commercial_partner_id and not self.env.user.share
        reply = (response or '').strip() or (
            _("Resubmitted by %s on the vendor's behalf.", self.env.user.name) if on_behalf else _("Resubmitted."))
        query.sudo().write({'state': 'answered', 'response': reply, 'answered_on': fields.Datetime.now(),
                            'answered_by': self.env.user.id})
        self.sudo().vendor_state = 'submitted'
        self._log_audit('state_change', old_value='query', new_value='submitted', action=_('Query answered, resubmitted'),
                        note=reply)
        reviewers = group_members(self.env.ref('vendor_management.group_vendor_manager')) \
            | group_members(self.env.ref('vendor_management.group_vendor_md'))
        partners = reviewers.filtered(lambda u: u.active and not u.share).partner_id
        self.sudo().message_post(
            body=Markup('<p><b>%s</b> answered the query and resubmitted the application.</p>'
                        '<blockquote>%s</blockquote>') % (self.name, reply),
            subject=_("Vendor resubmitted: %s", self.name), partner_ids=partners.ids,
            message_type='comment', subtype_xmlid='mail.mt_comment')
        return True


class VendorDocumentQuery(models.Model):
    _inherit = 'vendor.document'

    query_flag = fields.Boolean(string='Correction Needed', copy=False, tracking=True,
                                help="A query asks the vendor to replace this document.")

    def write(self, vals):
        # Replacing the file answers the flag: whoever uploads it (portal or
        # internal) clears the "correction needed" mark.
        if vals.get('attachment') and 'query_flag' not in vals:
            vals = dict(vals, query_flag=False)
        return super().write(vals)


class VendorQueryWizard(models.TransientModel):
    _name = 'vendor.query.wizard'
    _description = 'Raise a Query to the Vendor'

    vendor_id = fields.Many2one('res.partner', required=True, readonly=True)
    query = fields.Text(string='Query', required=True,
                        help="What is wrong or missing. The vendor receives this by email and sees it on the portal.")
    document_ids = fields.Many2many(
        'vendor.document', string='Documents to Correct',
        domain="[('vendor_id', '=', vendor_id)]",
        help="The vendor must upload a fresh copy of these before resubmitting.")

    def action_send(self):
        self.ensure_one()
        if not (self.query or '').strip():
            raise UserError(_("Write the query for the vendor."))
        self.vendor_id._raise_query(self.query.strip(), self.document_ids)
        return {'type': 'ir.actions.act_window_close'}
