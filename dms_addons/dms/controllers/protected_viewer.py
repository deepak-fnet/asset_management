"""DMS revisions shown in the award_management protected viewer (award_viewer.py).

Flow: the DMS app asks /dms/protected/token for a session-bound token, then opens
/dms/protected/<rev_id>?token=... in a new tab. The page, its screenshot shield and key
blocking are the award controller's own. Office pages are cached per revision, so only
the first view of a revision waits for LibreOffice.
"""
import logging
import os

from odoo import http, _
from odoo.exceptions import AccessError
from odoo.http import request

from .award_viewer import AwardStyleDocumentViewer
from .main import DmsController, _env, _unlocked

_logger = logging.getLogger(__name__)

MODEL = 'dms.revision'
LO_EXT = ('xlsx', 'xls', 'xlsm', 'ods', 'doc', 'docx', 'odt', 'rtf', 'ppt', 'pptx', 'odp')


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

    @http.route('/dms/protected/<int:rev_id>', type='http', auth='user', website=False)
    def protected_view(self, rev_id, token=None, **kw):
        rev = self._rev_for(rev_id, token)
        if not rev:
            return self._access_denied_response()
        file_name = rev.file_name or 'file'
        ext = self._get_ext(file_name)
        content_url = '/dms/protected/%s/content?token=%s' % (rev.id, token)
        if ext in LO_EXT:
            try:
                preview = rev._cached('.award.html', lambda: build_office_html(rev._read_data(), ext).encode()).decode()
            except Exception as e:  # noqa: BLE001
                _logger.warning("DMS protected view %s: %s", rev.id, e)
                preview = self._error_html('Preview failed', 'LibreOffice could not open this file.')
        elif ext in ('pdf', 'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg', 'mp4', 'webm', 'ogg'):
            preview = self._generate_preview(ext, content_url, None, rev, None)
        else:
            preview = self._generate_preview(ext, content_url, rev._read_data(), rev, None)
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
