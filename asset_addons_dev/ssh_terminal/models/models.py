from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from datetime import timedelta
import base64
import os
import paramiko
import logging

_logger = logging.getLogger(__name__)


class SSHEncryptionKey(models.Model):
    """Store master encryption key - only system admin can access"""
    _name = 'ssh.encryption.key'
    _description = 'SSH Encryption Key'

    name = fields.Char(default='Master Key', readonly=True)
    key = fields.Char(string='Encryption Key', readonly=True)

    @api.model
    def _get_or_create_key(self):
        """Get or create the master encryption key"""
        key_record = self.sudo().search([], limit=1)
        if not key_record:
            # Generate a new Fernet key
            new_key = Fernet.generate_key().decode()
            key_record = self.sudo().create({'key': new_key})
        return key_record.key

    @api.model
    def encrypt(self, data):
        """Encrypt sensitive data"""
        if not data:
            return False
        key = self._get_or_create_key()
        f = Fernet(key.encode())
        return f.encrypt(data.encode()).decode()

    @api.model
    def decrypt(self, encrypted_data):
        """Decrypt sensitive data"""
        if not encrypted_data:
            return False
        key = self._get_or_create_key()
        f = Fernet(key.encode())
        return f.decrypt(encrypted_data.encode()).decode()


class SSHServer(models.Model):
    """Server configuration - only IT Admin can see/edit"""
    _name = 'ssh.server'
    _description = 'SSH Server Configuration'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Server Name', required=True, tracking=True)
    description = fields.Text(string='Description')

    # Encrypted fields - stored encrypted in database
    ip_encrypted = fields.Char(string='IP (Encrypted)', copy=False)
    port_encrypted = fields.Char(string='Port (Encrypted)', copy=False)

    # Virtual fields for input (not stored)
    ip_input = fields.Char(string='IP Address', store=False, compute='_compute_dummy', inverse='_inverse_ip')
    port_input = fields.Integer(string='Port', store=False, compute='_compute_dummy_port', inverse='_inverse_port',
                                default=22)

    # Display fields (masked)
    ip_masked = fields.Char(string='Stored IP', compute='_compute_masked_values')

    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    # Credentials - multiple credentials per server
    credential_ids = fields.One2many('ssh.credential', 'server_id', string='Credentials')

    # Access control
    allowed_group_ids = fields.Many2many('res.groups', string='Allowed Groups',
                                         help='User groups that can request access to this server')

    @api.depends_context('uid')
    def _compute_dummy(self):
        for rec in self:
            rec.ip_input = ''

    @api.depends_context('uid')
    def _compute_dummy_port(self):
        for rec in self:
            rec.port_input = 22

    def _inverse_ip(self):
        encryption = self.env['ssh.encryption.key']
        for rec in self:
            if rec.ip_input:
                rec.ip_encrypted = encryption.encrypt(rec.ip_input)

    def _inverse_port(self):
        encryption = self.env['ssh.encryption.key']
        for rec in self:
            if rec.port_input:
                rec.port_encrypted = encryption.encrypt(str(rec.port_input))

    @api.depends('ip_encrypted')
    def _compute_masked_values(self):
        """Show masked IP to authorized users"""
        for rec in self:
            if rec.ip_encrypted and self.env.user.has_group('ssh_terminal.group_ssh_admin'):
                try:
                    ip = self.env['ssh.encryption.key'].decrypt(rec.ip_encrypted)
                    parts = ip.split('.')
                    rec.ip_masked = f"{parts[0]}.***.***.{parts[-1]}" if len(parts) == 4 else '***'
                except:
                    rec.ip_masked = '***'
            else:
                rec.ip_masked = '***.***.***'

    def _get_decrypted_ip(self):
        """Internal method to get decrypted IP - only for connection"""
        self.ensure_one()
        return self.env['ssh.encryption.key'].sudo().decrypt(self.ip_encrypted)

    def _get_decrypted_port(self):
        """Internal method to get decrypted port"""
        self.ensure_one()
        port_str = self.env['ssh.encryption.key'].sudo().decrypt(self.port_encrypted)
        return int(port_str) if port_str else 22


class SSHCredential(models.Model):
    """SSH Credentials - highly restricted"""
    _name = 'ssh.credential'
    _description = 'SSH Credential'

    name = fields.Char(string='Credential Name', required=True)
    server_id = fields.Many2one('ssh.server', required=True, ondelete='cascade')

    # Encrypted credentials
    username_encrypted = fields.Char(string='Username (Encrypted)')
    password_encrypted = fields.Char(string='Password (Encrypted)')

    # Virtual fields for input
    username_input = fields.Char(string='Username', store=False, compute='_compute_dummy', inverse='_inverse_username')
    password_input = fields.Char(string='Password', store=False, compute='_compute_dummy', inverse='_inverse_password')

    # Display
    username_masked = fields.Char(compute='_compute_masked')

    active = fields.Boolean(default=True)

    # Access level
    access_level = fields.Selection([
        ('readonly', 'Read Only'),
        ('standard', 'Standard'),
        ('admin', 'Administrator')
    ], default='standard', required=True)

    @api.depends_context('uid')
    def _compute_dummy(self):
        for rec in self:
            rec.username_input = ''
            rec.password_input = ''

    def _inverse_username(self):
        encryption = self.env['ssh.encryption.key']
        for rec in self:
            if rec.username_input:
                rec.username_encrypted = encryption.encrypt(rec.username_input)

    def _inverse_password(self):
        encryption = self.env['ssh.encryption.key']
        for rec in self:
            if rec.password_input:
                rec.password_encrypted = encryption.encrypt(rec.password_input)

    @api.depends('username_encrypted')
    def _compute_masked(self):
        for rec in self:
            if rec.username_encrypted and self.env.user.has_group('ssh_terminal.group_ssh_admin'):
                try:
                    username = self.env['ssh.encryption.key'].decrypt(rec.username_encrypted)
                    rec.username_masked = username[:2] + '***' if len(username) > 2 else '***'
                except:
                    rec.username_masked = '***'
            else:
                rec.username_masked = '***'


class SSHAccessRequest(models.Model):
    """Access request workflow"""
    _name = 'ssh.access.request'
    _description = 'SSH Access Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(string='Request Reference', readonly=True, copy=False, default='New')

    user_id = fields.Many2one('res.users', string='Requested By',
                              default=lambda self: self.env.user, readonly=True, required=True)
    server_id = fields.Many2one('ssh.server', string='Server', required=True,
                                domain="[('active', '=', True)]", tracking=True)
    credential_id = fields.Many2one('ssh.credential', string='Access Level',
                                    domain="[('server_id', '=', server_id), ('active', '=', True)]", tracking=True)

    reason = fields.Text(string='Reason for Access', required=True)

    # Time-based access
    access_start = fields.Datetime(string='Access Start', default=fields.Datetime.now)
    access_end = fields.Datetime(string='Access End', required=True)
    duration_hours = fields.Float(string='Duration (Hours)', compute='_compute_duration', store=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Pending Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('expired', 'Expired'),
        ('revoked', 'Revoked')
    ], default='draft', tracking=True, string='Status')

    # Approval
    approver_id = fields.Many2one('res.users', string='Approved/Rejected By', readonly=True)
    approval_date = fields.Datetime(string='Approval Date', readonly=True)
    rejection_reason = fields.Text(string='Rejection Reason')

    # Access token for secure connection
    access_token = fields.Char(string='Access Token', readonly=True, copy=False)

    # Audit
    connection_count = fields.Integer(string='Connection Count', default=0, readonly=True)
    last_connection = fields.Datetime(string='Last Connection', readonly=True)
    connection_log_ids = fields.One2many('ssh.connection.log', 'request_id', string='Connection Logs')

    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    @api.depends('access_start', 'access_end')
    def _compute_duration(self):
        for rec in self:
            if rec.access_start and rec.access_end:
                delta = rec.access_end - rec.access_start
                rec.duration_hours = delta.total_seconds() / 3600
            else:
                rec.duration_hours = 0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('ssh.access.request') or 'New'
        return super().create(vals_list)

    def action_submit(self):
        """Submit request for approval"""
        self.ensure_one()
        if not self.reason:
            raise UserError(_('Please provide a reason for access.'))
        if self.access_end <= fields.Datetime.now():
            raise UserError(_('Access end time must be in the future.'))

        self.state = 'submitted'

        # Notify managers
        self._notify_managers()

    def action_approve(self):
        """Approve the request"""
        self.ensure_one()
        if not self.env.user.has_group('ssh_terminal.group_ssh_manager'):
            raise AccessError(_('Only SSH Managers can approve requests.'))

        # Generate secure access token
        token = base64.urlsafe_b64encode(os.urandom(32)).decode()

        self.write({
            'state': 'approved',
            'approver_id': self.env.user.id,
            'approval_date': fields.Datetime.now(),
            'access_token': token,
        })

        # Notify user
        self._notify_user_approved()

    def action_reject(self):
        """Open rejection wizard"""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Reject Request',
            'res_model': 'ssh.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_request_id': self.id}
        }

    def action_revoke(self):
        """Revoke access"""
        self.ensure_one()
        self.state = 'revoked'
        # TODO: Force disconnect any active sessions

    # ── Kanban helper fields ─────────────────────────────────
    access_remaining = fields.Char(
        string='Time Remaining', compute='_compute_access_remaining')
    is_connectable = fields.Boolean(
        compute='_compute_is_connectable')

    @api.depends('state', 'access_start', 'access_end')
    def _compute_access_remaining(self):
        now = fields.Datetime.now()
        for rec in self:
            if rec.state != 'approved' or not rec.access_end:
                rec.access_remaining = ''
            else:
                delta = rec.access_end - now
                if delta.total_seconds() <= 0:
                    rec.access_remaining = 'Expired'
                elif delta.days > 0:
                    rec.access_remaining = f'{delta.days}d {delta.seconds // 3600}h left'
                elif delta.seconds >= 3600:
                    rec.access_remaining = f'{delta.seconds // 3600}h {(delta.seconds % 3600) // 60}m left'
                else:
                    rec.access_remaining = f'{delta.seconds // 60}m left'

    @api.depends('state', 'access_start', 'access_end')
    def _compute_is_connectable(self):
        now = fields.Datetime.now()
        for rec in self:
            rec.is_connectable = (
                rec.state == 'approved'
                and rec.access_start
                and rec.access_end
                and rec.access_start <= now <= rec.access_end
            )

    # ── Actions ───────────────────────────────────────────────

    def action_connect(self):
        """Open terminal — only if approved and within time window"""
        self.ensure_one()
        self._validate_access()

        self.sudo().write({
            'connection_count': self.connection_count + 1,
            'last_connection': fields.Datetime.now(),
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'ssh_terminal_widget',
            'target': 'current',
            'context': {
                'access_request_id': self.id,
                'access_token': self.access_token,
            }
        }

    def action_connect_sftp(self):
        """Open SFTP file manager — only if approved and within time window"""
        self.ensure_one()
        self._validate_access()

        self.sudo().write({
            'connection_count': self.connection_count + 1,
            'last_connection': fields.Datetime.now(),
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'ssh_sftp_widget',
            'target': 'current',
            'context': {
                'access_request_id': self.id,
                'access_token': self.access_token,
            }
        }

    def action_view_connections(self):
        """View connection logs for this request"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Connection Logs - {self.name}',
            'res_model': 'ssh.connection.log',
            'view_mode': 'list,form',
            'domain': [('request_id', '=', self.id)],
            'context': {'default_request_id': self.id}
        }

    def _validate_access(self):
        """Validate that user has valid access"""
        self.ensure_one()

        if self.state != 'approved':
            raise AccessError(_('This access request is not approved.'))

        if self.user_id != self.env.user:
            raise AccessError(_('This access request belongs to another user.'))

        now = fields.Datetime.now()
        if now < self.access_start:
            raise AccessError(_('Access period has not started yet.'))

        if now > self.access_end:
            self.sudo().state = 'expired'
            raise AccessError(_('Access period has expired.'))

    def _notify_managers(self):
        """Send notification to SSH managers"""
        template = self.env.ref('ssh_terminal.email_template_access_request', raise_if_not_found=False)
        if template:
            template.send_mail(self.id, force_send=True)

    def _notify_user_approved(self):
        """Notify user their request was approved"""
        template = self.env.ref('ssh_terminal.email_template_access_approved', raise_if_not_found=False)
        if template:
            template.send_mail(self.id, force_send=True)

    @api.model
    def _cron_expire_requests(self):
        """Cron job to expire old requests"""
        expired = self.search([
            ('state', '=', 'approved'),
            ('access_end', '<', fields.Datetime.now())
        ])
        expired.write({'state': 'expired'})


class SSHConnectionLog(models.Model):
    """Audit log for all SSH connections"""
    _name = 'ssh.connection.log'
    _description = 'SSH Connection Log'
    _order = 'create_date desc'

    request_id = fields.Many2one('ssh.access.request', required=True, ondelete='cascade')
    user_id = fields.Many2one('res.users', required=True)
    server_id = fields.Many2one('ssh.server', required=True)

    session_id = fields.Char(string='Session ID')
    connected_at = fields.Datetime(string='Connected At', default=fields.Datetime.now)
    disconnected_at = fields.Datetime(string='Disconnected At')
    duration_seconds = fields.Integer(string='Duration (s)', compute='_compute_duration', store=True)

    ip_address = fields.Char(string='Client IP')
    user_agent = fields.Char(string='User Agent')

    # Command logging - stores all commands executed during session
    command_log = fields.Text(string='Command Log', help='All commands executed during this session')
    html_command_log = fields.Html(default="<p>No history yet.</p>", sanitize=False,compute="_update_command_log_html")

    def _convert_command_log_to_html(self, command_log_text):
        """Convert plain text command log to HTML table format"""
        self.ensure_one()

        if not command_log_text:
            return ""

        # Parse the command log lines
        import re
        lines = command_log_text.strip().split('\n')
        rows = []

        for line in lines:
            # Match pattern: [timestamp] command
            match = re.match(r'\[([\d\-:\s]+)\]\s+(.+)', line.strip())
            if match:
                timestamp = match.group(1)
                command = match.group(2)
                rows.append((timestamp, command))

        if not rows:
            return ""

        # Build HTML table
        table_header = '''<table style="width:100%;border-collapse:collapse;font-size:13px;">
    <thead>
    <tr style="background-color:#343a40;color:white;">
    <th style="padding:8px;border:1px solid #dee2e6;">Time</th>
    <th style="padding:8px;border:1px solid #dee2e6;">Command</th>
    </tr>
    </thead>
    <tbody>'''

        table_rows = ""
        for timestamp, command in rows:
            table_rows += f'''<tr style="border-bottom:1px solid #dee2e6;">
    <td style="padding:8px;border:1px solid #dee2e6;">{timestamp}</td>
    <td style="padding:8px;border:1px solid #dee2e6;">{command}</td>
    </tr>'''

        table_footer = '''</tbody>
    </table>'''

        return table_header + table_rows + table_footer

    @api.depends('command_log')
    def _update_command_log_html(self):
        """Update the html_command_log field from command_log field"""
        for rec in self:
            rec.html_command_log = rec._convert_command_log_to_html(rec.command_log)

    # Disconnection reason
    disconnect_reason = fields.Selection([
        ('user', 'User Disconnected'),
        ('timeout', 'Session Timeout'),
        ('access_expired', 'Access Period Expired'),
        ('revoked', 'Access Revoked'),
        ('server_restart', 'Server Restarted'),
        ('error', 'Connection Error')
    ], string='Disconnect Reason')

    @api.depends('connected_at', 'disconnected_at')
    def _compute_duration(self):
        for rec in self:
            if rec.connected_at and rec.disconnected_at:
                delta = rec.disconnected_at - rec.connected_at
                rec.duration_seconds = int(delta.total_seconds())
            else:
                rec.duration_seconds = 0

    @api.model
    def _close_orphaned_sessions(self):
        """Cron job to close sessions that are still open after server restart"""
        # Find sessions that are still open (no disconnected_at) but created more than 1 hour ago
        orphaned = self.search([
            ('disconnected_at', '=', False),
            ('create_date', '<', fields.Datetime.now() - timedelta(hours=1))
        ])

        for session in orphaned:
            session.write({
                'disconnected_at': fields.Datetime.now(),
                'disconnect_reason': 'server_restart'
            })
            _logger.info(f"Closed orphaned session {session.session_id} due to server restart")