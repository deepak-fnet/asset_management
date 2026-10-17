import logging

from odoo import fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    dms_server_host = fields.Char("Server IP", config_parameter='dms.server_host')
    dms_server_port = fields.Integer("Port", config_parameter='dms.server_port', default=22)
    dms_server_user = fields.Char("User Name", config_parameter='dms.server_user')
    dms_server_password = fields.Char("Password", config_parameter='dms.server_password')
    dms_server_path = fields.Char("Root Path", config_parameter='dms.server_path')
    dms_cutoff_days = fields.Integer(
        "Cut-off after (days)", config_parameter='dms.cutoff_days', default=30,
        help="A superseded revision stays on screen this many days after the next revision is approved.")
    dms_theme = fields.Selection(
        [('navy', 'Navy'), ('brown', 'Brown (parchment)'), ('gold', 'Gold')],
        string="DMS Theme", config_parameter='dms.theme', default='navy',
        help="Colours of the whole DMS app: buttons, folders, the document scroll and animations.")
    dms_ai_enabled = fields.Boolean("AI", config_parameter='dms.ai_enabled', default=True)
    dms_ai_url = fields.Char("AI server URL", config_parameter='dms.ai_url', default='http://103.127.189.70:8000/v1',
                             help="OpenAI-compatible API, e.g. vLLM: http://host:8000/v1")
    dms_ai_model = fields.Char("AI model", config_parameter='dms.ai_model', default='Qwen/Qwen3-32B-AWQ')
    dms_ai_key = fields.Char("AI API key", config_parameter='dms.ai_key')
    dms_session_minutes = fields.Integer(
        "DMS login valid for (minutes)", config_parameter='dms.session_minutes', default=60)

    dms_server_encrypt = fields.Boolean(
        "Protect files on the server", config_parameter='dms.server_encrypt',
        help="Every file is stored on the DMS server inside an AES-256 password ZIP. "
             "A copy taken straight from the server needs this password.")
    dms_server_file_password = fields.Char("Server file password", config_parameter='dms.file_password')
    dms_download_protect = fields.Boolean(
        "Protect downloads", config_parameter='dms.download_protect',
        help="Files downloaded from the DMS get this password: PDF and Word/Excel/PowerPoint in their own "
             "format, other types in an AES-256 ZIP.")
    dms_download_password = fields.Char("Download password", config_parameter='dms.download_password')

    dms_alert_bulk_downloads = fields.Integer("Bulk download alert (files a day)", config_parameter='dms.alert_bulk_downloads', default=20)
    dms_alert_bulk_views = fields.Integer("Many files opened alert (files a day)", config_parameter='dms.alert_bulk_views', default=60)
    dms_alert_work_from = fields.Integer("Working hours from", config_parameter='dms.alert_work_from', default=8)
    dms_alert_work_to = fields.Integer("Working hours to", config_parameter='dms.alert_work_to', default=20)
    dms_alert_approval_days = fields.Integer("Approval overdue after (days)", config_parameter='dms.alert_approval_days', default=3)
    dms_mail_enabled = fields.Boolean("Send DMS emails", config_parameter='dms.mail_enabled', default=True,
                                      help="Approval, access, expiry, disposal and unusual-activity emails.")
    dms_expiry_alert_days = fields.Integer("Remind before expiry (days)", config_parameter='dms.expiry_alert_days', default=30)
    dms_alert_checkout_days = fields.Integer("Check-out stale after (days)", config_parameter='dms.alert_checkout_days', default=7)

    def set_values(self):
        ICP = self.env['ir.config_parameter'].sudo()
        before = (ICP.get_param('dms.server_encrypt') in ('True', '1'), ICP.get_param('dms.file_password') or '')
        for s in self:
            if s.dms_server_encrypt and not s.dms_server_file_password:
                raise UserError(_("Set the server file password to protect files on the server."))
            if s.dms_download_protect and not s.dms_download_password:
                raise UserError(_("Set the download password to protect downloads."))
            if s.dms_server_encrypt and s.dms_download_protect and s.dms_server_file_password == s.dms_download_password:
                raise UserError(_("Use two different passwords for the server copy and for downloads."))
        super().set_values()
        after = (ICP.get_param('dms.server_encrypt') in ('True', '1'), ICP.get_param('dms.file_password') or '')
        if before != after:
            # keep the old password readable while every stored file is rewritten
            ICP.set_param('dms.file_password_prev', before[1])
            done, failed = self.env['dms.storage'].reprotect_all()
            ICP.set_param('dms.file_password_prev', '' if not failed else before[1])
            self.env['dms.log'].sudo().log('access_grant', details=_(
                "Server file protection changed: %(d)s file(s) rewritten, %(f)s failed", d=done, f=len(failed)))
            if failed:
                _logger.warning("DMS re-protect failed for: %s", failed)

    def action_dms_test_connection(self):
        self.execute()
        entries = self.env['dms.storage'].test_connection()
        self.env['dms.folder']._sync_department_folders()
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'type': 'success', 'title': _("DMS server connected"),
                'message': _("%(n)s item(s) in root. Department folders synced.", n=len(entries)),
            },
        }
