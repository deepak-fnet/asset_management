"""Previews for every file type, prepared on the server.

* Office documents and slides: LibreOffice renders them to PDF exactly as it would print.
* Spreadsheets: LibreOffice renders them to HTML, so they look like the Calc grid
  (column widths, colours, borders) instead of being squeezed onto print pages.
* PDF / office for people with View only: the browser never receives the file. Each page
  is rendered to an image on the server, one page at a time.

A stored revision never changes, so every conversion happens once and is cached on disk.
It starts in the background right after upload / check-in, so it is usually ready before
anyone opens the file.
"""
import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile

from odoo import SUPERUSER_ID, api, models, _
from odoo.exceptions import UserError
from odoo.modules.registry import Registry
from odoo.tools import config

_logger = logging.getLogger(__name__)

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

DOC_EXT = {'doc', 'docx', 'odt', 'rtf', 'ppt', 'pptx', 'odp', 'pps', 'ppsx', 'wpd', 'pages', 'dot', 'dotx', 'odg'}
SHEET_EXT = {'xls', 'xlsx', 'xlsm', 'ods', 'csv', 'tsv'}
OFFICE_EXT = DOC_EXT | SHEET_EXT
IMAGE_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
IMAGE_CONVERT_EXT = {'tif', 'tiff', 'bmp', 'ico', 'jfif'}
VIDEO_EXT = {'mp4', 'webm', 'ogv', 'mov', 'm4v'}
AUDIO_EXT = {'mp3', 'wav', 'ogg', 'oga', 'm4a', 'aac', 'flac'}
TEXT_EXT = {'txt', 'log', 'md', 'json', 'xml', 'yml', 'yaml', 'ini', 'conf', 'cfg', 'py', 'js', 'ts', 'css', 'scss',
            'html', 'htm', 'sql', 'sh', 'bat', 'java', 'c', 'cpp', 'h', 'cs', 'php', 'rb', 'go', 'eml'}
ARCHIVE_EXT = {'zip'}

CONVERT_TIMEOUT = 180
PAGE_DPI = 150          # stays sharp when zoomed in
MAX_TEXT = 2 * 1024 * 1024

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(key):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def _soffice():
    return shutil.which('soffice') or shutil.which('libreoffice')


def _run_soffice(src, target, outdir):
    soffice = _soffice()
    if not soffice:
        raise UserError(_("LibreOffice is not installed on the Odoo server, so this file cannot be shown."))
    profile = 'file://' + os.path.join(outdir, 'profile')  # private profile: conversions can run in parallel
    cmd = [soffice, '-env:UserInstallation=%s' % profile, '--headless', '--invisible', '--norestore',
           '--nologo', '--convert-to', target, '--outdir', outdir, src]
    try:
        subprocess.run(cmd, check=True, timeout=CONVERT_TIMEOUT, capture_output=True,
                       env={**os.environ, 'SAL_USE_VCLPLUGIN': 'svp', 'HOME': outdir})
    except subprocess.TimeoutExpired:
        raise UserError(_("The file took too long to prepare for viewing."))
    except subprocess.CalledProcessError as e:
        _logger.warning("DMS preview: LibreOffice failed: %s", e.stderr)
        raise UserError(_("LibreOffice could not open this file."))


def _convert(data, ext, target, out_ext):
    tmp = tempfile.mkdtemp(prefix='dms_preview_')
    try:
        src = os.path.join(tmp, 'document.%s' % ext)
        with open(src, 'wb') as f:
            f.write(data)
        _run_soffice(src, target, tmp)
        out = os.path.join(tmp, 'document.%s' % out_ext)
        if not os.path.exists(out):
            raise UserError(_("LibreOffice could not open this file."))
        with open(out, 'rb') as f:
            return f.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_SHEET_CLEAN = [
    (re.compile(r'<script\b.*?</script>', re.S | re.I), ''),
    (re.compile(r'\son\w+\s*=\s*"[^"]*"', re.I), ''),
    (re.compile(r'<img\b[^>]*>', re.I), ''),           # images live in separate files we do not serve
    (re.compile(r'<a\b[^>]*>', re.I), '<span>'),       # links become plain text
    (re.compile(r'</a>', re.I), '</span>'),
]

SHEET_STYLE = """<style>
html,body{margin:0;background:#fff;font-family:Calibri,Carlito,'Liberation Sans',Arial,sans-serif;font-size:11pt;color:#000}
body{padding:0 0 40px}
h1{font:600 13px 'Public Sans',Arial,sans-serif;color:#1c2b4a;background:#e9edf3;margin:0;padding:8px 12px;
   position:sticky;top:0;z-index:2;border-bottom:1px solid #c9d0db}
h1 a,h1 em{font-style:normal;color:inherit}
table{border-collapse:collapse;margin:0 0 24px 0}
td{border:1px solid #d4d4d4;padding:1px 4px;white-space:nowrap;vertical-align:bottom;height:20px}
hr{display:none}
p{margin:0}
::selection{background:transparent}
</style>"""


def _clean_sheet_html(html):
    for rx, rep in _SHEET_CLEAN:
        html = rx.sub(rep, html)
    # drop LibreOffice's own sheet index at the top; we style the sheet titles ourselves
    html = re.sub(r'<center>.*?</center>', '', html, flags=re.S | re.I)
    html = html.replace('<head>', '<head>' + SHEET_STYLE, 1) if '<head>' in html else SHEET_STYLE + html
    return html


class DmsRevisionPreview(models.Model):
    _inherit = 'dms.revision'

    # ------------------------------------------------------------------ kinds
    def _ext(self):
        return os.path.splitext(self.file_name or '')[1].lstrip('.').lower()

    def _preview_kind(self):
        self.ensure_one()
        ext, mime = self._ext(), self.mimetype or ''
        if ext == 'pdf' or mime == 'application/pdf':
            return 'pdf'
        if ext in SHEET_EXT:
            return 'sheet'
        if ext in DOC_EXT:
            return 'doc'
        if ext in IMAGE_EXT:
            return 'image'
        if ext in IMAGE_CONVERT_EXT:
            return 'image_convert'
        if ext in VIDEO_EXT:
            return 'video'
        if ext in AUDIO_EXT:
            return 'audio'
        if ext in ARCHIVE_EXT:
            return 'zip'
        if ext in TEXT_EXT or mime.startswith('text/'):
            return 'text'
        return 'none'

    def _is_office(self):
        self.ensure_one()
        return self._ext() in OFFICE_EXT

    # ------------------------------------------------------------------ cache
    def _cache_base(self):
        self.ensure_one()
        folder = os.path.join(config['data_dir'], 'dms_preview', self.env.cr.dbname)
        os.makedirs(folder, exist_ok=True)
        # the stored path is part of the key: a moved or re-uploaded file gets a fresh preview
        digest = hashlib.sha1(('%s|%s' % (self.storage_path, self.file_size)).encode()).hexdigest()[:12]
        return os.path.join(folder, '%s_%s' % (self.id, digest))

    def _cached(self, suffix, build):
        """Return bytes of a cached artefact, building it once if missing."""
        path = self._cache_base() + suffix
        with _lock_for(path):
            if not os.path.exists(path):
                data = build()
                tmp = path + '.part'
                with open(tmp, 'wb') as f:
                    f.write(data)
                os.replace(tmp, path)
        with open(path, 'rb') as f:
            return f.read()

    # kept for the /pdf route and older callers
    def _preview_cache_path(self):
        return self._cache_base() + '.pdf'

    # ------------------------------------------------------------------ artefacts
    def _pdf_path(self):
        """Local path of the PDF for a pdf or document revision."""
        kind = self._preview_kind()
        if kind == 'pdf':
            self._cached('.pdf', self._read_data)
        elif kind == 'doc':
            self._cached('.pdf', lambda: _convert(self._read_data(), self._ext(), 'pdf', 'pdf'))
        else:
            raise UserError(_("This file has no page view."))
        return self._cache_base() + '.pdf'

    def _preview_pdf(self):
        self._pdf_path()
        with open(self._cache_base() + '.pdf', 'rb') as f:
            return f.read()

    def _page_count(self):
        def build():
            out = subprocess.run(['pdfinfo', self._pdf_path()], capture_output=True, text=True, timeout=30)
            m = re.search(r'^Pages:\s+(\d+)', out.stdout, re.M)
            return json.dumps({'pages': int(m.group(1)) if m else 0}).encode()
        return json.loads(self._cached('.pages.json', build))['pages']

    def _page_png(self, n):
        n = int(n)
        if n < 1 or n > self._page_count():
            raise UserError(_("No such page."))

        def build():
            tmp = tempfile.mkdtemp(prefix='dms_page_')
            try:
                out = os.path.join(tmp, 'p')
                subprocess.run(['pdftoppm', '-png', '-r', str(PAGE_DPI), '-f', str(n), '-l', str(n),
                                '-singlefile', self._pdf_path(), out], check=True, timeout=60, capture_output=True)
                with open(out + '.png', 'rb') as f:
                    return f.read()
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        return self._cached('.p%d.%d.png' % (n, PAGE_DPI), build)

    def _sheet_html(self):
        def build():
            ext = self._ext()
            data = self._read_data()
            if ext == 'tsv':
                ext = 'csv'
            html = _convert(data, ext, 'html', 'html').decode('utf-8', 'replace')
            return _clean_sheet_html(html).encode()
        return self._cached('.sheet.html', build)

    def _image_png(self):
        def build():
            if not Image:
                raise UserError(_("Image conversion is not available on the server."))
            img = Image.open(io.BytesIO(self._read_data()))
            img.seek(0)
            if img.mode not in ('RGB', 'RGBA'):
                img = img.convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            return buf.getvalue()
        return self._cached('.img.png', build)

    def _text(self):
        data = self._read_data()[:MAX_TEXT]
        for enc in ('utf-8', 'utf-16', 'latin-1'):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode('utf-8', 'replace')

    def _zip_listing(self):
        try:
            zf = zipfile.ZipFile(io.BytesIO(self._read_data()))
        except zipfile.BadZipFile:
            raise UserError(_("This archive is damaged."))
        return [{'name': i.filename, 'size': i.file_size, 'dir': i.is_dir()} for i in zf.infolist()[:2000]]

    def _prepare_preview(self):
        """Do the slow part now (conversion), so viewing is instant later."""
        from ..controllers.protected_viewer import LO_EXT, build_office_html
        if self._ext() in LO_EXT:
            # the protected viewer's page images (the slow part of opening an office file)
            self._cached('.award.html', lambda: build_office_html(self._read_data(), self._ext()).encode())
        kind = self._preview_kind()
        if kind in ('pdf', 'doc'):
            if self._page_count():
                self._page_png(1)
        elif kind == 'sheet':
            self._sheet_html()
        elif kind == 'image_convert':
            self._image_png()

    # ------------------------------------------------------------------ background warm-up
    def _schedule_preview(self):
        todo = self.filtered(lambda r: r.storage_path and r._preview_kind() in ('pdf', 'doc', 'sheet', 'image_convert'))
        if not todo:
            return
        dbname, ids = self.env.cr.dbname, todo.ids

        def run():
            try:
                with Registry(dbname).cursor() as cr:
                    env = api.Environment(cr, SUPERUSER_ID, {})
                    for rev in env['dms.revision'].browse(ids).exists():
                        try:
                            rev._prepare_preview()
                        except Exception as e:  # noqa: BLE001
                            _logger.info("DMS preview for revision %s not prepared: %s", rev.id, e)
            except Exception:  # noqa: BLE001
                _logger.exception("DMS preview thread failed")

        self.env.cr.postcommit.add(lambda: threading.Thread(target=run, name='dms-preview', daemon=True).start())

    def write(self, vals):
        res = super().write(vals)
        if vals.get('storage_path'):
            self._schedule_preview()
        return res
