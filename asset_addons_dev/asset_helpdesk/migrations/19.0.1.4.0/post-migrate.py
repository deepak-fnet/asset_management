"""Build mail entries (asset.helpdesk.mail.line) for the messages already on
helpdesk mail threads."""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Mail = env['asset.helpdesk.mail']
    for thread in Mail.search([]):
        if thread.line_ids:
            continue
        messages = env['mail.message'].search([
            ('model', '=', Mail._name), ('res_id', '=', thread.id),
            ('message_type', 'in', ('email', 'comment')), ('body', '!=', False)], order='id')
        for msg in messages:
            thread._line_from_message(msg)
