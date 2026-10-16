from odoo import fields, models, _


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
    dms_session_minutes = fields.Integer(
        "DMS login valid for (minutes)", config_parameter='dms.session_minutes', default=60)

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
