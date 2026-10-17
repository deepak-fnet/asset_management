"""DMS revisions shown in the award_management protected viewer (award_viewer.py).

Flow: the DMS app asks /dms/protected/token for a session-bound token, then opens
/dms/protected/<rev_id>?token=... in a new tab. The page, its screenshot shield and key
blocking are the award controller's own. Office pages are cached per revision, so only
the first view of a revision waits for LibreOffice.
"""
import base64
import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile

from odoo import http, _
from odoo.exceptions import AccessError
from odoo.http import request

from .award_viewer import AwardStyleDocumentViewer
from .main import DmsController, _env, _unlocked

_logger = logging.getLogger(__name__)

MODEL = 'dms.revision'
LO_EXT = ('xlsx', 'xls', 'xlsm', 'xltx', 'ods', 'doc', 'docx', 'dot', 'dotx', 'odt', 'rtf', 'wpd',
          'ppt', 'pptx', 'pps', 'ppsx', 'odp', 'odg', 'vsd', 'vsdx', 'pub')
HTML_EXT = ('html', 'htm', 'xhtml', 'mhtml')
IMAGE_EXT = ('png', 'jpg', 'jpeg', 'gif', 'webp', 'svg')
IMAGE_CONVERT_EXT = ('tif', 'tiff', 'bmp', 'ico', 'jfif')
VIDEO_EXT = ('mp4', 'webm', 'ogv', 'mov', 'm4v')
AUDIO_EXT = ('mp3', 'wav', 'ogg', 'oga', 'm4a', 'aac', 'flac')
TEXT_EXT = ('txt', 'log', 'md', 'json', 'xml', 'yml', 'yaml', 'ini', 'conf', 'cfg', 'py', 'js', 'ts', 'css',
            'sql', 'sh', 'bat', 'java', 'c', 'cpp', 'h', 'cs', 'php', 'rb', 'go', 'eml')
PAGE_DPI = 200


def _lift_memory_cap():
    """Odoo caps its own virtual memory (limit_memory_hard); Chrome reserves far more address
    space than it uses and refuses to start under that cap. Lift it for the Chrome child only."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (resource.RLIM_INFINITY, resource.getrlimit(resource.RLIMIT_AS)[1]))
    except Exception:  # noqa: BLE001
        pass


def _chrome():
    for name in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser'):
        path = shutil.which(name)
        if path:
            return path
    return None


def pdf_pages_html(pdf_bytes, password=None):
    """Any PDF as the award viewer's page images."""
    tmp = tempfile.mkdtemp(prefix='dms_pages_')
    try:
        src = os.path.join(tmp, 'in.pdf')
        with open(src, 'wb') as f:
            f.write(pdf_bytes)
        pw = ['-upw', password] if password else []
        if password and subprocess.run(['pdfinfo'] + pw + [src], capture_output=True, timeout=30).returncode != 0:
            raise PermissionError('wrong password')
        subprocess.run(['pdftoppm', '-png', '-r', str(PAGE_DPI)] + pw + [src, os.path.join(tmp, 'page')],
                       check=True, timeout=300, capture_output=True)
        pngs = sorted([n for n in os.listdir(tmp) if n.endswith('.png')],
                      key=lambda x: int(''.join(filter(str.isdigit, x)) or '0'))
        if not pngs:
            raise ValueError('no pages')
        imgs = ''.join(
            '<div class="xl-page-wrap" data-page="%d"><img src="data:image/png;base64,%s" draggable="false" '
            'oncontextmenu="return false" style="display:block;width:100%%;pointer-events:none;"/></div>'
            % (i + 1, base64.b64encode(open(os.path.join(tmp, n), 'rb').read()).decode())
            for i, n in enumerate(pngs))
        return AwardStyleDocumentViewer()._doc_image_viewer_html(imgs, len(pngs))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def html_to_pdf(data):
    """Render an HTML file the way a browser shows it (headless Chrome, scripts and network off).
    Falls back to LibreOffice when Chrome is not installed."""
    tmp = tempfile.mkdtemp(prefix='dms_html_')
    try:
        src = os.path.join(tmp, 'page.html')
        # no scripts and no network for uploaded pages: a CSP tag right after the doctype
        csp = (b'<meta http-equiv="Content-Security-Policy" content="script-src \'none\'; connect-src \'none\'; '
               b'frame-src \'none\'; object-src \'none\'">')
        m = re.match(rb'\s*(<!doctype[^>]*>)', data, re.I)
        data = (data[:m.end()] + csp + data[m.end():]) if m else csp + data
        with open(src, 'wb') as f:
            f.write(data)
        out = os.path.join(tmp, 'page.pdf')
        chrome = _chrome()
        if chrome:
            subprocess.run([
                chrome, '--headless=new', '--no-sandbox', '--disable-gpu', '--hide-scrollbars',
                '--user-data-dir=' + os.path.join(tmp, 'profile'),
                '--proxy-server=127.0.0.1:9', '--proxy-bypass-list=<-loopback>',  # and never reach the network
                '--no-pdf-header-footer', '--print-to-pdf=' + out, 'file://' + src,
            ], capture_output=True, timeout=120, preexec_fn=_lift_memory_cap)
        if not os.path.exists(out):
            from ..models.dms_preview import _convert
            return _convert(data, 'html', 'pdf:writer_web_pdf_Export', 'pdf')
        with open(out, 'rb') as f:
            return f.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build_office_html(data, ext):
    """The award viewer's LibreOffice page images for a file (no request needed)."""
    html = AwardStyleDocumentViewer()._preview_libreoffice(data, ext)
    if 'xl-page-wrap' not in html:  # an error page: do not cache it
        raise ValueError('LibreOffice preview failed')
    return html


class DmsProtectedViewer(AwardStyleDocumentViewer):

    def _rev_for(self, rev_id, token):
        if not _unlocked():
            return None
        if not self._check_view_token(MODEL, rev_id, token):
            return None
        rev = request.env[MODEL].sudo().browse(int(rev_id)).exists()
        if not rev or not DmsController()._can_open(rev, request.env.user, False):
            return None
        return rev

    @http.route('/dms/protected/token', type='jsonrpc', auth='user')
    def protected_token(self, rev_id):
        env = _env()
        rev = env[MODEL].sudo().browse(int(rev_id)).exists()
        if not rev or not DmsController()._can_open(rev, env.user, False):
            raise AccessError(_("You have no view access to this file."))
        return {'url': '/dms/protected/%s?token=%s' % (rev.id, self._mint_view_token(MODEL, rev.id))}

    # ------------------------------------------------------------------
    # Password-protected files: ask for the password, unlock in memory, never store it
    # ------------------------------------------------------------------
    def _password_page(self, rev, token, error=None):
        doc = rev.document_id
        esc = lambda v: (v or '').replace('&', '&amp;').replace('<', '&lt;').replace('"', '&quot;')
        html = (
            '<!DOCTYPE html><html><head><meta charset="UTF-8"/><title>Password required</title>'
            '<meta name="viewport" content="width=device-width,initial-scale=1"/>'
            '<style>body{margin:0;font-family:Segoe UI,Arial,sans-serif;background:#f5f5f5;display:flex;'
            'align-items:center;justify-content:center;min-height:100vh}'
            '.card{background:#fff;padding:32px 36px;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.12);width:380px;max-width:calc(100%% - 32px)}'
            'h2{margin:8px 0 6px;font-size:20px;color:#333}p{color:#666;font-size:13px;line-height:1.5;margin:0 0 16px}'
            'label{font-size:13px;font-weight:600;color:#444}input[type=password]{width:100%%;box-sizing:border-box;'
            'padding:10px 12px;margin:6px 0 14px;border:1px solid #ccc;border-radius:6px;font-size:15px}'
            'button{width:100%%;padding:11px;border:0;border-radius:6px;background:#875A7B;color:#fff;font-size:15px;'
            'font-weight:600;cursor:pointer}.err{color:#d9534f;font-size:13px;margin:-6px 0 12px}'
            '.lock{font-size:40px}</style></head><body oncontextmenu="return false;">'
            '<form class="card" method="post" autocomplete="off">'
            '<div class="lock">&#128274;</div><h2>Password required</h2>'
            '<p><b>%s %s</b> is locked with a password. Enter it to view the document. '
            'The password is used only to open the file and is not saved.</p>'
            '<input type="hidden" name="csrf_token" value="%s"/>'
            '<label for="pw">Document password</label>'
            '<input id="pw" type="password" name="doc_password" autofocus="autofocus" required="required"/>'
            '%s<button type="submit">Open document</button></form></body></html>'
        ) % (esc(doc.code), esc(doc.name), request.csrf_token(),
             '<div class="err">%s</div>' % esc(error) if error else '')
        return request.make_response(html, headers=[
            ('Content-Type', 'text/html; charset=utf-8'),
            ('Cache-Control', 'no-store, no-cache, must-revalidate'),
        ])

    def _unlock(self, data, ext, password):
        """Decrypted bytes, or None when the password is wrong."""
        if ext == 'pdf':
            return data  # pdftoppm opens it with the password itself
        import msoffcrypto
        from msoffcrypto.exceptions import InvalidKeyError
        try:
            f = msoffcrypto.OfficeFile(io.BytesIO(data))
            f.load_key(password=password)
            out = io.BytesIO()
            f.decrypt(out)
            return out.getvalue()
        except InvalidKeyError:
            return None
        except Exception as e:  # noqa: BLE001 - e.g. the password is right but decryption failed
            _logger.warning("DMS: could not unlock %s file: %s", ext, e)
            return None

    def _protected_preview(self, rev, ext, data, password):
        """Award-style page images for a protected file opened with its password (or None if wrong)."""
        # the rendered pages are cached under a hash of the password, so nobody gets them without typing it
        key = hashlib.sha256(('%s|%s' % (rev.id, password)).encode()).hexdigest()[:16]
        if ext == 'pdf':
            def build():
                return pdf_pages_html(data, password).encode()
        else:
            def build():
                plain = self._unlock(data, ext, password)
                if plain is None:
                    raise PermissionError('wrong password')
                return build_office_html(plain, ext).encode()
        try:
            return rev._cached('.award.pw%s.html' % key, build).decode()
        except PermissionError:
            return None

    @http.route('/dms/protected/<int:rev_id>', type='http', auth='user', website=False, methods=['GET', 'POST'])
    def protected_view(self, rev_id, token=None, doc_password=None, **kw):
        from ..models.dms_preview import is_password_protected
        rev = self._rev_for(rev_id, token)
        if not rev:
            return self._access_denied_response()
        file_name = rev.file_name or 'file'
        ext = self._get_ext(file_name)
        content_url = '/dms/protected/%s/content?token=%s' % (rev.id, token)
        data = None
        protected = False
        if ext in LO_EXT + ('pdf',):
            data = rev._read_data()
            protected = is_password_protected(data, ext)
        if protected:
            if not doc_password:
                return self._password_page(rev, token)
            preview = self._protected_preview(rev, ext, data, doc_password)
            if preview is None:
                request.env['dms.log'].sudo().log('view', revision=rev, details='Wrong document password')
                return self._password_page(rev, token, error='That password is not correct. Try again.')
        elif ext in LO_EXT:
            try:
                preview = rev._cached('.award.html', lambda: build_office_html(data, ext).encode()).decode()
            except Exception as e:  # noqa: BLE001
                _logger.warning("DMS protected view %s: %s", rev.id, e)
                preview = self._error_html(
                    'Preview not available',
                    'LibreOffice could not open this file. It may be damaged or in an unsupported format.')
        elif ext == 'pdf':
            preview = self._generate_preview(ext, content_url, None, rev, None)
        elif ext in HTML_EXT:
            try:
                preview = rev._cached('.award.html', lambda: pdf_pages_html(html_to_pdf(rev._read_data())).encode()).decode()
            except Exception as e:  # noqa: BLE001
                _logger.warning("DMS protected view %s (html): %s", rev.id, e)
                preview = self._preview_text_data(rev._read_data())
        elif ext in IMAGE_EXT:
            preview = self._preview_image(content_url, file_name)
        elif ext in IMAGE_CONVERT_EXT:
            preview = self._preview_image('/dms/protected/%s/image?token=%s' % (rev.id, token), file_name)
        elif ext in VIDEO_EXT:
            preview = self._preview_video(content_url, 'mp4' if ext in ('mov', 'm4v') else ext)
        elif ext in AUDIO_EXT:
            preview = ('<div style="display:flex;justify-content:center;align-items:center;min-height:calc(100vh - 60px);'
                       'background:#f0f0f0;"><audio controls controlslist="nodownload" oncontextmenu="return false;" '
                       'src="%s" style="width:min(640px,90%%);"></audio></div>' % content_url)
        elif ext == 'zip':
            preview = self._preview_zip(rev)
        elif ext == 'csv':
            preview = self._preview_csv_data(rev._read_data())
        elif ext in TEXT_EXT or (rev.mimetype or '').startswith('text/'):
            preview = self._preview_text_data(rev._read_data())
        else:
            # last try: LibreOffice opens many more formats than the ones listed above
            try:
                preview = rev._cached('.award.html', lambda: build_office_html(rev._read_data(), ext or 'bin').encode()).decode()
            except Exception:  # noqa: BLE001
                preview = self._preview_unsupported(file_name, ext)
        request.env['dms.log'].sudo().log('view', revision=rev, details='Protected viewer')
        doc = rev.document_id
        html = self._page_shell(
            title='%s %s (Rev %s)' % (doc.code, doc.name, rev.rev_label),
            desc=doc.folder_id.complete_name,
            ftype=ext.upper(),
            preview=preview,
            user_name=request.env.user.name or 'User',
        )
        return request.make_response(html, headers=[
            ('Content-Type', 'text/html; charset=utf-8'),
            ('Cache-Control', 'no-store, no-cache, must-revalidate'),
        ])

    def _preview_zip(self, rev):
        """List what is inside an archive (nothing is extracted or opened)."""
        try:
            entries = rev._zip_listing()
        except Exception as e:  # noqa: BLE001
            return self._error_html('Archive cannot be read', str(e))
        esc = lambda v: (v or '').replace('&', '&amp;').replace('<', '&lt;')
        rows = ''.join(
            '<tr><td style="padding:6px 12px;">%s %s</td><td style="padding:6px 12px;text-align:right;color:#666;">%s</td></tr>'
            % ('&#128193;' if e['dir'] else '&#128196;', esc(e['name']),
               '' if e['dir'] else '{:,} bytes'.format(e['size'])) for e in entries)
        return ('<div style="padding:24px;background:#f5f5f5;min-height:calc(100vh - 60px);user-select:none;">'
                '<div style="max-width:900px;margin:0 auto;background:#fff;border-radius:8px;box-shadow:0 2px 10px rgba(0,0,0,.08);">'
                '<div style="padding:14px 16px;font-weight:600;border-bottom:1px solid #eee;">%d item(s) in this archive</div>'
                '<table style="width:100%%;border-collapse:collapse;font-size:13px;">%s</table></div></div>') % (len(entries), rows)

    @http.route('/dms/protected/<int:rev_id>/image', type='http', auth='user', website=False)
    def protected_image(self, rev_id, token=None, **kw):
        """TIFF / BMP and other images browsers cannot show, converted to PNG."""
        rev = self._rev_for(rev_id, token)
        if not rev:
            return self._access_denied_response()
        png = rev._image_png()
        return request.make_response(png, headers=[
            ('Content-Type', 'image/png'), ('Content-Length', len(png)),
            ('Cache-Control', 'no-store, no-cache, must-revalidate'), ('X-Content-Type-Options', 'nosniff')])

    @http.route('/dms/protected/<int:rev_id>/content', type='http', auth='user', website=False)
    def protected_content(self, rev_id, token=None, **kw):
        """Raw bytes for the viewer's own pdf.js / image / video tags (token-bound, like /web/content)."""
        rev = self._rev_for(rev_id, token)
        if not rev:
            return self._access_denied_response()
        data = rev._read_data()
        return request.make_response(data, headers=[
            ('Content-Type', rev.mimetype or 'application/octet-stream'),
            ('Content-Length', len(data)),
            ('Content-Disposition', 'inline'),
            ('Cache-Control', 'no-store, no-cache, must-revalidate'),
            ('X-Content-Type-Options', 'nosniff'),
        ])
