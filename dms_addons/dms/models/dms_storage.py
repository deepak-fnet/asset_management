import contextlib
import logging
import posixpath
import re
import socket
import threading

from odoo import api, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    import paramiko
except ImportError:  # pragma: no cover
    paramiko = None


_POOL_LOCK = threading.RLock()
_POOL = {'key': None, 'transport': None, 'sftp': None}
TIMEOUT = 15


def _pool_drop():
    t = _POOL.get('transport')
    _POOL.update(key=None, transport=None, sftp=None)
    if t:
        try:
            t.close()
        except Exception:  # noqa: BLE001
            pass


def _pool_get(key):
    """Return a live SFTP client for these credentials, reconnecting when needed."""
    t = _POOL['transport']
    if _POOL['key'] == key and t and t.is_active():
        try:
            _POOL['sftp'].stat('.')  # cheap liveness check
            return _POOL['sftp']
        except Exception:  # noqa: BLE001
            _logger.info("DMS: SFTP connection went stale, reconnecting")
    _pool_drop()
    host, port, user, password = key
    sock = socket.create_connection((host, port), timeout=TIMEOUT)
    t = paramiko.Transport(sock)
    t.banner_timeout = TIMEOUT
    t.auth_timeout = TIMEOUT
    t.set_keepalive(30)
    try:
        t.connect(username=user, password=password)
        sftp = paramiko.SFTPClient.from_transport(t)
        sftp.get_channel().settimeout(60)
    except Exception:
        t.close()
        raise
    _POOL.update(key=key, transport=t, sftp=sftp)
    return sftp


def safe_name(name):
    """Make a string safe to use as one path segment on the DMS server."""
    name = re.sub(r'[\\/\x00]+', '_', (name or '').strip())
    name = re.sub(r'\s+', ' ', name)
    return name.strip('. ') or 'unnamed'


class DmsStorage(models.AbstractModel):
    """Files live on the DMS server; Odoo only keeps metadata.

    Every read/write goes through SFTP using the credentials in DMS Settings.
    """
    _name = 'dms.storage'
    _description = 'DMS Server Storage (SFTP)'

    @api.model
    def _get_params(self):
        ICP = self.env['ir.config_parameter'].sudo()
        return {
            'host': ICP.get_param('dms.server_host') or '',
            'port': int(ICP.get_param('dms.server_port') or 22),
            'user': ICP.get_param('dms.server_user') or '',
            'password': ICP.get_param('dms.server_password') or '',
            'root': (ICP.get_param('dms.server_path') or '').rstrip('/') or '/',
        }

    @contextlib.contextmanager
    def _sftp(self):
        """Yield (sftp, root) over a pooled SSH connection.

        One connection per Odoo worker is reused across requests: opening a new SSH
        session for every file makes OpenSSH throttle this host (PerSourcePenalties).
        """
        if paramiko is None:
            raise UserError(_("Python library 'paramiko' is required for the DMS server connection."))
        p = self._get_params()
        if not p['host'] or not p['user']:
            raise UserError(_("DMS server is not configured. Go to DMS › Configuration › Settings."))
        key = (p['host'], p['port'], p['user'], p['password'])
        with _POOL_LOCK:
            try:
                sftp = _pool_get(key)
                yield sftp, p['root']
            except paramiko.AuthenticationException:
                _pool_drop()
                raise UserError(_("DMS server login failed for user %s. Check DMS Settings.", p['user']))
            except FileNotFoundError:
                raise
            except (paramiko.SSHException, EOFError, OSError, socket.timeout) as e:
                _pool_drop()
                raise UserError(_("Cannot reach DMS server %(host)s:%(port)s (%(err)s). Try again in a moment.",
                                  host=p['host'], port=p['port'], err=e or type(e).__name__))

    @api.model
    def _abs(self, root, rel):
        return posixpath.join(root, rel.lstrip('/'))

    @api.model
    def _mkdirs(self, sftp, path):
        parts, cur = path.strip('/').split('/'), ''
        for part in parts:
            cur += '/' + part
            try:
                sftp.stat(cur)
            except FileNotFoundError:
                sftp.mkdir(cur)

    # ---- public helpers (relative paths, under the DMS root) ----
    @api.model
    def test_connection(self):
        with self._sftp() as (sftp, root):
            self._mkdirs(sftp, root)
            return sftp.listdir(root)

    @api.model
    def make_dir(self, rel):
        with self._sftp() as (sftp, root):
            self._mkdirs(sftp, self._abs(root, rel))

    @api.model
    def write_file(self, rel, data):
        with self._sftp() as (sftp, root):
            path = self._abs(root, rel)
            self._mkdirs(sftp, posixpath.dirname(path))
            with sftp.open(path, 'wb') as f:
                f.set_pipelined(True)
                f.write(data)
        return rel

    @api.model
    def read_file(self, rel):
        with self._sftp() as (sftp, root):
            try:
                with sftp.open(self._abs(root, rel), 'rb') as f:
                    f.prefetch()
                    return f.read()
            except FileNotFoundError:
                raise UserError(_("File not found on DMS server: %s", rel))

    @api.model
    def move_file(self, rel_from, rel_to):
        with self._sftp() as (sftp, root):
            src, dst = self._abs(root, rel_from), self._abs(root, rel_to)
            self._mkdirs(sftp, posixpath.dirname(dst))
            try:
                sftp.posix_rename(src, dst)
            except FileNotFoundError:
                _logger.warning("DMS move: %s not found", src)
                return rel_from
        return rel_to

    @api.model
    def delete_file(self, rel):
        with self._sftp() as (sftp, root):
            try:
                sftp.remove(self._abs(root, rel))
            except FileNotFoundError:
                _logger.warning("DMS delete: %s already gone", rel)

    @api.model
    def remove_dir(self, rel):
        """Remove an empty folder tree on the server (sub-folders first). Files are never touched."""
        with self._sftp() as (sftp, root):
            def rm(path):
                try:
                    entries = sftp.listdir_attr(path)
                except FileNotFoundError:
                    return
                import stat
                for e in entries:
                    if stat.S_ISDIR(e.st_mode):
                        rm(posixpath.join(path, e.filename))
                try:
                    sftp.rmdir(path)
                except OSError as e:
                    _logger.warning("DMS: folder %s not removed (not empty?): %s", path, e)
            rm(self._abs(root, rel))
