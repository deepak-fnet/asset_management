import logging
import time

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from . import engine

_logger = logging.getLogger(__name__)


class RbAllowedHost(models.Model):
    _name = 'rb.allowed.host'
    _description = 'Network Browser: allowed host'
    _order = 'sequence, id'

    name = fields.Char("Host pattern", required=True,
                       help="A host name or IP; * matches anything. Examples: 192.168.105.*, *.local, intranet.mycompany.com")
    note = fields.Char("What is it")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)


class RbBookmark(models.Model):
    _name = 'rb.bookmark'
    _description = 'Network Browser: bookmark'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    url = fields.Char("Address", required=True)
    sequence = fields.Integer(default=10)
    color = fields.Char(default='#3b6fd8')
    user_id = fields.Many2one('res.users', "Only for", help="Empty: shown to everyone.")


class RbVisit(models.Model):
    _name = 'rb.visit'
    _description = 'Network Browser: page visit'
    _order = 'id desc'

    user_id = fields.Many2one('res.users', required=True, index=True, readonly=True)
    url = fields.Char(readonly=True)
    title = fields.Char(readonly=True)
    host = fields.Char(readonly=True, index=True)

    @api.model
    def _cron_reap_sessions(self):
        """Close browsers nobody used for a while (and ones whose Chrome died)."""
        idle = int(self.env['ir.config_parameter'].sudo().get_param('remote_browser.idle_minutes') or 20) * 60
        with engine.registry() as reg:
            for key, entry in list(reg.items()):
                if time.time() - entry.get('last', 0) > idle or not engine.alive(entry):
                    engine.kill(entry)
                    reg.pop(key, None)
                    _logger.info("Remote browser: closed idle session %s", key)


class RbEngine(models.AbstractModel):
    _name = 'rb.engine'
    _description = 'Network Browser engine'

    @api.model
    def _settings(self):
        icp = self.env['ir.config_parameter'].sudo()
        hosts = self.env['rb.allowed.host'].sudo().search([]).mapped('name')
        return {
            'chrome': engine.find_chrome(icp.get_param('remote_browser.chrome_path') or None),
            'patterns': hosts,
            'allow_all': icp.get_param('remote_browser.allow_all') in ('1', 'True', 'true'),
            'max': int(icp.get_param('remote_browser.max_sessions') or 5),
        }

    @api.model
    def _key(self):
        return '%s:%s' % (self.env.cr.dbname, self.env.uid)

    @api.model
    def _session(self, start=True, width=1280, height=800):
        """This user's browser entry; starts Chrome when needed."""
        key = self._key()
        with engine.registry() as reg:
            entry = reg.get(key)
            if entry and engine.alive(entry):
                if time.time() - entry.get('last', 0) > 30:  # the idle clock only needs to be rough
                    entry['last'] = time.time()
                return entry
            if entry:
                engine.kill(entry)
                reg.pop(key, None)
            if not start:
                return None
            s = self._settings()
            if not s['chrome']:
                raise UserError(_("Chrome is not installed on this server. Install google-chrome or set its path "
                                  "in Network Browser > Settings."))
            if len([k for k in reg if k.startswith(self.env.cr.dbname + ':')]) >= s['max']:
                raise UserError(_("All %s browser sessions are in use. Try again in a few minutes.", s['max']))
            entry = engine.launch(key, s['chrome'], s['patterns'], s['allow_all'], width, height)
            reg[key] = entry
            return entry

    @api.model
    def _close(self):
        key = self._key()
        with engine.registry() as reg:
            entry = reg.pop(key, None)
        if entry:
            engine.kill(entry)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    rb_chrome_path = fields.Char("Chrome path", config_parameter='remote_browser.chrome_path',
                                 help="Empty: found automatically (google-chrome, chromium).")
    rb_idle_minutes = fields.Integer("Close idle browsers after (minutes)", config_parameter='remote_browser.idle_minutes',
                                     default=20)
    rb_max_sessions = fields.Integer("Browsers at the same time", config_parameter='remote_browser.max_sessions',
                                     default=5)
    rb_allow_all = fields.Boolean("Allow every address", config_parameter='remote_browser.allow_all',
                                  help="Off: only the allowed hosts open. Changes apply to browsers started afterwards.")
