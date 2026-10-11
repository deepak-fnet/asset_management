# -*- coding: utf-8 -*-
"""Stand-in for the incoming mailbox until IMAP is configured.

POST /helpdesk/api/mail   (JSON)  {"api_key", "from", "subject", "body"}
GET  /helpdesk/send-mail          a small page to send one by hand

Both build a real RFC 822 message and hand it to Odoo's mail gateway
(message_process), so the result is exactly what fetching the mailbox
would give: an All Mails record, a ticket number, the acknowledgement,
and "[Ticket#N]" replies threaded onto the same record.
"""
import json
import secrets
from email.message import EmailMessage
from email.utils import make_msgid, formatdate

from odoo import http, _
from odoo.http import request

API_KEY_PARAM = 'asset_helpdesk.mail_api_key'


def _api_key():
    ICP = request.env['ir.config_parameter'].sudo()
    key = ICP.get_param(API_KEY_PARAM)
    if not key:
        key = secrets.token_urlsafe(24)
        ICP.set_param(API_KEY_PARAM, key)
    return key


def _deliver(email_from, subject, body):
    msg = EmailMessage()
    msg['From'] = email_from
    msg['To'] = request.env['ir.config_parameter'].sudo().get_param(
        'asset_helpdesk.mailbox') or 'helpdesk@localhost'
    msg['Subject'] = subject or _('No Subject')
    msg['Message-ID'] = make_msgid(domain='helpdesk.api')
    msg['Date'] = formatdate(localtime=True)
    msg.set_content(body or '')
    res_id = request.env['mail.thread'].sudo().message_process(
        'asset.helpdesk.mail', msg.as_bytes())
    return request.env['asset.helpdesk.mail'].sudo().browse(res_id)


class HelpdeskMailApi(http.Controller):

    @http.route('/helpdesk/api/mail', type='http', auth='public', methods=['POST'], csrf=False)
    def api_mail(self, **kw):
        try:
            data = json.loads(request.httprequest.get_data(as_text=True) or '{}')
        except ValueError:
            data = {}
        data = dict(kw, **data)
        if data.get('api_key') != _api_key():
            return request.make_json_response({'error': 'invalid api_key'}, status=403)
        if not data.get('from') or not data.get('subject'):
            return request.make_json_response({'error': '"from" and "subject" are required'}, status=400)
        rec = _deliver(data['from'], data['subject'], data.get('body'))
        return request.make_json_response({
            'id': rec.id, 'ticket': rec.ticket_ref, 'state': rec.state,
        })

    @http.route('/helpdesk/send-mail', type='http', auth='user', website=True, methods=['GET', 'POST'])
    def send_mail_page(self, **post):
        values = {'result': None, 'error': None, 'post': post,
                  'default_from': request.env.user.email_formatted or ''}
        if request.httprequest.method == 'POST':
            if not post.get('email_from') or not post.get('subject'):
                values['error'] = _("From and Subject are required.")
            else:
                values['result'] = _deliver(post['email_from'], post['subject'], post.get('body'))
                values['post'] = {}
        return request.render('asset_helpdesk.helpdesk_send_mail_page', values)
