# -*- coding: utf-8 -*-
"""Public self-service ticket submission form.

auth='user' (not 'public') is the whole access-control story here: Odoo's
own routing redirects an anonymous visitor to /web/login?redirect=... before
this code ever runs, and bounces them back once they log in. "If login
match, create the record - else don't" is exactly what that gets for free,
rather than hand-rolling a logged-in check and a bespoke login prompt page.

Once a real res.users is confirmed, everything the requester would
otherwise have had to type - who they are, their email/phone, department,
company - is filled in from that identity instead. They only choose what
the system cannot know on its own: subject, type, category, priority, which
of THEIR assets it concerns, and what happened.
"""
from odoo import http, _
from odoo.http import request


class HelpdeskPortalForm(http.Controller):

    def _default_ticket_type(self):
        return request.env['asset.helpdesk.type'].sudo().search(
            [('active', '=', True)], limit=1)

    def _default_team(self):
        return request.env['asset.helpdesk.team'].sudo().search(
            [('active', '=', True)], limit=1)

    def _resolve_asset_by_code(self, code):
        """The requester types the code printed on the asset's tag
        ('FN-LP-001'-style) rather than picking from a dropdown - they
        know what's on the sticker, not our internal record.

        Matched against both tag_number (the physical tag, from
        general_asset - guarded since that module is not a hard dependency
        here) and asset_code (this module's own generated code), and only
        against assets that came through the Asset List / receiving flow
        (asset_list_id set) - the same "is this a real, tracked unit"
        bar used elsewhere for this asset family.
        """
        code = (code or '').strip()
        if not code:
            return request.env['asset.asset']
        Asset = request.env['asset.asset'].sudo()
        code_domain = [('asset_code', '=ilike', code)]
        if 'tag_number' in Asset._fields:
            code_domain = ['|'] + code_domain + [('tag_number', '=ilike', code)]
        return Asset.search([
            ('is_general_asset', '=', True),
            ('asset_list_id', '!=', False),
        ] + code_domain, limit=1)

    def _form_values(self, post=None, error=None):
        return {
            'values': post or {},
            'error': error,
            'current_user': request.env.user,
        }

    @http.route('/helpdesk/submit', type='http', auth='user', website=True,
           methods=['GET'])
    def helpdesk_submit_form(self, **kwargs):
        return request.render(
            'asset_helpdesk.portal_ticket_form', self._form_values())

    @http.route('/helpdesk/submit', type='http', auth='user', website=True,
           methods=['POST'])
    def helpdesk_submit_post(self, **post):
        user = request.env.user
        subject = (post.get('subject') or '').strip()
        notes = (post.get('notes') or '').strip()
        asset_code = (post.get('asset_code') or '').strip()

        error = None
        asset = request.env['asset.asset']
        if not subject:
            error = _("Please enter a subject.")
        elif not notes:
            error = _("Please describe the issue.")
        elif asset_code:
            asset = self._resolve_asset_by_code(asset_code)
            if not asset:
                error = _(
                    "We couldn't find an asset with code '%s' - please "
                    "check the code on the asset tag and try again."
                ) % asset_code

        ticket_type = self._default_ticket_type()
        team = self._default_team()
        if not error and not ticket_type:
            error = _("No ticket type is configured yet - contact an administrator.")
        if not error and not team:
            error = _(
                "No support team is configured yet - contact an "
                "administrator before tickets can be submitted.")

        if error:
            return request.render(
                'asset_helpdesk.portal_ticket_form',
                self._form_values(post, error))

        # Classifying the issue (category, ticket type detail, priority,
        # engineer) is deliberately NOT asked of the requester - they
        # usually cannot tell hardware from software from general, and
        # guessing wrong here would just misroute the ticket. That
        # triage is the support team's job once it lands on their side.
        vals = {
            'subject': subject,
            'ticket_type_id': ticket_type.id,
            'priority': 'medium',
            'asset_id': asset.id if asset else False,
            'notes': notes,
            'team_id': team.id,
            'customer_id': user.id,
            'email': user.partner_id.email or user.login,
            'phone': user.partner_id.phone,
            'department_id': (
                user.employee_id.department_id.id
                if user.employee_id and user.employee_id.department_id
                else False),
            'company_id': user.company_id.id,
        }
        ticket = request.env['asset.helpdesk'].sudo().create(vals)
        ticket._send_portal_confirmation_email()

        return request.render(
            'asset_helpdesk.portal_ticket_success', {'ticket': ticket})
