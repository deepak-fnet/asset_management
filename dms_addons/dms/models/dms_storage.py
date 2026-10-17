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

    # ------------------------------------------------------------------
    # Files at rest: an AES-256 ZIP next to the logical path ("<path>.zip") when the
    # server password is on. Callers always use the logical path; this layer adds/removes
    # the protection, so viewer, AI and downloads keep working unchanged.
    # ------------------------------------------------------------------
    @api.model
    def _server_crypto(self):
        ICP = self.env['ir.config_parameter'].sudo()
        on = ICP.get_param('dms.server_encrypt') in ('True', '1')
        pw = ICP.get_param('dms.file_password') or ''
        prev = ICP.get_param('dms.file_password_prev') or ''
        return on and bool(pw), pw, prev

    @staticmethod
    def _exists(sftp, path):
        try:
            sftp.stat(path)
            return True
        except FileNotFoundError:
            return False

    @api.model
    def write_file(self, rel, data):
        from .dms_crypto import ZIP_SUFFIX, zip_encrypt
        on, pw, _prev = self._server_crypto()
        with self._sftp() as (sftp, root):
            path = self._abs(root, rel)
            self._mkdirs(sftp, posixpath.dirname(path))
            target, stale = (path + ZIP_SUFFIX, path) if on else (path, path + ZIP_SUFFIX)
            blob = zip_encrypt(data, posixpath.basename(rel), pw) if on else data
            with sftp.open(target, 'wb') as f:
                f.set_pipelined(True)
                f.write(blob)
            if self._exists(sftp, stale):
                sftp.remove(stale)
        return rel

    @api.model
    def read_file(self, rel):
        from .dms_crypto import ZIP_SUFFIX, zip_decrypt
        _on, pw, prev = self._server_crypto()
        with self._sftp() as (sftp, root):
            path = self._abs(root, rel)
            for candidate, wrapped in ((path + ZIP_SUFFIX, True), (path, False)):
                try:
                    with sftp.open(candidate, 'rb') as f:
                        f.prefetch()
                        blob = f.read()
                except FileNotFoundError:
                    continue
                if not wrapped:
                    return blob
                for key in filter(None, (pw, prev)):
                    try:
                        return zip_decrypt(blob, key)
                    except RuntimeError:
                        continue
                raise UserError(_("The stored copy of %s cannot be opened with the server password in DMS Settings.", rel))
            raise UserError(_("File not found on DMS server: %s", rel))

    @api.model
    def move_file(self, rel_from, rel_to):
        from .dms_crypto import ZIP_SUFFIX
        with self._sftp() as (sftp, root):
            src, dst = self._abs(root, rel_from), self._abs(root, rel_to)
            self._mkdirs(sftp, posixpath.dirname(dst))
            for suffix in (ZIP_SUFFIX, ''):
                if self._exists(sftp, src + suffix):
                    sftp.posix_rename(src + suffix, dst + suffix)
                    return rel_to
            _logger.warning("DMS move: %s not found", src)
            return rel_from

    @api.model
    def delete_file(self, rel):
        from .dms_crypto import ZIP_SUFFIX
        with self._sftp() as (sftp, root):
            gone = True
            for suffix in (ZIP_SUFFIX, ''):
                try:
                    sftp.remove(self._abs(root, rel) + suffix)
                    gone = False
                except FileNotFoundError:
                    pass
            if gone:
                _logger.warning("DMS delete: %s already gone", rel)

    @api.model
    def reprotect_all(self):
        """Rewrite every stored file with the current server-password setting
        (encrypts, decrypts or re-keys). Returns (done, failed)."""
        done, failed = 0, []
        revs = self.env['dms.revision'].sudo().with_context(active_test=False).search([('storage_path', '!=', False)])
        for rev in revs:
            try:
                self.write_file(rev.storage_path, self.read_file(rev.storage_path))
                done += 1
            except UserError as e:
                failed.append('%s: %s' % (rev.display_name, e.args[0] if e.args else e))
        return done, failed

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

    @api.model
    def purge_document_dirs(self, code, keep=()):
        """Remove every folder named exactly after a document number (files included), then the
        parent folders that became empty. Top-level folders and folders in `keep` stay."""
        if not code or '/' in code or code in ('.', '..'):
            return 0
        if self.env['dms.document'].sudo().with_context(active_test=False).search_count([('code', '=', code)]):
            return 0  # a live document carries this number: its folder stays
        import stat
        removed = 0
        with self._sftp() as (sftp, root):
            keep_abs = {self._abs(root, k) for k in keep if k}
            found = []

            def walk(path, depth):
                try:
                    entries = sftp.listdir_attr(path)
                except FileNotFoundError:
                    return
                for e in entries:
                    if stat.S_ISDIR(e.st_mode):
                        sub = posixpath.join(path, e.filename)
                        if e.filename == code:
                            found.append(sub)
                        elif depth < 8:
                            walk(sub, depth + 1)
            walk(root, 0)

            def rm_tree(path):
                for e in sftp.listdir_attr(path):
                    p = posixpath.join(path, e.filename)
                    if stat.S_ISDIR(e.st_mode):
                        rm_tree(p)
                    else:
                        sftp.remove(p)
                sftp.rmdir(path)

            for path in found:
                rm_tree(path)
                removed += 1
                parent = posixpath.dirname(path)
                # walk up while the parent is empty, never touching the root's direct children
                while posixpath.dirname(parent) != root and parent not in keep_abs and parent.startswith(root + '/'):
                    try:
                        if sftp.listdir(parent):
                            break
                        sftp.rmdir(parent)
                    except OSError:
                        break
                    parent = posixpath.dirname(parent)
        return removed
