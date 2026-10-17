from odoo import http, fields
from odoo.http import request
from odoo.exceptions import AccessError
import paramiko
import time
import threading
import queue
import logging
import hashlib
import socket
import re
import stat
import base64
import codecs
import os

from odoo import api, SUPERUSER_ID
from odoo.modules.registry import Registry

_logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Sensitive-data patterns to redact from command logs
# ─────────────────────────────────────────────────────────────────
SENSITIVE_PATTERNS = [
    # GitHub / GitLab tokens
    re.compile(r'(ghp_[A-Za-z0-9]{36,})'),
    re.compile(r'(glpat-[A-Za-z0-9\-]{20,})'),
    re.compile(r'(github_pat_[A-Za-z0-9_]{22,})'),
    # Generic password/token flags
    re.compile(r'(--password[= ]\S+)', re.IGNORECASE),
    re.compile(r'(--token[= ]\S+)', re.IGNORECASE),
    re.compile(r'(-p\s?\S+)'),
    # AWS keys
    re.compile(r'(AKIA[0-9A-Z]{16})'),
    re.compile(r'(AWS_SECRET_ACCESS_KEY[= ]\S+)', re.IGNORECASE),
    # Inline secrets
    re.compile(r'(sshpass\s+-p\s+\S+)', re.IGNORECASE),
    re.compile(r'([A-Z_]*PASSWORD[= ]\S+)', re.IGNORECASE),
    re.compile(r'([A-Z_]*SECRET[= ]\S+)', re.IGNORECASE),
    re.compile(r'([A-Z_]*TOKEN[= ]\S+)', re.IGNORECASE),
    re.compile(r'([A-Z_]*API_KEY[= ]\S+)', re.IGNORECASE),
]


def redact_sensitive(text):
    """Replace known sensitive patterns with [REDACTED]."""
    if not text:
        return text
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub('[REDACTED]', text)
    return text


# ─────────────────────────────────────────────────────────────────
# SSH Session
# ─────────────────────────────────────────────────────────────────

class SSHSession:
    """Handles individual SSH connection lifecycle"""

    def __init__(self, connection_data, session_id):
        self.ssh = None
        self.channel = None
        self.connection_data = connection_data
        self.session_id = session_id
        self.output_queue = queue.Queue()
        self.is_connected = False
        self.created_at = time.time()
        self.last_activity = time.time()
        self.reader_thread = None
        self._lock = threading.Lock()
        self.command_buffer = []

        # ── Server-side line tracking ──────────────────────────
        # Reconstructs the current prompt line from channel output
        # so history-recalled commands (Up arrow) are captured.
        self._output_line_buffer = ''
        self._pending_enter = False
        self._user_tz = 'UTC'
        self._channel_broken = False
        # bytes can split a UTF-8 character between two reads: decode as a stream
        self._decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self._data_ready = threading.Event()

    def connect(self):
        """Establish SSH connection"""
        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            self.ssh.connect(
                hostname=self.connection_data['ip'],
                port=self.connection_data['port'],
                username=self.connection_data['username'],
                password=self.connection_data['password'],
                timeout=10,
                look_for_keys=False,
                allow_agent=False,
                banner_timeout=30
            )

            # ── Clear plaintext credentials from memory ────────
            self.connection_data.clear()

            self.channel = self.ssh.invoke_shell(term='xterm-256color', width=120, height=30)
            self.channel.settimeout(0.1)
            self.is_connected = True

            self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
            self.reader_thread.start()

            _logger.info(f"SSH connection established for session {self.session_id}")
            return True

        except paramiko.AuthenticationException:
            self.connection_data.clear()
            _logger.error(f"SSH Authentication failed for session {self.session_id}")
            return False
        except paramiko.SSHException as e:
            self.connection_data.clear()
            _logger.error(f"SSH error for session {self.session_id}: {str(e)}")
            return False
        except Exception as e:
            self.connection_data.clear()
            _logger.error(f"SSH Connection error for session {self.session_id}: {str(e)}")
            return False

    # ── ANSI escape stripper ──────────────────────────────────
    _ansi_escape_re = re.compile(
        r'\x1b\[[0-9;]*[A-Za-z]'    # CSI sequences
        r'|\x1b\].*?(?:\x07|\x1b\\)'  # OSC sequences
        r'|\x1b[()][0-9A-Za-z]'       # charset sequences
        r'|\r'                          # carriage return
    )

    def _strip_ansi(self, text):
        return self._ansi_escape_re.sub('', text)

    def _read_output(self):
        """Background thread to continuously read channel output"""
        while self.is_connected and self.channel:
            try:
                if self.channel.recv_ready():
                    raw = self.channel.recv(32768)
                    if not raw:
                        self._channel_broken = True
                        self._data_ready.set()
                        break
                    data = self._decoder.decode(raw)
                    if data:
                        self.output_queue.put(data)
                        self._data_ready.set()
                        self.last_activity = time.time()

                        # ── Track the current line from server output ──
                        clean = self._strip_ansi(data)
                        for ch in clean:
                            if ch == '\n':
                                self._flush_line_if_enter()
                                self._output_line_buffer = ''
                            elif ch == '\x08':
                                self._output_line_buffer = self._output_line_buffer[:-1]
                            else:
                                self._output_line_buffer += ch
                else:
                    time.sleep(0.01)

                # ── Detect broken / closed channel ─────────────
                if self.channel.closed or self.channel.eof_received:
                    _logger.info(f"Channel closed for session {self.session_id}")
                    self._channel_broken = True
                    break

                transport = self.channel.get_transport()
                if transport is None or not transport.is_active():
                    _logger.info(f"Transport dead for session {self.session_id}")
                    self._channel_broken = True
                    break

            except socket.timeout:
                continue
            except EOFError:
                _logger.info(f"EOF on channel for session {self.session_id}")
                self._channel_broken = True
                break
            except OSError as e:
                _logger.info(f"OS error (connection lost?) for session {self.session_id}: {e}")
                self._channel_broken = True
                break
            except Exception as e:
                if self.is_connected:
                    _logger.error(f"Read error for session {self.session_id}: {str(e)}")
                self._channel_broken = True
                break

    def _flush_line_if_enter(self):
        """
        If the client notified us that Enter was pressed, record the
        current output-line content as a captured command.
        """
        if self._pending_enter and self._output_line_buffer.strip():
            line = self._output_line_buffer.strip()
            # Extract the command part after the last prompt indicator
            for marker in ['$ ', '# ', '> ', '% ']:
                idx = line.rfind(marker)
                if idx != -1:
                    line = line[idx + len(marker):]
                    break
            if line.strip():
                self._record_command(line.strip())
            self._pending_enter = False

    def _record_command(self, command_text, user_tz=None):
        """Record a command with timestamp, redacting sensitive data."""
        if not command_text or not command_text.strip():
            return

        tz = user_tz or self._user_tz or 'UTC'

        from datetime import datetime
        import pytz

        utc_now = datetime.utcnow().replace(tzinfo=pytz.UTC)
        try:
            user_timezone = pytz.timezone(tz)
            local_time = utc_now.astimezone(user_timezone)
        except Exception:
            ist_tz = pytz.timezone('Asia/Kolkata')
            local_time = utc_now.astimezone(ist_tz)

        timestamp = local_time.strftime('%Y-%m-%d %H:%M:%S')
        safe_command = redact_sensitive(command_text.strip())
        self.command_buffer.append(f"[{timestamp}] {safe_command}")
        _logger.debug(f"Captured command for session {self.session_id}: {safe_command}")

    def notify_enter(self, user_tz='UTC'):
        """
        Called by the controller when the client sends Enter.
        Sets a flag so the reader thread records the current line.
        """
        self._user_tz = user_tz
        self._pending_enter = True

        # Deferred fallback: if the reader already processed the
        # newline before we set the flag, try again after a short wait.
        def _deferred_capture():
            time.sleep(0.25)
            if self._pending_enter:
                self._flush_line_if_enter()

        threading.Thread(target=_deferred_capture, daemon=True).start()

    def capture_command(self, command_text, user_tz='UTC'):
        """Capture a client-side reported completed command (fallback)."""
        self._record_command(command_text, user_tz)

    def send_command(self, command):
        """Send command to SSH channel"""
        with self._lock:
            if self.channel and self.is_connected:
                try:
                    # sendall: a long paste must never be cut short
                    self.channel.sendall(command.encode('utf-8'))
                    self.last_activity = time.time()
                    return True
                except Exception as e:
                    _logger.error(f"Send error for session {self.session_id}: {str(e)}")
                    self._channel_broken = True
                    return False
            return False

    def get_command_log(self):
        """Get all captured commands as formatted string"""
        return '\n'.join(self.command_buffer)

    def get_output(self, wait=0):
        """Retrieve all queued output; with wait, hold up to that many seconds for the first bytes."""
        if wait and self.output_queue.empty() and not self._channel_broken:
            self._data_ready.wait(wait)
        self._data_ready.clear()
        output = []
        try:
            while not self.output_queue.empty():
                output.append(self.output_queue.get_nowait())
        except queue.Empty:
            pass
        return ''.join(output)

    def is_channel_broken(self):
        """Check if the remote side dropped the connection."""
        if self._channel_broken:
            return True
        if self.channel is None:
            return True
        if self.channel.closed or self.channel.eof_received:
            self._channel_broken = True
            return True
        try:
            transport = self.channel.get_transport()
            if transport is None or not transport.is_active():
                self._channel_broken = True
                return True
        except Exception:
            self._channel_broken = True
            return True
        return False

    def resize(self, cols, rows):
        """Resize terminal PTY"""
        with self._lock:
            if self.channel and self.is_connected:
                try:
                    self.channel.resize_pty(width=cols, height=rows)
                except Exception as e:
                    _logger.error(f"Resize error for session {self.session_id}: {str(e)}")

    def close(self):
        """Clean up SSH connection"""
        self.is_connected = False

        if self.channel:
            try:
                self.channel.close()
            except Exception:
                pass
            self.channel = None

        if self.ssh:
            try:
                self.ssh.close()
            except Exception:
                pass
            self.ssh = None

        _logger.info(f"SSH session {self.session_id} closed")


# ─────────────────────────────────────────────────────────────────
# Controller
# ─────────────────────────────────────────────────────────────────

class SecureSSHTerminalController(http.Controller):
    """Secure SSH Terminal with comprehensive access validation"""

    sessions = {}
    _lock = threading.Lock()
    _cleanup_thread = None
    _cleanup_running = False

    @classmethod
    def _start_cleanup_thread(cls):
        if not cls._cleanup_running:
            cls._cleanup_running = True
            cls._cleanup_thread = threading.Thread(target=cls._cleanup_expired_sessions, daemon=True)
            cls._cleanup_thread.start()

    @classmethod
    def _cleanup_expired_sessions(cls):
        while cls._cleanup_running:
            try:
                time.sleep(60)
                current_time = time.time()
                expired_sessions = []

                with cls._lock:
                    for session_id, session_data in list(cls.sessions.items()):
                        session = session_data['session']
                        if current_time - session.last_activity > 1800:
                            expired_sessions.append((session_id, 'timeout'))
                        elif current_time - session_data['created_at'] > 7200:
                            expired_sessions.append((session_id, 'timeout'))
                        elif session.is_channel_broken():
                            expired_sessions.append((session_id, 'server_restart'))

                for sid, reason in expired_sessions:
                    cls._force_close_session(sid, reason)
                    _logger.info(f"Cleaned up session: {sid} (reason: {reason})")

                # ── Also clean up stale SFTP sessions ──────────
                stale_sftp = []
                with cls._sftp_lock:
                    for sid, sd in list(cls.sftp_sessions.items()):
                        if current_time - sd['last_activity'] > 1800:
                            stale_sftp.append(sid)
                        elif current_time - sd['created_at'] > 7200:
                            stale_sftp.append(sid)
                        else:
                            try:
                                t = sd['ssh'].get_transport()
                                if t is None or not t.is_active():
                                    stale_sftp.append(sid)
                            except Exception:
                                stale_sftp.append(sid)

                for sid in stale_sftp:
                    sd = None
                    with cls._sftp_lock:
                        sd = cls.sftp_sessions.pop(sid, None)
                    if sd:
                        try:
                            sd['sftp'].close()
                        except Exception:
                            pass
                        try:
                            sd['ssh'].close()
                        except Exception:
                            pass
                    _logger.info(f"Cleaned up SFTP session: {sid}")

            except Exception as e:
                _logger.error(f"Cleanup thread error: {str(e)}")

    # ── Access validation ─────────────────────────────────────

    def _validate_access(self, access_request_id, access_token):
        try:
            access_request = request.env['ssh.access.request'].sudo().browse(access_request_id)

            if not access_request.exists():
                raise AccessError('Access request not found')

            if not self._constant_time_compare(access_request.access_token, access_token):
                _logger.warning(
                    f"Invalid token attempt for request {access_request_id} by user {request.env.user.id}")
                raise AccessError('Invalid access token')

            if access_request.user_id.id != request.env.user.id:
                raise AccessError('Access denied - wrong user')

            if access_request.state != 'approved':
                raise AccessError(f'Access request is {access_request.state}, not approved')

            now = fields.Datetime.now()
            if now < access_request.access_start:
                raise AccessError('Access period has not started yet')

            if now > access_request.access_end:
                access_request.sudo().write({'state': 'expired'})
                raise AccessError('Access period has expired')

            return access_request

        except AccessError:
            raise
        except Exception as e:
            _logger.error(f"Access validation error: {str(e)}")
            raise AccessError('Access validation failed')

    def _constant_time_compare(self, val1, val2):
        if val1 is None or val2 is None:
            return False
        if len(val1) != len(val2):
            return False
        result = 0
        for x, y in zip(val1, val2):
            result |= ord(x) ^ ord(y)
        return result == 0

    def _get_connection_data(self, access_request):
        try:
            server = access_request.server_id.sudo()
            credential = access_request.credential_id.sudo()
            encryption = request.env['ssh.encryption.key'].sudo()

            return {
                'ip': encryption.decrypt(server.ip_encrypted),
                'port': int(encryption.decrypt(server.port_encrypted) or 22),
                'username': encryption.decrypt(credential.username_encrypted),
                'password': encryption.decrypt(credential.password_encrypted),
            }
        except Exception as e:
            _logger.error(f"Failed to decrypt connection data: {str(e)}")
            raise AccessError('Failed to retrieve connection credentials')

    def _generate_session_id(self, access_request_id):
        data = f"{request.session.sid}_{access_request_id}_{time.time()}_{request.env.user.id}"
        return hashlib.sha256(data.encode()).hexdigest()

    def _create_log_entry(self, access_request, session_id):
        try:
            return request.env['ssh.connection.log'].sudo().create({
                'request_id': access_request.id,
                'user_id': request.env.user.id,
                'server_id': access_request.server_id.id,
                'session_id': session_id,
                'ip_address': request.httprequest.remote_addr,
                'user_agent': request.httprequest.user_agent.string[
                              :200] if request.httprequest.user_agent else 'Unknown',
                'connected_at': fields.Datetime.now(),
            })
        except Exception as e:
            _logger.error(f"Failed to create log entry: {str(e)}")
            return None

    # ── Routes ────────────────────────────────────────────────

    @http.route('/ssh_terminal/secure_connect', type='jsonrpc', auth='user')
    def secure_connect(self, access_request_id, access_token):
        try:
            self._start_cleanup_thread()
            access_request = self._validate_access(access_request_id, access_token)
            connection_data = self._get_connection_data(access_request)

            session_id = self._generate_session_id(access_request_id)
            session = SSHSession(connection_data, session_id)

            if session.connect():
                with self._lock:
                    self.sessions[session_id] = {
                        'session': session,
                        'access_request_id': access_request_id,
                        'user_id': request.env.user.id,
                        'dbname': request.env.cr.dbname,
                        'created_at': time.time(),
                    }

                log_entry = self._create_log_entry(access_request, session_id)
                if log_entry:
                    self.sessions[session_id]['log_id'] = log_entry.id

                initial_output = session.get_output(wait=1.0)

                _logger.info(f"User {request.env.user.id} connected to {access_request.server_id.name}")

                return {
                    'success': True,
                    'session_id': session_id,
                    'output': initial_output,
                    'server_name': access_request.server_id.name,
                }
            else:
                return {
                    'success': False,
                    'error': 'Failed to establish SSH connection. Please check server credentials.',
                }

        except AccessError as e:
            return {'success': False, 'error': str(e)}
        except Exception as e:
            _logger.error(f"Connection error: {str(e)}", exc_info=True)
            return {'success': False, 'error': 'Connection failed. Please contact administrator.'}

    def _validate_session(self, session_id):
        if session_id not in self.sessions:
            raise AccessError('Session not found or expired')

        session_data = self.sessions[session_id]

        if session_data['user_id'] != request.env.user.id:
            raise AccessError('Session access denied')

        # ── Detect broken channel (remote restart / network drop) ──
        if session_data['session'].is_channel_broken():
            self._force_close_session_with_env(session_id, 'server_restart')
            raise AccessError('Connection lost – the remote server may have restarted')

        # Re-validate access request
        try:
            access_request = request.env['ssh.access.request'].sudo().browse(
                session_data['access_request_id']
            )
            if not access_request.exists() or access_request.state != 'approved':
                self._force_close_session_with_env(session_id, 'revoked')
                raise AccessError('Access has been revoked')

            now = fields.Datetime.now()
            if now > access_request.access_end:
                access_request.sudo().write({'state': 'expired'})
                self._force_close_session_with_env(session_id, 'access_expired')
                raise AccessError('Access period has expired')

        except AccessError:
            raise
        except Exception as e:
            _logger.error(f"Session validation error: {str(e)}")
            raise AccessError('Session validation failed')

        return session_data

    def _force_close_session_with_env(self, session_id, disconnect_reason='error'):
        """Close session using the current request environment."""
        with self._lock:
            if session_id in self.sessions:
                try:
                    session_data = self.sessions[session_id]
                    session = session_data['session']
                    command_log = session.get_command_log()

                    if 'log_id' in session_data:
                        try:
                            log = request.env['ssh.connection.log'].sudo().browse(session_data['log_id'])
                            if log.exists():
                                log.write({
                                    'disconnected_at': fields.Datetime.now(),
                                    'disconnect_reason': disconnect_reason,
                                    'command_log': command_log,
                                })
                        except Exception as e:
                            _logger.error(f"Failed to update log: {str(e)}")

                    session.close()
                    del self.sessions[session_id]
                    _logger.info(f"Force closed session: {session_id} (reason: {disconnect_reason})")
                except Exception as e:
                    _logger.error(f"Error force closing session {session_id}: {str(e)}")

    @classmethod
    def _force_close_session(cls, session_id, disconnect_reason='error'):
        """Close session from background thread (no request env available)."""
        with cls._lock:
            if session_id in cls.sessions:
                try:
                    session_data = cls.sessions[session_id]
                    session = session_data['session']
                    command_log = session.get_command_log()

                    if 'log_id' in session_data:
                        try:
                            db_name = session_data.get('dbname')
                            if db_name:
                                with Registry(db_name).cursor() as cr:
                                    env = api.Environment(cr, SUPERUSER_ID, {})
                                    log = env['ssh.connection.log'].browse(session_data['log_id'])
                                    if log.exists():
                                        log.write({
                                            'disconnected_at': fields.Datetime.now(),
                                            'disconnect_reason': disconnect_reason,
                                            'command_log': command_log,
                                        })
                        except Exception as e:
                            _logger.error(f"Failed to update log for session {session_id}: {str(e)}")

                    session.close()
                    del cls.sessions[session_id]
                    _logger.info(f"Force closed session: {session_id} (reason: {disconnect_reason})")
                except Exception as e:
                    _logger.error(f"Error force closing session {session_id}: {str(e)}")

    @http.route('/ssh_terminal/secure_send', type='jsonrpc', auth='user')
    def secure_send(self, session_id, data, command=None, is_enter=False):
        """Send data to SSH session.

        Args:
            session_id: session identifier
            data: raw keystrokes to forward
            command: (optional) client-side reconstructed command - fallback
            is_enter: True when the client pressed Enter
        """
        try:
            session_data = self._validate_session(session_id)
            session = session_data['session']
            user_tz = request.env.context.get('tz') or request.env.user.tz or 'UTC'

            # When Enter is pressed, tell the session to capture the
            # current line from the server output (works for Up-arrow
            # recalled commands too).
            if is_enter:
                session.notify_enter(user_tz)
            elif command:
                session.capture_command(command, user_tz)

            if session.send_command(data):
                return {'success': True}
            else:
                return {'success': False, 'error': 'Failed to send command', 'disconnect': True}

        except AccessError as e:
            return {'success': False, 'error': str(e), 'disconnect': True}
        except Exception as e:
            _logger.error(f"Send error: {str(e)}")
            return {'success': False, 'error': 'Send failed', 'disconnect': True}

    @http.route('/ssh_terminal/secure_read', type='jsonrpc', auth='user')
    def secure_read(self, session_id, wait=0):
        try:
            session_data = self._validate_session(session_id)
            # short long-poll: answers at once when output arrives, otherwise after `wait`
            output = session_data['session'].get_output(wait=min(float(wait or 0), 1.0))
            return {'success': True, 'output': output}
        except AccessError as e:
            return {'success': False, 'error': str(e), 'disconnect': True}
        except Exception as e:
            _logger.error(f"Read error: {str(e)}")
            return {'success': False, 'error': 'Read failed'}

    @http.route('/ssh_terminal/secure_resize', type='jsonrpc', auth='user')
    def secure_resize(self, session_id, cols, rows):
        try:
            session_data = self._validate_session(session_id)
            session_data['session'].resize(cols, rows)
            return {'success': True}
        except AccessError as e:
            return {'success': False, 'error': str(e)}
        except Exception as e:
            _logger.error(f"Resize error: {str(e)}")
            return {'success': False, 'error': 'Resize failed'}

    @http.route('/ssh_terminal/secure_close', type='jsonrpc', auth='user')
    def secure_close(self, session_id, command_log=None):
        try:
            with self._lock:
                if session_id in self.sessions:
                    session_data = self.sessions[session_id]
                    session = session_data['session']

                    server_command_log = session.get_command_log()
                    final_command_log = server_command_log or command_log

                    if 'log_id' in session_data:
                        try:
                            log = request.env['ssh.connection.log'].sudo().browse(session_data['log_id'])
                            if log.exists():
                                log.write({
                                    'disconnected_at': fields.Datetime.now(),
                                    'disconnect_reason': 'user',
                                    'command_log': final_command_log,
                                })
                        except Exception as e:
                            _logger.error(f"Failed to update log: {str(e)}")

                    session.close()
                    del self.sessions[session_id]
                    _logger.info(f"User {request.env.user.id} closed session {session_id}")

            return {'success': True}
        except Exception as e:
            _logger.error(f"Close error: {str(e)}")
            return {'success': False, 'error': 'Close failed'}

    @http.route('/ssh_terminal/check_access', type='jsonrpc', auth='user')
    def check_access(self, access_request_id, access_token):
        try:
            self._validate_access(access_request_id, access_token)
            return {'valid': True}
        except AccessError as e:
            return {'valid': False, 'error': str(e)}
        except Exception as e:
            _logger.error(f"Access check error: {str(e)}")
            return {'valid': False, 'error': 'Validation failed'}

    # ═══════════════════════════════════════════════════════════
    #  SFTP FILE MANAGER
    # ═══════════════════════════════════════════════════════════

    sftp_sessions = {}
    _sftp_lock = threading.Lock()

    # ── SFTP connect / close ──────────────────────────────────

    @http.route('/ssh_terminal/sftp_connect', type='jsonrpc', auth='user')
    def sftp_connect(self, access_request_id, access_token):
        """Open SFTP connection for file management."""
        try:
            self._start_cleanup_thread()
            access_request = self._validate_access(access_request_id, access_token)
            connection_data = self._get_connection_data(access_request)

            session_id = 'sftp_' + self._generate_session_id(access_request_id)

            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=connection_data['ip'],
                port=connection_data['port'],
                username=connection_data['username'],
                password=connection_data['password'],
                timeout=10,
                look_for_keys=False,
                allow_agent=False,
                banner_timeout=30,
            )
            connection_data.clear()

            sftp = ssh.open_sftp()
            home_dir = sftp.normalize('.')

            with self._sftp_lock:
                self.sftp_sessions[session_id] = {
                    'ssh': ssh,
                    'sftp': sftp,
                    'user_id': request.env.user.id,
                    'access_request_id': access_request_id,
                    'created_at': time.time(),
                    'last_activity': time.time(),
                    'home_dir': home_dir,
                    'transfer_log': [],
                }

            # Create audit log
            self._create_log_entry(access_request, session_id)

            _logger.info(f"SFTP session opened for user {request.env.user.id}")

            return {
                'success': True,
                'session_id': session_id,
                'home_dir': home_dir,
                'server_name': access_request.server_id.name,
            }

        except AccessError as e:
            return {'success': False, 'error': str(e)}
        except paramiko.AuthenticationException:
            return {'success': False, 'error': 'Authentication failed.'}
        except Exception as e:
            _logger.error(f"SFTP connect error: {e}", exc_info=True)
            return {'success': False, 'error': 'Failed to connect. Contact administrator.'}

    def _validate_sftp_session(self, session_id):
        """Validate SFTP session ownership."""
        if session_id not in self.sftp_sessions:
            raise AccessError('SFTP session not found or expired')
        sd = self.sftp_sessions[session_id]
        if sd['user_id'] != request.env.user.id:
            raise AccessError('Session access denied')
        sd['last_activity'] = time.time()

        # Check transport alive
        transport = sd['ssh'].get_transport()
        if transport is None or not transport.is_active():
            self._close_sftp_session(session_id)
            raise AccessError('Connection lost – remote server may have restarted')

        return sd

    def _close_sftp_session(self, session_id):
        with self._sftp_lock:
            sd = self.sftp_sessions.pop(session_id, None)
        if sd:
            try:
                sd['sftp'].close()
            except Exception:
                pass
            try:
                sd['ssh'].close()
            except Exception:
                pass

    @http.route('/ssh_terminal/sftp_close', type='jsonrpc', auth='user')
    def sftp_close(self, session_id):
        try:
            self._validate_sftp_session(session_id)
            self._close_sftp_session(session_id)
            return {'success': True}
        except Exception as e:
            self._close_sftp_session(session_id)
            return {'success': True}

    # ── Directory listing ─────────────────────────────────────

    @http.route('/ssh_terminal/sftp_list', type='jsonrpc', auth='user')
    def sftp_list(self, session_id, path='/'):
        try:
            sd = self._validate_sftp_session(session_id)
            sftp = sd['sftp']

            path = sftp.normalize(path)
            entries = []

            for attr in sftp.listdir_attr(path):
                is_dir = False
                is_link = False
                mode = attr.st_mode or 0

                if stat.S_ISDIR(mode):
                    is_dir = True
                if stat.S_ISLNK(mode):
                    is_link = True

                perm_str = ''
                if mode:
                    perm_str = self._mode_to_str(mode)

                entries.append({
                    'name': attr.filename,
                    'size': attr.st_size or 0,
                    'mtime': attr.st_mtime or 0,
                    'is_dir': is_dir,
                    'is_link': is_link,
                    'permissions': perm_str,
                    'uid': attr.st_uid or 0,
                    'gid': attr.st_gid or 0,
                })

            # Sort: directories first, then alphabetical
            entries.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))

            return {
                'success': True,
                'path': path,
                'files': entries,
            }

        except AccessError as e:
            return {'success': False, 'error': str(e), 'disconnect': True}
        except PermissionError:
            return {'success': False, 'error': 'Permission denied'}
        except FileNotFoundError:
            return {'success': False, 'error': 'Directory not found'}
        except Exception as e:
            _logger.error(f"SFTP list error: {e}")
            return {'success': False, 'error': f'Failed to list directory: {str(e)}'}

    def _mode_to_str(self, mode):
        """Convert numeric mode to rwx string."""
        parts = []
        if stat.S_ISDIR(mode):
            parts.append('d')
        elif stat.S_ISLNK(mode):
            parts.append('l')
        else:
            parts.append('-')
        for who in ('USR', 'GRP', 'OTH'):
            for perm, letter in (('R', 'r'), ('W', 'w'), ('X', 'x')):
                if mode & getattr(stat, f'S_I{perm}{who}'):
                    parts.append(letter)
                else:
                    parts.append('-')
        return ''.join(parts)

    # ── File operations ───────────────────────────────────────

    @http.route('/ssh_terminal/sftp_mkdir', type='jsonrpc', auth='user')
    def sftp_mkdir(self, session_id, path):
        try:
            sd = self._validate_sftp_session(session_id)
            sd['sftp'].mkdir(path)
            return {'success': True}
        except AccessError as e:
            return {'success': False, 'error': str(e), 'disconnect': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    @http.route('/ssh_terminal/sftp_delete', type='jsonrpc', auth='user')
    def sftp_delete(self, session_id, path, is_dir=False):
        try:
            sd = self._validate_sftp_session(session_id)
            if is_dir:
                self._sftp_rmdir_recursive(sd['sftp'], path)
            else:
                sd['sftp'].remove(path)
            return {'success': True}
        except AccessError as e:
            return {'success': False, 'error': str(e), 'disconnect': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _sftp_rmdir_recursive(self, sftp, path):
        """Recursively remove directory."""
        for attr in sftp.listdir_attr(path):
            child = path.rstrip('/') + '/' + attr.filename
            if stat.S_ISDIR(attr.st_mode or 0):
                self._sftp_rmdir_recursive(sftp, child)
            else:
                sftp.remove(child)
        sftp.rmdir(path)

    @http.route('/ssh_terminal/sftp_rename', type='jsonrpc', auth='user')
    def sftp_rename(self, session_id, old_path, new_path):
        try:
            sd = self._validate_sftp_session(session_id)
            sd['sftp'].rename(old_path, new_path)
            return {'success': True}
        except AccessError as e:
            return {'success': False, 'error': str(e), 'disconnect': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    @http.route('/ssh_terminal/sftp_upload', type='jsonrpc', auth='user')
    def sftp_upload(self, session_id, path, data_b64, filename):
        """Upload file. Data is base64 encoded."""
        try:
            sd = self._validate_sftp_session(session_id)
            file_data = base64.b64decode(data_b64)

            MAX_SIZE = 100 * 1024 * 1024  # 100 MB
            if len(file_data) > MAX_SIZE:
                return {'success': False, 'error': 'File exceeds 100 MB limit'}

            full_path = path.rstrip('/') + '/' + filename
            with sd['sftp'].file(full_path, 'wb') as f:
                f.write(file_data)

            return {'success': True, 'size': len(file_data)}

        except AccessError as e:
            return {'success': False, 'error': str(e), 'disconnect': True}
        except Exception as e:
            _logger.error(f"SFTP upload error: {e}")
            return {'success': False, 'error': str(e)}

    @http.route('/ssh_terminal/sftp_download', type='http', auth='user')
    def sftp_download(self, session_id, path, **kwargs):
        """Download file as HTTP binary response."""
        try:
            sd = self._validate_sftp_session(session_id)
            filename = os.path.basename(path)

            with sd['sftp'].file(path, 'rb') as f:
                file_data = f.read()

            return request.make_response(
                file_data,
                headers=[
                    ('Content-Type', 'application/octet-stream'),
                    ('Content-Disposition', f'attachment; filename="{filename}"'),
                    ('Content-Length', str(len(file_data))),
                ],
            )

        except AccessError as e:
            return request.make_response(str(e), status=403)
        except Exception as e:
            _logger.error(f"SFTP download error: {e}")
            return request.make_response('Download failed', status=500)

    # ── File reading / editing ────────────────────────────────

    @http.route('/ssh_terminal/sftp_read_file', type='jsonrpc', auth='user')
    def sftp_read_file(self, session_id, path):
        """Read text file content for inline editing."""
        try:
            sd = self._validate_sftp_session(session_id)

            fstat = sd['sftp'].stat(path)
            MAX_EDIT_SIZE = 5 * 1024 * 1024  # 5 MB
            if fstat.st_size and fstat.st_size > MAX_EDIT_SIZE:
                return {'success': False, 'error': 'File too large to edit (>5 MB). Use download instead.'}

            with sd['sftp'].file(path, 'r') as f:
                content = f.read().decode('utf-8', errors='replace')

            return {'success': True, 'content': content, 'size': fstat.st_size}

        except AccessError as e:
            return {'success': False, 'error': str(e), 'disconnect': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    @http.route('/ssh_terminal/sftp_write_file', type='jsonrpc', auth='user')
    def sftp_write_file(self, session_id, path, content):
        """Write text file content (save from editor)."""
        try:
            sd = self._validate_sftp_session(session_id)
            with sd['sftp'].file(path, 'w') as f:
                f.write(content)
            return {'success': True}
        except AccessError as e:
            return {'success': False, 'error': str(e), 'disconnect': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}
