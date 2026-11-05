import re
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

LOCATION_EXTENSION_ID = 'hhofbcampdemdhpnhpfaepconmaipnjb'   # signing key kept outside git (~/.config/asset-agent-keys/)
LOCATION_EXTENSION_VERSION = '1.0.0'

SECTIONS = [
    ('system', 'System'), ('cpu', 'CPU'), ('memory', 'Memory'), ('disks', 'Disks'),
    ('processes', 'Top Processes'), ('network', 'Network'), ('services', 'Services'),
    ('packages', 'Packages'), ('hardware', 'Hardware'), ('raw', 'Raw Command Output'),
]


class AssetFleetPolicy(models.Model):
    """What Agent Telemetry collects. The agent fetches its policy before every
    report, so a change here applies at the next report without touching the
    machine."""
    _name = 'asset.fleet.policy'
    _description = 'Telemetry Policy'
    _inherit = ['mail.thread']
    _order = 'is_default desc, name'

    name = fields.Char(required=True, tracking=True)
    description = fields.Text()
    active = fields.Boolean(default=True)
    is_default = fields.Boolean('Default Policy', tracking=True,
                                help="Used by every asset that has no policy of its own.")
    enabled = fields.Boolean('Send Telemetry', default=True, tracking=True)
    revision = fields.Integer(default=1, readonly=True, copy=False)
    interval = fields.Integer('Report Every (s)', default=900, tracking=True)
    snapshot_every = fields.Integer('Keep History Every Nth Report', default=4,
                                    help="1 = every report is kept in Telemetry History.")
    section_system = fields.Boolean('System', default=True)
    section_cpu = fields.Boolean('CPU', default=True)
    section_memory = fields.Boolean('Memory', default=True)
    section_disks = fields.Boolean('Disks', default=True)
    section_processes = fields.Boolean('Top Processes', default=True)
    section_network = fields.Boolean('Network', default=True)
    section_services = fields.Boolean('Services', default=True)
    section_packages = fields.Boolean('Packages', default=True)
    section_hardware = fields.Boolean('Hardware', default=True)
    section_raw = fields.Boolean('Raw Command Output', default=True,
                                 help="df -h, free -h, lsblk, ss, top ... verbatim.")
    block_screenshot = fields.Boolean('Block Screenshots', tracking=True,
                                      help="Print Screen / GNOME screenshot UI disabled and locked, screenshot "
                                           "tools (gnome-screenshot, Flameshot...) stopped, new files in "
                                           "~/Pictures/Screenshots removed. Linux (GNOME) agents.")
    block_screen_record = fields.Boolean('Block Screen Recording', tracking=True,
                                         help="GNOME screen recorder disabled and locked, recorders (OBS, "
                                              "SimpleScreenRecorder, Kazam, Peek...) stopped, new files in "
                                              "~/Videos/Screencasts removed. Linux (GNOME) agents.")
    track_location = fields.Boolean('Precise Location (Chrome)', default=True, tracking=True,
                                    help="A small company extension is installed in Google Chrome and reports the "
                                         "laptop's position from Chrome's location service (nearby Wi-Fi, ~20-50 m) "
                                         "every 10 minutes. Without Chrome running, the agent's IP location is used.")
    track_screen_time = fields.Boolean('Track Screen Time', default=True, tracking=True,
                                       help="Daily on / active / idle / locked time and time per application "
                                            "(application names only - no window titles or content).")
    idle_after_minutes = fields.Integer(
        'Idle After (min)', default=5, tracking=True,
        help="No keyboard / mouse / touchpad input for this many minutes (screen still unlocked) "
             "and the time counts as Idle in screen time, until the user touches it again or the "
             "screen locks.")
    auto_block_capture_extensions = fields.Boolean(
        'Auto-block Capture Extensions', default=True, tracking=True,
        help="With Block Screenshots / Screen Recording on, the agent looks through every browser profile "
             "(Chrome, Edge, Brave, Chromium, Firefox) for screenshot / screen-recording extensions and blocks "
             "them - no extension IDs needed. Chrome-family browsers also refuse any extension asking for "
             "screen or tab capture.")
    blocked_websites = fields.Text(
        'Blocked Websites',
        help="One site per line, e.g. facebook.com (sub-domains included) or reddit.com/r/all. "
             "Chrome, Edge, Brave, Chromium and Firefox.")
    usb_storage = fields.Selection([('allow', 'Allow'), ('read_only', 'Read-only'), ('block', 'Block')],
                                   string='USB Storage', default='allow', required=True, tracking=True,
                                   help="Pen drives / external disks. Keyboards, mice, phones charging are "
                                        "not affected.")
    remove_admin_rights = fields.Boolean(
        'Remove Admin Rights', tracking=True,
        help="Users are taken out of the sudo / admin group (except the allowed users below) and put back "
             "when this is switched off. IT keeps root access through the agent's SSH terminal.")
    admin_allowed_users = fields.Char('Keep Admin For', help="Comma-separated login names, e.g. itadmin, support")
    blocked_apps = fields.Text(
        'Blocked Applications',
        help="One program (process) name per line - stopped within seconds whenever it starts.")
    screen_lock_minutes = fields.Integer(
        'Auto Screen Lock (min)', default=0, tracking=True,
        help="Lock the screen after this many idle minutes; users cannot change it. 0 = not enforced.")
    blocked_extensions = fields.Text(
        'Blocked Browser Extensions',
        help="One extension per line (text after the ID is ignored). Chrome / Edge / Brave / Chromium: the "
             "32-letter ID from chrome://extensions (blocked and disabled if installed). Firefox: the add-on "
             "ID, e.g. {46551ec9-40f0-4e47-8e18-8e5cf550cfb8}.")
    command_ids = fields.One2many('asset.fleet.policy.command', 'policy_id', string='Custom Commands')
    asset_ids = fields.One2many('asset.asset', 'telemetry_policy_id', string='Assets')
    asset_count = fields.Integer(compute='_compute_asset_count')

    _BUMP = {'enabled', 'interval', 'snapshot_every', 'command_ids', 'block_screenshot', 'block_screen_record',
             'blocked_extensions', 'auto_block_capture_extensions', 'track_screen_time', 'idle_after_minutes', 'track_location', 'blocked_websites', 'usb_storage',
             'remove_admin_rights', 'admin_allowed_users', 'blocked_apps', 'screen_lock_minutes'} | {'section_%s' % s for s, _l in SECTIONS}

    def _compute_asset_count(self):
        for p in self:
            p.asset_count = len(p.asset_ids)

    @api.constrains('interval')
    def _check_interval(self):
        if any(p.interval < 60 for p in self):
            raise ValidationError(_("Report interval must be at least 60 seconds."))

    def write(self, vals):
        res = super().write(vals)
        if self._BUMP & set(vals):
            self._bump()
        if vals.get('is_default'):
            self.search([('id', 'not in', self.ids), ('is_default', '=', True)]).write({'is_default': False})
        return res

    def _bump(self):
        for p in self:
            super(AssetFleetPolicy, p).write({'revision': p.revision + 1})

    @api.model
    def _for_asset(self, asset):
        return asset.telemetry_policy_id or self.search([('is_default', '=', True)], limit=1)

    def _payload(self):
        self.ensure_one()
        return {
            'name': self.name, 'revision': self.revision, 'enabled': self.enabled,
            'interval': self.interval, 'snapshot_every': self.snapshot_every,
            'sections': [],   # telemetry = the custom commands only
            'track_screen_time': self.track_screen_time,
            'idle_after_minutes': self.idle_after_minutes or 5,
            'location_extension': self._location_extension() if self.track_location else False,
            'restrictions': {'block_screenshot': self.block_screenshot,
                             'block_screen_record': self.block_screen_record,
                             'blocked_extensions': self._extension_ids(),
                             'auto_block_capture_extensions': self.auto_block_capture_extensions,
                             'blocked_websites': self._lines(self.blocked_websites),
                             'usb_storage': self.usb_storage,
                             'remove_admin_rights': self.remove_admin_rights,
                             'admin_allowed_users': [u.strip() for u in (self.admin_allowed_users or '').split(',')
                                                     if u.strip()],
                             'blocked_apps': self._lines(self.blocked_apps),
                             'screen_lock_minutes': self.screen_lock_minutes},
            'commands': [{'name': c.name, 'key': c.key, 'command': c.command, 'platform': c.platform,
                          'interval': c.interval, 'timeout': c.timeout} for c in self.command_ids if c.active],
        }

    def _location_extension(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        tok = self.env['asset.fleet.enrollment.token'].sudo().search([('active', '=', True)], limit=1)
        base = (tok.server_url or base).rstrip('/')
        return {'id': LOCATION_EXTENSION_ID,
                'update_url': '%s/asset/agent/chrome/update.xml?db=%s' % (base, self.env.cr.dbname),
                'server': base, 'db': self.env.cr.dbname, 'token': tok.token or ''}

    @staticmethod
    def _lines(text):
        """First word of every non-empty, non-comment line (text after it is a note)."""
        out = []
        for line in (text or '').splitlines():
            token = line.strip().split()[0] if line.strip() else ''
            if token and not token.startswith('#'):
                out.append(token)
        return out

    def _extension_ids(self):
        return self._lines(self.blocked_extensions)

    @api.constrains('screen_lock_minutes', 'idle_after_minutes')
    def _check_lock(self):
        if any(p.screen_lock_minutes < 0 for p in self):
            raise ValidationError(_("Screen lock minutes cannot be negative."))
        if any(p.idle_after_minutes < 1 for p in self):
            raise ValidationError(_("Idle After must be at least 1 minute."))

    def action_view_assets(self):
        return {'type': 'ir.actions.act_window', 'name': _('Assets'), 'res_model': 'asset.asset',
                'view_mode': 'list,form', 'domain': [('telemetry_policy_id', 'in', self.ids)]}


class AssetFleetPolicyCommand(models.Model):
    _name = 'asset.fleet.policy.command'
    _description = 'Telemetry Policy Command'
    _order = 'sequence, id'

    policy_id = fields.Many2one('asset.fleet.policy', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    name = fields.Char(required=True)
    key = fields.Char(required=True, help="Stored in the telemetry under custom.<key>.")
    command = fields.Text(required=True, help="Shell command, run as root by the agent.")
    platform = fields.Selection([('all', 'All'), ('linux', 'Linux'), ('windows', 'Windows'), ('macos', 'macOS')],
                                default='linux', required=True)
    interval = fields.Integer('Run Every (s)', default=3600)
    timeout = fields.Integer('Timeout (s)', default=60)

    @api.constrains('key')
    def _check_key(self):
        for c in self:
            if not re.fullmatch(r'[a-z0-9_]+', c.key or ''):
                raise ValidationError(_("Key: lower-case letters, digits and _ only."))

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs.policy_id._bump()
        return recs

    def write(self, vals):
        res = super().write(vals)
        self.policy_id._bump()
        return res

    def unlink(self):
        policies = self.policy_id
        res = super().unlink()
        policies._bump()
        return res


class AssetFleetEnrollmentToken(models.Model):
    """Agent install link: the server / relay addresses the agent is set up
    with, and the token that allows downloading it."""
    _name = 'asset.fleet.enrollment.token'
    _description = 'Agent Install Link'

    name = fields.Char(required=True)
    token = fields.Char(readonly=True, copy=False, default=lambda s: secrets.token_urlsafe(24))
    active = fields.Boolean(default=True)
    server_url = fields.Char('Odoo URL (agent data)', required=True,
                             default=lambda s: s.env['ir.config_parameter'].sudo().get_param('web.base.url', ''),
                             help="Address the agent sends its data to - must be reachable from the machines.")
    relay_url = fields.Char('Relay URL (remote terminal / screen)', compute='_compute_relay_url',
                            inverse='_inverse_relay_url',
                            help="WebSocket relay for SSH terminal and screen sharing (system parameter "
                                 "asset_remote.relay_url, one for all agents).")
    install_linux = fields.Text('Ubuntu / Linux', compute='_compute_install')

    _token_uniq = models.Constraint('UNIQUE(token)', 'Token must be unique.')

    def _compute_relay_url(self):
        relay = self.env['ir.config_parameter'].sudo().get_param('asset_remote.relay_url', '')
        for t in self:
            t.relay_url = relay

    def _inverse_relay_url(self):
        for t in self:
            if t.relay_url:
                self.env['ir.config_parameter'].sudo().set_param('asset_remote.relay_url', t.relay_url.strip())

    @api.constrains('server_url', 'relay_url')
    def _check_urls(self):
        for t in self:
            if t.server_url and not re.match(r'^https?://', t.server_url):
                raise ValidationError(_("Odoo URL must start with http:// or https://"))
            if t.relay_url and not re.match(r'^wss?://', t.relay_url):
                raise ValidationError(_("Relay URL must start with ws:// or wss://"))

    def _compute_install(self):
        db = self.env.cr.dbname
        for t in self:
            t.install_linux = 'curl -fsSL "%s/asset/agent/install.sh?token=%s&db=%s" | sudo bash' % (
                (t.server_url or '').rstrip('/'), t.token, db)

    def action_regenerate(self):
        for t in self:
            t.token = secrets.token_urlsafe(24)

    @api.model
    def _get_valid(self, token):
        if not token:
            return self.browse()
        return self.sudo().search([('token', '=', token), ('active', '=', True)], limit=1)


class AssetAsset(models.Model):
    _inherit = 'asset.asset'

    location_accuracy = fields.Float('Location Accuracy (m)', readonly=True,
                                     help="Radius the Chrome location service is sure of.")
    telemetry_policy_id = fields.Many2one('asset.fleet.policy', string='Telemetry Policy',
                                          help="Empty = the default telemetry policy.")
    policy_enforcement = fields.Html('Policy on this Machine', compute='_compute_policy_enforcement',
                                     sanitize=False)

    @api.depends('agent_telemetry')
    def _compute_policy_enforcement(self):
        from markupsafe import Markup, escape
        for a in self:
            r = (a.agent_telemetry or {}).get('restrictions') if isinstance(a.agent_telemetry, dict) else None
            if not r:
                a.policy_enforcement = False
                continue
            rows = []
            for e in r.get('detected_extensions') or []:
                rows.append(Markup('<tr><td>%s</td><td>%s</td><td><code>%s</code></td></tr>') % (
                    escape(e.get('browser', '').title()), escape(e.get('name', '')), escape(e.get('id', ''))))
            ext = (Markup('<table class="table table-sm mb-2"><thead><tr><th>Browser</th>'
                          '<th>Blocked extension (found automatically)</th><th>ID</th></tr></thead>'
                          '<tbody>%s</tbody></table>') % Markup('').join(rows)) if rows else Markup(
                '<p class="text-muted mb-2">No screenshot / recording extension found.</p>')
            facts = Markup(' &middot; ').join(Markup('%s: <b>%s</b>') % (k, escape(str(v))) for k, v in (
                ('Screenshots blocked', r.get('screenshot')), ('Recording blocked', r.get('screen_record')),
                ('USB', r.get('usb_storage')), ('Websites', r.get('blocked_websites')),
                ('Admin removed', ', '.join(r.get('admin_removed') or []) or '-'),
                ('Screen lock', '%s min' % r.get('screen_lock_minutes') if r.get('screen_lock_minutes') else 'off'),
                ('Captures removed', r.get('removed_files')), ('Apps stopped', r.get('blocked_apps_killed')),
            ) if v is not None)
            a.policy_enforcement = ext + Markup('<p class="small">%s</p>') % facts
