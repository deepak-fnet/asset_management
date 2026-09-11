import base64
import io
import os
import secrets
import shutil
import subprocess
import tempfile
import logging

from odoo import http
from odoo.http import request, content_disposition
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

try:
    from openpyxl import load_workbook

    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False

try:
    from PIL import Image

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


class GenericDocumentController(http.Controller):
    """
    Generic document viewer — works with ANY Odoo model.
    (Cursor-anchored zoom update + PrintScreen block + reload button)

    Reused as-is from the award_management module's document viewer: it
    already takes `model`/`field`/`fname` as query params, so it needed no
    change to serve document.management.line files too - see the
    /dms/document/view and /dms/document/download convenience routes below.
    """

    # ══════════════════════════════════════════════════════════
    #  SINGLE FILE — View Only
    # ══════════════════════════════════════════════════════════

    @http.route('/doc/view/<int:doc_id>',
                type='http', auth='user', website=False)
    def view_single(self, doc_id, model=None, field='file',
                    fname='file_name', token=None, **kwargs):
        if not model:
            return request.not_found()

        if not self._consume_view_token(model, doc_id, token):
            return self._access_denied_response()

        try:
            doc = self._get_record(model, doc_id)
        except AccessError:
            return self._access_denied_response()
        if not doc or not getattr(doc, field, None):
            return request.not_found()

        file_name = getattr(doc, fname, None) or 'document'
        file_data = base64.b64decode(getattr(doc, field))
        ext = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else ''

        # Strip folder path from filename (fixes ZIP-extracted files like "folder/file.pdf")
        safe_name = os.path.basename(file_name)
        content_url = '/web/content/%s/%s/%s/%s' % (
            model, doc.id, field, safe_name)

        preview_html = self._generate_preview(
            ext, content_url, file_data, doc, field)

        user = request.env.user
        title = getattr(doc, 'name', None) or file_name

        page_html = self._page_shell(
            title=title,
            desc=model,
            ftype=ext.upper(),
            preview=preview_html,
            user_name=user.name or 'User',
        )
        return request.make_response(page_html, headers=[
            ('Content-Type', 'text/html; charset=utf-8'),
            ('Cache-Control', 'no-store, no-cache, must-revalidate'),
        ])

    @http.route('/doc/view/multi',
                type='http', auth='user', website=False)
    def view_multi(self, model=None, ids=None, field='file',
                   fname='file_name', token=None, **kwargs):
        if not model or not ids:
            return request.not_found()

        if not self._consume_view_token(model, ids, token):
            return self._access_denied_response()

        try:
            doc_ids = [int(x.strip()) for x in ids.split(',') if x.strip()]
        except ValueError:
            return request.not_found()

        if not doc_ids:
            return request.not_found()

        records = []
        for did in doc_ids:
            try:
                rec = self._get_record(model, did)
            except AccessError:
                continue
            if rec:
                records.append(rec)
        if not records:
            return request.not_found()

        tabs = []
        for doc in records:
            if not getattr(doc, field, None):
                continue
            file_name = getattr(doc, fname, None) or 'document'
            ext = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else ''
            tab_name = getattr(doc, 'name', None) or file_name
            tab_token = self._mint_view_token(model, doc.id)
            view_url = '/doc/view/%s?model=%s&field=%s&fname=%s&token=%s' % (
                doc.id, model, field, fname, tab_token)
            tabs.append({
                'id': doc.id,
                'name': tab_name,
                'ext': ext.upper(),
                'url': view_url,
            })

        if not tabs:
            return request.not_found()

        multi_preview = self._build_multi_viewer(tabs)

        user = request.env.user
        page_html = self._page_shell(
            title='Documents (%d files)' % len(tabs),
            desc=model,
            ftype='MULTI',
            preview=multi_preview,
            user_name=user.name or 'User',
        )
        return request.make_response(page_html, headers=[
            ('Content-Type', 'text/html; charset=utf-8'),
            ('Cache-Control', 'no-store, no-cache, must-revalidate'),
        ])

    @http.route('/doc/download/<int:doc_id>',
                type='http', auth='user')
    def download_file(self, doc_id, model=None, field='file',
                      fname='file_name', token=None, **kwargs):
        if not model:
            return request.not_found()

        if not self._consume_view_token(model, doc_id, token):
            return self._access_denied_response()

        try:
            doc = self._get_record(model, doc_id)
        except AccessError:
            return self._access_denied_response()
        if not doc or not getattr(doc, field, None):
            return request.not_found()

        file_content = base64.b64decode(getattr(doc, field))
        file_name = getattr(doc, fname, None) or 'download'

        return request.make_response(file_content, headers=[
            ('Content-Type', 'application/octet-stream'),
            ('Content-Disposition', content_disposition(file_name)),
        ])

    @http.route('/dms/document/view/<int:doc_id>',
                type='http', auth='user', website=False)
    def view_dms_doc(self, doc_id, **kwargs):
        return self.view_single(
            doc_id, model='document.management.line',
            field='file', fname='file_name', **kwargs)

    @http.route('/dms/document/download/<int:doc_id>',
                type='http', auth='user')
    def download_dms_doc(self, doc_id, **kwargs):
        return self.download_file(
            doc_id, model='document.management.line',
            field='file', fname='file_name', **kwargs)

    # ══════════════════════════════════════════════════════════
    #  HELPERS
    # ══════════════════════════════════════════════════════════

    # ── One-time view tokens ────────────────────────────────────
    # A /doc/view or /doc/download URL only works ONCE. It's minted the
    # moment a user clicks a real "View"/"Download" button and is deleted
    # from the session the instant it's used — so copying the URL and
    # pasting it anywhere (a new tab in the same browser, a different
    # browser, another person entirely) never works, because the token
    # has already been spent. Callers building one of these URLs must
    # mint a token first and append it as `&token=...`; see
    # _mint_view_token below.

    SESSION_TOKEN_KEY = 'doc_view_tokens'

    def _mint_view_token(self, model, doc_id):
        token = secrets.token_urlsafe(24)
        tokens = dict(request.session.get(self.SESSION_TOKEN_KEY) or {})
        tokens[token] = '%s,%s' % (model, doc_id)
        request.session[self.SESSION_TOKEN_KEY] = tokens
        return token

    def _consume_view_token(self, model, doc_id, token):
        """Validate the token and burn it in the same step, so it can
        never be replayed — from this tab, another tab, or anyone else."""
        if not token:
            return False
        tokens = dict(request.session.get(self.SESSION_TOKEN_KEY) or {})
        if tokens.get(token) != '%s,%s' % (model, doc_id):
            return False
        del tokens[token]
        request.session[self.SESSION_TOKEN_KEY] = tokens
        return True

    def _get_record(self, model, doc_id):
        """Fetch a record as the CURRENT user (no sudo) so Odoo's normal
        ir.model.access and ir.rule checks decide whether this user may
        view it. Returns None if the model/record doesn't exist, but lets
        AccessError propagate so callers can tell "not found" apart from
        "exists but you're not allowed to see it" (copied-URL scenario)."""
        try:
            Model = request.env[model]
        except KeyError:
            return None
        rec = Model.browse(doc_id)
        if not rec.exists():
            return None
        rec.check_access_rights('read', raise_exception=True)
        rec.check_access_rule('read')
        return rec

    def _access_denied_response(self):
        html = (
                '<!DOCTYPE html><html><head><meta charset="UTF-8"/>'
                '<title>Access Denied</title></head>'
                '<body style="margin:0;font-family:Segoe UI,Arial,sans-serif;'
                'background:#f5f5f5;display:flex;align-items:center;'
                'justify-content:center;height:100vh;">'
                '<div style="text-align:center;padding:40px;">'
                '<div style="font-size:60px;margin-bottom:20px;">&#128274;</div>'
                '<h2 style="color:#d9534f;margin-bottom:12px;">Access Denied</h2>'
                '<p style="color:#666;max-width:420px;line-height:1.6;">'
                'You do not have permission to view this document. '
                'If you believe this is a mistake, contact the person who '
                'shared this link or your administrator.</p>'
                '</div></body></html>'
        )
        return request.make_response(html, headers=[
            ('Content-Type', 'text/html; charset=utf-8'),
            ('Cache-Control', 'no-store, no-cache, must-revalidate'),
        ], status=403)

    def _get_ext(self, file_name):
        if file_name and '.' in file_name:
            return file_name.rsplit('.', 1)[-1].lower()
        return ''

    def _generate_preview(self, ext, content_url, file_data, doc, field):
        if ext == 'pdf':
            return self._preview_pdf(content_url)
        elif ext in ('xlsx', 'xls', 'doc', 'docx', 'pptx', 'ppt'):
            return self._preview_libreoffice(file_data, ext)
        elif ext == 'csv':
            return self._preview_csv_data(file_data)
        elif ext in ('png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg'):
            return self._preview_image(content_url, '')
        elif ext in ('mp4', 'webm', 'ogg'):
            return self._preview_video(content_url, ext)
        elif ext in ('txt', 'log', 'xml', 'json', 'py', 'js', 'css'):
            return self._preview_text_data(file_data)
        else:
            return self._preview_unsupported('document', ext)

    def _build_multi_viewer(self, tabs):
        tab_buttons = ''
        for idx, tab in enumerate(tabs):
            active = ' doc-tab-active' if idx == 0 else ''
            tab_buttons += (
                    '<button class="doc-tab%s" onclick="switchDocTab(%d)">'
                    '<span class="doc-tab-name">%s</span>'
                    '<span class="doc-tab-ext">%s</span>'
                    '</button>' % (active, idx,
                                   tab['name'].replace('<', '&lt;'),
                                   tab['ext'])
            )

        tab_panels = ''
        for idx, tab in enumerate(tabs):
            display = 'block' if idx == 0 else 'none'
            if idx == 0:
                tab_panels += (
                        '<div class="doc-panel" id="doc-panel-%d" '
                        'style="display:%s;" data-loaded="1">'
                        '<iframe src="%s" class="doc-iframe" '
                        'frameborder="0"></iframe>'
                        '</div>' % (idx, display, tab['url'])
                )
            else:
                tab_panels += (
                        '<div class="doc-panel" id="doc-panel-%d" '
                        'style="display:%s;" data-loaded="0" '
                        'data-src="%s">'
                        '<div class="doc-loading">'
                        '<div class="doc-loading-spinner"></div>'
                        '<div class="doc-loading-text">Click to load document</div>'
                        '</div>'
                        '</div>' % (idx, display, tab['url'])
                )

        return (
                '<style>'
                '.doc-tabs-bar{background:#2c2c3a;padding:0 16px;'
                '  display:flex;align-items:end;gap:2px;'
                '  overflow-x:auto;position:sticky;top:0;z-index:60;'
                '  border-bottom:2px solid #875A7B;}'
                '.doc-tab{background:#3a3a4a;color:#aaa;border:none;'
                '  padding:10px 20px;cursor:pointer;font-size:13px;'
                '  border-radius:8px 8px 0 0;display:flex;'
                '  align-items:center;gap:8px;white-space:nowrap;'
                '  transition:all 0.15s;min-width:0;'
                '  border:1px solid transparent;border-bottom:none;}'
                '.doc-tab:hover{background:#4a4a5a;color:#ddd;}'
                '.doc-tab-active{background:#875A7B;color:#fff;'
                '  border-color:rgba(255,255,255,0.2);}'
                '.doc-tab-name{max-width:180px;overflow:hidden;'
                '  text-overflow:ellipsis;font-weight:500;}'
                '.doc-tab-ext{font-size:10px;opacity:0.7;'
                '  background:rgba(255,255,255,0.1);'
                '  padding:2px 6px;border-radius:3px;}'
                '.doc-tab-active .doc-tab-ext{background:rgba(255,255,255,0.2);}'
                '.doc-panel{height:calc(100vh - 90px);}'
                '.doc-iframe{width:100%;height:100%;border:none;}'
                '.doc-loading{display:flex;flex-direction:column;align-items:center;'
                '  justify-content:center;height:100%;'
                '  background:#34495e;color:#aaa;gap:15px;}'
                '.doc-loading-spinner{width:40px;height:40px;'
                '  border:4px solid rgba(255,255,255,0.1);'
                '  border-top:4px solid #875A7B;border-radius:50%;'
                '  animation:spin 0.8s linear infinite;}'
                '@keyframes spin{to{transform:rotate(360deg);}}'
                '.doc-loading-text{font-size:14px;}'
                '</style>'
                '<div class="doc-tabs-bar">' + tab_buttons + '</div>'
                + tab_panels +
                '<script>'
                'function switchDocTab(idx){'
                '  document.querySelectorAll(".doc-panel").forEach(function(p){'
                '    p.style.display="none";});'
                '  document.querySelectorAll(".doc-tab").forEach(function(t){'
                '    t.classList.remove("doc-tab-active");});'
                '  var panel=document.getElementById("doc-panel-"+idx);'
                '  if(!panel)return;'
                '  panel.style.display="block";'
                '  var tabs=document.querySelectorAll(".doc-tab");'
                '  if(tabs[idx])tabs[idx].classList.add("doc-tab-active");'
                '  if(panel.getAttribute("data-loaded")==="0"){'
                '    panel.setAttribute("data-loaded","1");'
                '    var src=panel.getAttribute("data-src");'
                '    panel.innerHTML='
                '      \'<iframe src="\'+src+\'" class="doc-iframe" \'+'
                '      \'frameborder="0"></iframe>\';'
                '  }'
                '}'
                '</script>'
        )

    # ══════════════════════════════════════════════════════════
    #  PREVIEW: PDF  (pdf.js canvas) — CURSOR-ANCHORED ZOOM
    # ══════════════════════════════════════════════════════════

    def _preview_pdf(self, content_url):
        return (
                '<div id="pdf-toolbar" style="background:#323639;padding:6px 16px;display:flex;'
                'align-items:center;justify-content:center;gap:12px;position:sticky;top:0;z-index:50;">'
                '  <button onclick="pdfPrevPage()" style="background:none;border:1px solid #555;'
                '    color:#ccc;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:13px;">'
                '    &#9664; Prev</button>'
                '  <span style="color:#ccc;font-size:13px;">'
                '    Page <span id="page-num">1</span> of <span id="page-count">-</span>'
                '  </span>'
                '  <button onclick="pdfNextPage()" style="background:none;border:1px solid #555;'
                '    color:#ccc;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:13px;">'
                '    Next &#9654;</button>'
                '  <span style="color:#555;margin:0 8px;">|</span>'
                '  <button onclick="pdfZoom(-0.2)" style="background:none;border:1px solid #555;'
                '    color:#ccc;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:15px;'
                '    font-weight:bold;">&#8722;</button>'
                '  <span id="zoom-level" style="color:#ccc;font-size:13px;min-width:45px;'
                '    text-align:center;">150%</span>'
                '  <button onclick="pdfZoom(0.2)" style="background:none;border:1px solid #555;'
                '    color:#ccc;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:15px;'
                '    font-weight:bold;">+</button>'
                '  <button onclick="pdfZoomFit()" style="background:none;border:1px solid #555;'
                '    color:#ccc;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:12px;">'
                '    Fit</button>'
                '</div>'
                '<div id="pdf-container" style="background:#525659;overflow:auto;'
                '  height:calc(100vh - 98px);display:flex;flex-direction:column;'
                '  align-items:center;padding:10px 0;">'
                '  <div id="pdf-loading" style="color:#aaa;text-align:center;'
                '    padding:80px;font-size:15px;">Loading PDF...</div>'
                '</div>'
                '<script>'
                '(function(){'
                '  var url="' + content_url + '";'
                                              '  var pdfDoc=null,currentScale=1.5;'
                                              '  var container=document.getElementById("pdf-container");'

                # ── Mouse tracking for cursor-anchored zoom ──
                                              '  var cursorX=null,cursorY=null;'
                                              '  container.addEventListener("mousemove",function(e){'
                                              '    var r=container.getBoundingClientRect();'
                                              '    cursorX=e.clientX-r.left;'
                                              '    cursorY=e.clientY-r.top;'
                                              '  });'
                                              '  container.addEventListener("mouseleave",function(){'
                                              '    cursorX=null;cursorY=null;'
                                              '  });'
                                              '  function getAnchor(){'
                                              '    var r=container.getBoundingClientRect();'
                                              '    if(cursorX===null||cursorY===null||'
                                              '       cursorX<0||cursorY<0||cursorX>r.width||cursorY>r.height){'
                                              '      return {x:r.width/2,y:r.height/2};'
                                              '    }'
                                              '    return {x:cursorX,y:cursorY};'
                                              '  }'

                # pdf.js is shipped as an ES module in this Odoo version (Odoo's own
                # loader loads it with type="module"), so it must be dynamic-imported
                # rather than loaded via a classic <script src> tag.
                                              '  import("/web/static/lib/pdfjs/build/pdf.js").then(function(pdfLib){'
                                              '  pdfLib.GlobalWorkerOptions.workerSrc="/web/static/lib/pdfjs/build/pdf.worker.js";'
                                              '  pdfLib.getDocument(url).promise.then(function(pdf){'
                                              '    pdfDoc=pdf;'
                                              '    document.getElementById("page-count").textContent=pdf.numPages;'
                                              '    document.getElementById("pdf-loading").style.display="none";'
                                              '    renderAllPages();'
                                              '  }).catch(function(err){'
                                              '    document.getElementById("pdf-loading").innerHTML='
                                              '      "<div style=\\"color:#f66\\">Failed to load PDF</div>"+'
                                              '      "<div style=\\"color:#999;font-size:12px;margin-top:8px\\">"+err.message+"</div>";'
                                              '  });'

                                              '  function renderAllPages(){'
                                              '    var old=container.querySelectorAll(".pdf-page-wrap");'
                                              '    old.forEach(function(el){el.remove();});'
                                              '    var promises=[];'
                                              '    for(var i=1;i<=pdfDoc.numPages;i++){promises.push(renderPage(i));}'
                                              '    return Promise.all(promises);'
                                              '  }'
                                              '  function renderPage(num){'
                                              '    return pdfDoc.getPage(num).then(function(page){'
                                              '      var dpr=window.devicePixelRatio||1;'
                                              '      var viewport=page.getViewport({scale:currentScale});'
                                              '      var wrapper=document.createElement("div");'
                                              '      wrapper.className="pdf-page-wrap";'
                                              '      wrapper.setAttribute("data-page",num);'
                                              '      wrapper.style.cssText="margin:8px auto;box-shadow:0 2px 8px rgba(0,0,0,0.4);background:#fff;";'
                                              '      var canvas=document.createElement("canvas");'
                                              '      canvas.width=Math.floor(viewport.width*dpr);'
                                              '      canvas.height=Math.floor(viewport.height*dpr);'
                                              '      canvas.style.width=Math.floor(viewport.width)+"px";'
                                              '      canvas.style.height=Math.floor(viewport.height)+"px";'
                                              '      canvas.style.display="block";'
                                              '      canvas.setAttribute("oncontextmenu","return false");'
                                              '      var ctx=canvas.getContext("2d");ctx.scale(dpr,dpr);'
                                              '      wrapper.appendChild(canvas);container.appendChild(wrapper);'
                                              '      page.render({canvasContext:ctx,viewport:viewport});'
                                              '    });'
                                              '  }'
                                              '  var currentPage=1;'
                                              '  window.pdfPrevPage=function(){'
                                              '    if(currentPage<=1)return;currentPage--;scrollToPage(currentPage);};'
                                              '  window.pdfNextPage=function(){'
                                              '    if(!pdfDoc||currentPage>=pdfDoc.numPages)return;'
                                              '    currentPage++;scrollToPage(currentPage);};'
                                              '  function scrollToPage(num){'
                                              '    var el=container.querySelector("[data-page=\\""+num+"\\"]");'
                                              '    if(el)el.scrollIntoView({behavior:"smooth",block:"start"});'
                                              '    document.getElementById("page-num").textContent=num;'
                                              '  }'
                                              '  container.addEventListener("scroll",function(){'
                                              '    var pages=container.querySelectorAll(".pdf-page-wrap");'
                                              '    var cr=container.getBoundingClientRect();'
                                              '    var mid=cr.top+cr.height/3;'
                                              '    for(var i=0;i<pages.length;i++){'
                                              '      var r=pages[i].getBoundingClientRect();'
                                              '      if(r.top<=mid&&r.bottom>mid){'
                                              '        currentPage=parseInt(pages[i].getAttribute("data-page"));'
                                              '        document.getElementById("page-num").textContent=currentPage;'
                                              '        break;}}'
                                              '  });'

                # ── CURSOR-ANCHORED pdfZoom ──
                                              '  window.pdfZoom=function(d){'
                                              '    if(!pdfDoc)return;'
                                              '    var oldScale=currentScale;'
                                              '    var newScale=Math.max(0.4,Math.min(3.0,currentScale+d));'
                                              '    if(newScale===oldScale)return;'
                                              '    var ratio=newScale/oldScale;'
                                              '    var a=getAnchor();'
                                              '    var oldScrollLeft=container.scrollLeft;'
                                              '    var oldScrollTop=container.scrollTop;'
                                              '    currentScale=newScale;'
                                              '    document.getElementById("zoom-level").textContent='
                                              '      Math.round(currentScale*100)+"%";'
                                              '    renderAllPages().then(function(){'
                                              '      container.scrollLeft=oldScrollLeft*ratio+a.x*(ratio-1);'
                                              '      container.scrollTop=oldScrollTop*ratio+a.y*(ratio-1);'
                                              '    });'
                                              '  };'
                                              '  window.pdfZoomFit=function(){'
                                              '    if(!pdfDoc)return;'
                                              '    var oldScale=currentScale;'
                                              '    var newScale=1.5;'
                                              '    if(newScale===oldScale)return;'
                                              '    var ratio=newScale/oldScale;'
                                              '    var a=getAnchor();'
                                              '    var oldScrollLeft=container.scrollLeft;'
                                              '    var oldScrollTop=container.scrollTop;'
                                              '    currentScale=newScale;'
                                              '    document.getElementById("zoom-level").textContent="150%";'
                                              '    renderAllPages().then(function(){'
                                              '      container.scrollLeft=oldScrollLeft*ratio+a.x*(ratio-1);'
                                              '      container.scrollTop=oldScrollTop*ratio+a.y*(ratio-1);'
                                              '    });'
                                              '  };'

                # ── Ctrl + wheel to zoom at cursor ──
                                              '  container.addEventListener("wheel",function(e){'
                                              '    if(e.ctrlKey||e.metaKey){'
                                              '      e.preventDefault();'
                                              '      var r=container.getBoundingClientRect();'
                                              '      cursorX=e.clientX-r.left;'
                                              '      cursorY=e.clientY-r.top;'
                                              '      window.pdfZoom(e.deltaY<0?0.2:-0.2);'
                                              '    }'
                                              '  },{passive:false});'
                                              '  }).catch(function(err){'
                                              '    document.getElementById("pdf-loading").innerHTML='
                                              '      "<div style=\\"color:#f66\\">Failed to load PDF viewer</div>"+'
                                              '      "<div style=\\"color:#999;font-size:12px;margin-top:8px\\">"+err.message+"</div>";'
                                              '  });'

                                              '})();'
                                              '</script>'
        )

    # ══════════════════════════════════════════════════════════
    #  PREVIEW: XLSX/XLS/DOC/DOCX (LibreOffice -> PDF -> PNG)
    # ══════════════════════════════════════════════════════════

    def _preview_libreoffice(self, file_data, ext):
        temp_dir = None
        try:
            temp_dir = tempfile.mkdtemp(prefix='odoo_doc_view_')
            input_path = os.path.join(temp_dir, 'input.%s' % ext)
            with open(input_path, 'wb') as f:
                f.write(file_data)

            if ext in ('xlsx', 'xls') and OPENPYXL_AVAILABLE:
                try:
                    self._set_excel_page_layout(input_path)
                except Exception as e:
                    _logger.warning("Page layout skipped: %s", e)

            try:
                subprocess.run(
                    ['libreoffice', '--headless', '--invisible',
                     '--convert-to', 'pdf', '--outdir', temp_dir,
                     input_path],
                    check=True, timeout=300, capture_output=True,
                    text=True,
                    env={**os.environ, 'SAL_USE_VCLPLUGIN': 'svp'})
            except FileNotFoundError:
                return self._error_html(
                    'LibreOffice Not Installed',
                    '<code>sudo apt-get install -y libreoffice</code>')
            except subprocess.TimeoutExpired:
                return self._error_html('Timed Out', 'File too large.')

            pdf_path = input_path.rsplit('.', 1)[0] + '.pdf'
            if not os.path.exists(pdf_path):
                return self._error_html('Failed', 'Conversion failed.')

            try:
                subprocess.run(
                    ['pdftoppm', '-png', '-r', '400',
                     '-aa', 'yes', '-aaVector', 'yes',
                     pdf_path, os.path.join(temp_dir, 'page')],
                    check=True, timeout=300, capture_output=True,
                    text=True)
            except FileNotFoundError:
                return self._error_html(
                    'poppler-utils Missing',
                    '<code>sudo apt-get install -y poppler-utils</code>')
            except subprocess.TimeoutExpired:
                return self._error_html('Timed Out', 'Too many pages.')

            png_files = sorted(
                [f for f in os.listdir(temp_dir) if f.endswith('.png')],
                key=lambda x: int(
                    ''.join(filter(str.isdigit, x)) or '0'))

            if not png_files:
                return self._error_html('Empty', 'No pages generated.')

            images_html = ''
            for idx, pf in enumerate(png_files):
                with open(os.path.join(temp_dir, pf), 'rb') as f:
                    png_data = f.read()
                if PIL_AVAILABLE:
                    try:
                        img = Image.open(io.BytesIO(png_data))
                        if img.mode not in ('RGB', 'RGBA'):
                            img = img.convert('RGB')
                        buf = io.BytesIO()
                        img.save(buf, format='PNG',
                                 optimize=False, compress_level=1)
                        png_data = buf.getvalue()
                    except Exception:
                        pass
                img64 = base64.b64encode(png_data).decode()
                images_html += (
                        '<div class="xl-page-wrap" data-page="%d">'
                        '<img src="data:image/png;base64,%s" '
                        'draggable="false" oncontextmenu="return false" '
                        'style="display:block;width:100%%;'
                        'pointer-events:none;"/>'
                        '</div>' % (idx + 1, img64))

            return self._doc_image_viewer_html(
                images_html, len(png_files))
        except Exception as e:
            _logger.error("[DOC VIEW] Error: %s", str(e))
            return self._error_html('Error', str(e))
        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _set_excel_page_layout(self, path):
        wb = load_workbook(path, data_only=False)
        for ws in wb.worksheets:
            ws.page_setup.orientation = 'landscape'
            ws.page_setup.paperSize = 9
            ws.page_setup.fitToWidth = 1
            ws.page_setup.fitToHeight = 0
            ws.sheet_properties.pageSetUpPr.fitToPage = True
            ws.page_margins.left = 0.25
            ws.page_margins.right = 0.25
            ws.page_margins.top = 0.3
            ws.page_margins.bottom = 0.3
        wb.save(path)
        wb.close()

    # ══════════════════════════════════════════════════════════
    #  XL IMAGE VIEWER — CURSOR-ANCHORED ZOOM
    # ══════════════════════════════════════════════════════════

    def _doc_image_viewer_html(self, images_html, total_pages):
        return (
                '<style>'
                '.xl-toolbar{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);'
                '  padding:8px 20px;display:flex;align-items:center;'
                '  justify-content:center;gap:8px;position:sticky;'
                '  top:0;z-index:50;flex-wrap:wrap;'
                '  box-shadow:0 2px 10px rgba(0,0,0,0.3);}'
                '.xl-btn{background:rgba(255,255,255,0.15);color:#fff;'
                '  border:1px solid rgba(255,255,255,0.3);'
                '  padding:5px 14px;border-radius:5px;cursor:pointer;'
                '  font-size:13px;font-weight:600;transition:all 0.15s;'
                '  white-space:nowrap;}'
                '.xl-btn:hover{background:rgba(255,255,255,0.25);}'
                '.xl-btn.active{background:rgba(255,255,255,0.4);'
                '  border-color:#fff;}'
                '.xl-zoom-display{background:rgba(255,255,255,0.95);'
                '  color:#667eea;padding:5px 14px;border-radius:5px;'
                '  font-weight:700;font-size:14px;min-width:60px;'
                '  text-align:center;}'
                '.xl-sep{color:rgba(255,255,255,0.3);margin:0 4px;}'
                '.xl-page-info{color:rgba(255,255,255,0.85);font-size:12px;'
                '  font-weight:500;background:rgba(0,0,0,0.2);'
                '  padding:5px 12px;border-radius:12px;}'
                '.xl-container{background:#34495e;overflow:auto;'
                '  height:calc(100vh - 96px);padding:20px;}'
                '.xl-page-wrap{background:#fff;'
                '  box-shadow:0 4px 20px rgba(0,0,0,0.3);'
                '  border-radius:4px;overflow:hidden;'
                '  margin:0 auto 20px auto;line-height:0;'
                '  transition:width 0.2s ease;}'
                '</style>'
                '<div class="xl-toolbar">'
                '  <button class="xl-btn" onclick="xlZoom(50)">50%</button>'
                '  <button class="xl-btn" onclick="xlZoom(75)">75%</button>'
                '  <button class="xl-btn active" onclick="xlZoom(100)">100%</button>'
                '  <button class="xl-btn" onclick="xlZoom(125)">125%</button>'
                '  <button class="xl-btn" onclick="xlZoom(150)">150%</button>'
                '  <button class="xl-btn" onclick="xlZoom(200)">200%</button>'
                '  <button class="xl-btn" onclick="xlZoom(300)">300%</button>'
                '  <span class="xl-sep">|</span>'
                '  <button class="xl-btn" onclick="xlZoomDelta(-10)"'
                '    style="font-size:16px;padding:3px 10px;font-weight:bold;"'
                '    >&#8722;</button>'
                '  <div class="xl-zoom-display" id="xl-zoom">100%</div>'
                '  <button class="xl-btn" onclick="xlZoomDelta(10)"'
                '    style="font-size:16px;padding:3px 10px;font-weight:bold;"'
                '    >+</button>'
                '  <span class="xl-sep">|</span>'
                '  <div class="xl-page-info">'
                '    Page <span id="xl-cur-page">1</span> / '
                + str(total_pages) +
                '  </div>'
                '</div>'
                '<div class="xl-container" id="xl-container">'
                + images_html +
                '</div>'
                '<script>'
                '(function(){'
                '  var c=document.getElementById("xl-container");'
                '  var zd=document.getElementById("xl-zoom");'
                '  var cp=document.getElementById("xl-cur-page");'
                '  var cz=100;var pp=c.querySelectorAll(".xl-page-wrap");'

                # ── Mouse tracking for cursor-anchored zoom ──
                '  var cursorX=null,cursorY=null;'
                '  c.addEventListener("mousemove",function(e){'
                '    var r=c.getBoundingClientRect();'
                '    cursorX=e.clientX-r.left;'
                '    cursorY=e.clientY-r.top;'
                '  });'
                '  c.addEventListener("mouseleave",function(){'
                '    cursorX=null;cursorY=null;'
                '  });'
                '  function getAnchor(){'
                '    var r=c.getBoundingClientRect();'
                '    if(cursorX===null||cursorY===null||'
                '       cursorX<0||cursorY<0||cursorX>r.width||cursorY>r.height){'
                '      return {x:r.width/2,y:r.height/2};'
                '    }'
                '    return {x:cursorX,y:cursorY};'
                '  }'

                '  function applyZoom(newZoom){'
                '    newZoom=Math.max(25,Math.min(400,newZoom));'
                '    if(newZoom===cz)return;'
                '    var oldZoom=cz;'
                '    var ratio=newZoom/oldZoom;'
                '    var a=getAnchor();'
                '    var oldScrollLeft=c.scrollLeft;'
                '    var oldScrollTop=c.scrollTop;'
                '    cz=newZoom;'
                '    pp.forEach(function(p){p.style.width=cz+"%";});'
                '    zd.textContent=cz+"%";'
                '    document.querySelectorAll(".xl-btn").forEach(function(b){'
                '      b.classList.toggle("active",'
                '        b.textContent.trim()===cz+"%");});'
                '    requestAnimationFrame(function(){'
                '      c.scrollLeft=oldScrollLeft*ratio+a.x*(ratio-1);'
                '      c.scrollTop=oldScrollTop*ratio+a.y*(ratio-1);'
                '    });'
                '  }'

                '  window.xlZoom=function(l){applyZoom(l);};'
                '  window.xlZoomDelta=function(d){applyZoom(cz+d);};'

                '  c.addEventListener("scroll",function(){'
                '    var cr=c.getBoundingClientRect();'
                '    var mid=cr.top+cr.height/3;'
                '    for(var i=0;i<pp.length;i++){'
                '      var r=pp[i].getBoundingClientRect();'
                '      if(r.top<=mid&&r.bottom>mid){'
                '        cp.textContent=pp[i].getAttribute("data-page");'
                '        break;}}});'

                # ── Ctrl + wheel ──
                '  c.addEventListener("wheel",function(e){'
                '    if(e.ctrlKey||e.metaKey){'
                '      e.preventDefault();'
                '      var r=c.getBoundingClientRect();'
                '      cursorX=e.clientX-r.left;'
                '      cursorY=e.clientY-r.top;'
                '      applyZoom(cz+(e.deltaY<0?10:-10));'
                '    }'
                '  },{passive:false});'
                '})();'
                '</script>'
        )

    # ══════════════════════════════════════════════════════════
    #  PREVIEW: CSV / Image / Video / Text / Unsupported
    # ══════════════════════════════════════════════════════════

    def _preview_csv_data(self, file_data):
        import csv as csv_module
        try:
            text = file_data.decode('utf-8', errors='replace')
            rows = list(csv_module.reader(io.StringIO(text)))
        except Exception as e:
            return self._error_html('CSV Error', str(e))
        if not rows:
            return '<div style="padding:40px;text-align:center;color:#888;">Empty</div>'

        def esc(v):
            if not v:
                return ''
            return str(v).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        h = '<div style="overflow:auto;max-height:calc(100vh - 60px);background:#fff;">'
        h += '<table style="border-collapse:collapse;width:100%;font-size:13px;font-family:Segoe UI,sans-serif;">'
        h += '<thead><tr>'
        for c in rows[0]:
            h += '<th style="background:#f0f0f0;padding:8px 12px;border:1px solid #ddd;font-weight:600;position:sticky;top:0;">' + esc(
                c) + '</th>'
        h += '</tr></thead><tbody>'
        for ri, row in enumerate(rows[1:], 1):
            bg = '#fafafa' if ri % 2 == 0 else '#fff'
            h += '<tr>'
            for c in row:
                h += '<td style="padding:6px 12px;border:1px solid #e8e8e8;background:' + bg + ';white-space:nowrap;">' + esc(
                    c) + '</td>'
            h += '</tr>'
        h += '</tbody></table></div>'
        return h

    def _preview_image(self, content_url, file_name):
        return (
                '<div style="display:flex;justify-content:center;align-items:center;'
                'padding:40px;background:#f0f0f0;min-height:calc(100vh - 60px);user-select:none;">'
                '<img src="' + content_url + '" draggable="false" '
                                             'style="max-width:100%;max-height:calc(100vh - 100px);object-fit:contain;'
                                             'border-radius:4px;box-shadow:0 2px 12px rgba(0,0,0,0.15);pointer-events:none;"/></div>')

    def _preview_video(self, content_url, ext):
        return (
                '<div style="display:flex;justify-content:center;padding:40px;'
                'background:#000;min-height:calc(100vh - 60px);">'
                '<video controls controlslist="nodownload" disablepictureinpicture '
                'oncontextmenu="return false;" '
                'style="max-width:100%;max-height:calc(100vh - 100px);">'
                '<source src="' + content_url + '" type="video/' + ext + '"/>'
                                                                         '</video></div>')

    def _preview_text_data(self, file_data):
        try:
            text = file_data.decode('utf-8', errors='replace')
            text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        except Exception:
            text = 'Unable to decode.'
        return (
                '<div style="padding:30px;background:#1e1e2e;min-height:calc(100vh - 60px);user-select:none;">'
                '<pre style="font-family:monospace;font-size:13px;line-height:1.7;'
                'color:#d4d4d4;white-space:pre-wrap;">' + text + '</pre></div>')

    def _preview_unsupported(self, file_name, ext):
        return (
                '<div style="display:flex;flex-direction:column;align-items:center;'
                'justify-content:center;padding:80px;min-height:calc(100vh - 60px);text-align:center;">'
                '<div style="font-size:80px;margin-bottom:20px;">&#128196;</div>'
                '<h2 style="color:#333;margin-bottom:8px;">' + file_name + '</h2>'
                                                                           '<p style="color:#888;">Cannot preview .' + ext.upper() + ' files.</p></div>')

    def _error_html(self, title, message):
        return (
                '<div style="display:flex;flex-direction:column;align-items:center;'
                'justify-content:center;padding:80px;min-height:calc(100vh - 60px);text-align:center;">'
                '<div style="font-size:60px;margin-bottom:20px;">&#9888;</div>'
                '<h2 style="color:#d9534f;margin-bottom:12px;">' + title + '</h2>'
                                                                           '<p style="color:#666;max-width:500px;line-height:1.6;">' + message + '</p></div>')

    # ══════════════════════════════════════════════════════════
    #  PAGE SHELL — View-only wrapper with all protections
    # ══════════════════════════════════════════════════════════

    def _page_shell(self, title, desc, ftype, preview, user_name='User'):
        return (
                '<!DOCTYPE html><html><head>'
                '<meta charset="UTF-8"/>'
                '<title>' + title + '</title>'
                                    '<style>'
                                    '*{margin:0;padding:0;box-sizing:border-box;}'
                                    'body{font-family:Segoe UI,Arial,sans-serif;background:#f5f5f5;'
                                    '  -webkit-user-select:none;-moz-user-select:none;'
                                    '  -ms-user-select:none;user-select:none;overflow:hidden;'
                                    '  -webkit-touch-callout:none;}'
                                    '@media print{body *{display:none !important;}'
                                    '  body::after{content:"Printing is disabled.";'
                                    '  display:block;font-size:24px;text-align:center;'
                                    '  padding:100px 20px;color:#999;}}'
                                    '.view-badge{display:inline-flex;align-items:center;gap:5px;'
                                    '  background:rgba(255,255,255,0.15);border:1px solid rgba(255,255,255,0.3);'
                                    '  border-radius:20px;padding:4px 14px;font-size:11px;'
                                    '  color:rgba(255,255,255,0.9);font-weight:500;'
                                    '  letter-spacing:0.5px;text-transform:uppercase;}'
                                    '.view-badge svg{width:14px;height:14px;}'

                # ── Shield overlay ──
                                    '#blur-shield{'
                                    '  display:none;position:fixed;inset:0;z-index:99999;'
                                    '  background:rgba(20,20,30,0.97);'
                                    '  backdrop-filter:blur(60px);'
                                    '  -webkit-backdrop-filter:blur(60px);'
                                    '  align-items:center;justify-content:center;'
                                    '  flex-direction:column;gap:16px;}'
                                    '#blur-shield.active{display:flex;}'
                                    '#blur-shield .shield-icon{font-size:72px;'
                                    '  animation:pulse-icon 1.5s ease-in-out infinite;}'
                                    '@keyframes pulse-icon{'
                                    '  0%,100%{transform:scale(1);}50%{transform:scale(1.12);}}'
                                    '#blur-shield .shield-msg{'
                                    '  font-size:22px;font-weight:700;color:#fff;letter-spacing:0.5px;}'
                                    '#blur-shield .shield-sub{'
                                    '  font-size:13px;color:rgba(255,255,255,0.5);}'
                                    '#shield-reload-btn{'
                                    '  margin-top:10px;padding:12px 36px;'
                                    '  background:linear-gradient(135deg,#875A7B,#a06090);'
                                    '  color:#fff;border:none;border-radius:10px;'
                                    '  font-size:15px;font-weight:700;cursor:pointer;'
                                    '  letter-spacing:0.5px;'
                                    '  box-shadow:0 4px 18px rgba(135,90,123,0.5);'
                                    '  transition:all 0.2s;}'
                                    '#shield-reload-btn:hover{'
                                    '  background:linear-gradient(135deg,#9a6a8e,#b070a0);'
                                    '  transform:translateY(-1px);'
                                    '  box-shadow:0 6px 24px rgba(135,90,123,0.7);}'
                                    '#shield-reload-btn:active{transform:translateY(0);}'

                                    '#watermark{position:fixed;inset:0;z-index:9999;pointer-events:none;'
                                    '  overflow:hidden;opacity:0.09;}'
                                    '#watermark span{display:inline-block;font-size:16px;font-weight:700;'
                                    '  color:#000;white-space:nowrap;transform:rotate(-30deg);'
                                    '  padding:50px 40px;letter-spacing:1px;}'
                                    '#doc-content{overflow:auto;height:calc(100vh - 48px);}'
                                    '.pdf-page-wrap,.xl-page-wrap{transition:transform 0.3s ease;}'
                                    '.ctrl-panel{position:fixed;right:15px;top:50%;'
                                    '  transform:translateY(-50%);z-index:9998;'
                                    '  display:flex;flex-direction:column;gap:6px;'
                                    '  padding:10px 8px;background:rgba(0,0,0,0.6);'
                                    '  border-radius:14px;backdrop-filter:blur(10px);'
                                    '  -webkit-backdrop-filter:blur(10px);}'
                                    '.ctrl-btn{width:40px;height:40px;border-radius:10px;'
                                    '  border:2px solid rgba(255,255,255,0.3);cursor:pointer;'
                                    '  display:flex;align-items:center;justify-content:center;'
                                    '  font-size:18px;color:#fff;transition:all 0.15s;'
                                    '  background:rgba(255,255,255,0.1);}'
                                    '.ctrl-btn:hover{background:rgba(255,255,255,0.25);'
                                    '  border-color:rgba(255,255,255,0.6);'
                                    '  transform:scale(1.1);}'
                                    '.ctrl-btn:active{transform:scale(0.95);}'
                                    '.ctrl-btn.accent{background:rgba(135,90,123,0.7);'
                                    '  border-color:rgba(135,90,123,0.9);}'
                                    '.ctrl-btn.accent:hover{background:rgba(135,90,123,0.9);}'
                                    '.ctrl-sep{height:1px;background:rgba(255,255,255,0.2);'
                                    '  margin:2px 4px;}'
                                    '.ctrl-label{font-size:9px;color:rgba(255,255,255,0.5);'
                                    '  text-align:center;letter-spacing:0.5px;'
                                    '  text-transform:uppercase;padding:2px 0;}'
                                    '#rotate-badge{position:fixed;bottom:15px;left:50%;'
                                    '  transform:translateX(-50%);z-index:9998;'
                                    '  background:rgba(0,0,0,0.7);color:#fff;'
                                    '  padding:6px 16px;border-radius:20px;font-size:12px;'
                                    '  font-weight:600;opacity:0;transition:opacity 0.3s;'
                                    '  pointer-events:none;backdrop-filter:blur(10px);}'
                                    '#rotate-badge.show{opacity:1;}'
                                    '</style></head>'
                                    '<body oncontextmenu="return false;" contenteditable="false">'

                # ── Shield div — now has reload button ──
                                    '<div id="blur-shield">'
                                    '  <div class="shield-icon">&#128274;</div>'
                                    '  <div class="shield-msg">Screenshot Blocked</div>'
                                    '  <div class="shield-sub">This document is protected. '
                                    '    Screenshots are not permitted.</div>'
                                    '  <button id="shield-reload-btn" onclick="dismissShield()">'
                                    '    &#8635;&nbsp; Reload Document'
                                    '  </button>'
                                    '</div>'

                                    '<div id="watermark"></div>'
                                    '<div style="background:#875A7B;padding:8px 24px;display:flex;'
                                    '  justify-content:space-between;align-items:center;'
                                    '  position:sticky;top:0;z-index:100;height:48px;">'
                                    '  <div style="display:flex;align-items:center;gap:14px;">'
                                    '    <div style="width:32px;height:32px;background:rgba(255,255,255,0.2);'
                                    '      border-radius:8px;display:flex;align-items:center;'
                                    '      justify-content:center;font-size:16px;color:#fff;">&#128196;</div>'
                                    '    <div>'
                                    '      <div style="font-size:14px;font-weight:600;color:#fff;">'
                + title + '</div>'
                          '      <div style="font-size:10px;color:rgba(255,255,255,0.7);">'
                + desc + ' | ' + ftype + '</div>'
                                         '    </div></div>'
                                         '  <div class="view-badge">'
                                         '    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
                                         '      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>'
                                         '      <circle cx="12" cy="12" r="3"/></svg>View Only</div></div>'
                                         '<div id="doc-content">' + preview + '</div>'
                                                                              '<div class="ctrl-panel">'
                                                                              '  <div class="ctrl-label">Scroll</div>'
                                                                              '  <button class="ctrl-btn" onclick="scrollDoc(\'up\')" title="Scroll Up">&#9650;</button>'
                                                                              '  <button class="ctrl-btn" onclick="scrollDoc(\'down\')" title="Scroll Down">&#9660;</button>'
                                                                              '  <button class="ctrl-btn" onclick="scrollDoc(\'left\')" title="Scroll Left">&#9664;</button>'
                                                                              '  <button class="ctrl-btn" onclick="scrollDoc(\'right\')" title="Scroll Right">&#9654;</button>'
                                                                              '  <div class="ctrl-sep"></div>'
                                                                              '  <div class="ctrl-label">Jump</div>'
                                                                              '  <button class="ctrl-btn" onclick="scrollDoc(\'top\')" title="Go to Top">&#8648;</button>'
                                                                              '  <button class="ctrl-btn" onclick="scrollDoc(\'bottom\')" title="Go to Bottom">&#8650;</button>'
                                                                              '  <div class="ctrl-sep"></div>'
                                                                              '  <div class="ctrl-label">Rotate</div>'
                                                                              '  <button class="ctrl-btn accent" onclick="rotateDoc(-90)" title="Rotate Left">&#8634;</button>'
                                                                              '  <button class="ctrl-btn accent" onclick="rotateDoc(90)" title="Rotate Right">&#8635;</button>'
                                                                              '  <button class="ctrl-btn" onclick="rotateDoc(0)" title="Reset Rotation"'
                                                                              '    style="font-size:12px;font-weight:700;">0&deg;</button>'
                                                                              '</div>'
                                                                              '<div id="rotate-badge">0&deg;</div>'

                                                                              '<script>'
                                                                              'var shield=document.getElementById("blur-shield");'

                # ── Shield helpers (persistent until button click) ──
                                                                              'function showShield(){'
                                                                              '  shield.classList.add("active");'
                                                                              '}'
                                                                              'function dismissShield(){'
                                                                              '  shield.classList.remove("active");'
                # Reload all iframes so content is fresh after shield is dismissed
                                                                              '  document.querySelectorAll("iframe").forEach(function(f){'
                                                                              '    var s=f.src;f.src="about:blank";'
                                                                              '    setTimeout(function(){f.src=s;},50);'
                                                                              '  });'
                                                                              '}'

                                                                              'function nukeClipboard(){'
                                                                              '  try{'
                                                                              '    if(navigator.clipboard&&navigator.clipboard.writeText)'
                                                                              '      navigator.clipboard.writeText("");'
                                                                              '  }catch(e){}'
                                                                              '}'

                                                                              'var docContent=document.getElementById("doc-content");'
                                                                              'function getScrollTarget(){'
                                                                              '  var candidates=docContent.querySelectorAll('
                                                                              '    "#pdf-container,.xl-container,.doc-panel");'
                                                                              '  for(var i=0;i<candidates.length;i++){'
                                                                              '    var el=candidates[i];'
                                                                              '    if(el.scrollHeight>el.clientHeight||'
                                                                              '       el.scrollWidth>el.clientWidth){'
                                                                              '      if(el.offsetParent!==null)return el;'
                                                                              '    }'
                                                                              '  }'
                                                                              '  if(docContent.scrollHeight>docContent.clientHeight||'
                                                                              '     docContent.scrollWidth>docContent.clientWidth)'
                                                                              '    return docContent;'
                                                                              '  return document.scrollingElement||document.documentElement;'
                                                                              '}'
                                                                              'function scrollDoc(dir){'
                                                                              '  var el=getScrollTarget();'
                                                                              '  var amt=300;'
                                                                              '  switch(dir){'
                                                                              '    case"up":el.scrollBy({top:-amt,behavior:"smooth"});break;'
                                                                              '    case"down":el.scrollBy({top:amt,behavior:"smooth"});break;'
                                                                              '    case"left":el.scrollBy({left:-amt,behavior:"smooth"});break;'
                                                                              '    case"right":el.scrollBy({left:amt,behavior:"smooth"});break;'
                                                                              '    case"top":el.scrollTo({top:0,behavior:"smooth"});break;'
                                                                              '    case"bottom":el.scrollTo({top:el.scrollHeight,behavior:"smooth"});break;'
                                                                              '  }'
                                                                              '}'
                                                                              'var currentRotation=0;'
                                                                              'var rotateBadge=document.getElementById("rotate-badge");'
                                                                              'var badgeTimer=null;'
                                                                              'function rotateDoc(deg){'
                                                                              '  if(deg===0){currentRotation=0;}'
                                                                              '  else{currentRotation=(currentRotation+deg)%360;'
                                                                              '    if(currentRotation<0)currentRotation+=360;}'
                                                                              '  var pages=docContent.querySelectorAll('
                                                                              '    ".pdf-page-wrap,.xl-page-wrap,img.doc-page");'
                                                                              '  pages.forEach(function(p){'
                                                                              '    p.style.transform="rotate("+currentRotation+"deg)";'
                                                                              '    p.style.transformOrigin="center center";'
                                                                              '  });'
                                                                              '  rotateBadge.textContent=currentRotation+"\\u00B0";'
                                                                              '  rotateBadge.classList.add("show");'
                                                                              '  if(badgeTimer)clearTimeout(badgeTimer);'
                                                                              '  badgeTimer=setTimeout(function(){'
                                                                              '    rotateBadge.classList.remove("show");},2000);'
                                                                              '}'

                # ── Key allowlist ──
                                                                              'function isAllowedKey(e){'
                                                                              '  var k=e.key;'
                                                                              '  return k==="Control"||k==="ArrowUp"||k==="ArrowDown"||'
                                                                              '         k==="ArrowLeft"||k==="ArrowRight";'
                                                                              '}'

                # ── keydown — PrintScreen handled first ──
                                                                              'document.addEventListener("keydown",function(e){'

                # Block PrintScreen — show persistent shield + nuke clipboard repeatedly
                # REPLACE WITH:
                                                                              '  if(e.key==="PrintScreen"||e.key==="Print Screen"||'
                                                                              '     e.code==="PrintScreen"||e.keyCode===44){'
                                                                              '    e.preventDefault();e.stopImmediatePropagation();'
                # Show shield NOW so the screen is covered if they press again
                                                                              '    showShield();'
                # Nuke starts immediately — keyup listener below does the real post-capture nuke
                                                                              '    nukeClipboard();'
                                                                              '    return false;'
                                                                              '  }'

                # Block the Fn key — most keyboards never surface a bare Fn
                # press to the browser (it's consumed by keyboard firmware
                # before the OS sees it), so this only fires on the rare
                # hardware/browser combos that do report it. Anything else
                # not in the allowlist still hits the catch-all below.
                                                                              '  if(e.key==="Fn"||e.code==="Fn"||e.keyCode===255||'
                                                                              '     (e.getModifierState&&(function(){try{return e.getModifierState("Fn");}catch(_){return false;}})())){'
                                                                              '    e.preventDefault();e.stopImmediatePropagation();'
                                                                              '    showShield();'
                                                                              '    nukeClipboard();'
                                                                              '    return false;'
                                                                              '  }'

                # Ctrl+ArrowUp/Down → zoom
                                                                              '  if(e.ctrlKey&&(e.key==="ArrowUp"||e.key==="ArrowDown")){'
                                                                              '    e.preventDefault();e.stopImmediatePropagation();'
                                                                              '    var zin=(e.key==="ArrowUp");'
                                                                              '    if(typeof window.pdfZoom==="function"){'
                                                                              '      window.pdfZoom(zin?0.2:-0.2);'
                                                                              '    }else if(typeof window.xlZoomDelta==="function"){'
                                                                              '      window.xlZoomDelta(zin?10:-10);'
                                                                              '    }'
                                                                              '    return false;'
                                                                              '  }'

                # All other non-whitelisted keys → persistent shield
                                                                              '  if(isAllowedKey(e))return;'
                                                                              '  e.preventDefault();e.stopImmediatePropagation();'
                                                                              '  showShield();'
                                                                              '  nukeClipboard();'
                                                                              '  setTimeout(nukeClipboard,100);'
                                                                              '  setTimeout(nukeClipboard,300);'
                                                                              '  setTimeout(nukeClipboard,500);'
                                                                              '  setTimeout(nukeClipboard,1000);'
                                                                              '  return false;'
                                                                              '},true);'

                                                                              'document.addEventListener("keyup",function(e){'
                # PrintScreen keyup fires AFTER the OS has written to clipboard — nuke it hard here
                                                                              '  if(e.key==="PrintScreen"||e.key==="Print Screen"||'
                                                                              '     e.code==="PrintScreen"||e.keyCode===44){'
                                                                              '    e.preventDefault();e.stopImmediatePropagation();'
                                                                              '    nukeClipboard();'  # immediate
                                                                              '    setTimeout(nukeClipboard,50);'  # 50 ms
                                                                              '    setTimeout(nukeClipboard,150);'  # 150 ms
                                                                              '    setTimeout(nukeClipboard,300);'  # 300 ms
                                                                              '    setTimeout(nukeClipboard,600);'  # 600 ms
                                                                              '    setTimeout(nukeClipboard,1000);'  # 1 s
                                                                              '    setTimeout(nukeClipboard,2000);'  # 2 s
                                                                              '    return false;'
                                                                              '  }'
                                                                              '  if(e.key==="Fn"||e.code==="Fn"||e.keyCode===255){'
                                                                              '    e.preventDefault();e.stopImmediatePropagation();'
                                                                              '    nukeClipboard();return false;'
                                                                              '  }'
                                                                              '  if(isAllowedKey(e))return;'
                                                                              '  e.preventDefault();e.stopImmediatePropagation();'
                                                                              '  nukeClipboard();return false;},true);'
                                                                              'document.addEventListener("keypress",function(e){'
                                                                              '  if(isAllowedKey(e))return;'
                                                                              '  e.preventDefault();e.stopImmediatePropagation();'
                                                                              '  return false;},true);'
                                                                              'document.addEventListener("dragstart",function(e){e.preventDefault();},true);'
                                                                              'document.addEventListener("copy",function(e){'
                                                                              '  e.preventDefault();if(e.clipboardData)e.clipboardData.setData("text/plain","");},true);'
                                                                              'document.addEventListener("paste",function(e){'
                                                                              '  e.preventDefault();e.stopImmediatePropagation();return false;},true);'
                                                                              'document.addEventListener("beforeinput",function(e){'
                                                                              '  if(e.inputType&&e.inputType.indexOf("insert")!==-1){'
                                                                              '    e.preventDefault();return false;}},true);'
                                                                              'document.addEventListener("selectstart",function(e){'
                                                                              '  e.preventDefault();return false;},true);'
                                                                              'document.addEventListener("contextmenu",function(e){'
                                                                              '  e.preventDefault();e.stopImmediatePropagation();return false;},true);'
                                                                              'document.addEventListener("mousedown",function(e){'
                                                                              '  if(e.button===2||e.button===1){'
                                                                              '    e.preventDefault();e.stopImmediatePropagation();return false;}},true);'
                                                                              'document.addEventListener("mouseup",function(e){'
                                                                              '  if(e.button===2||e.button===1){'
                                                                              '    e.preventDefault();e.stopImmediatePropagation();return false;}},true);'
                                                                              'document.addEventListener("auxclick",function(e){'
                                                                              '  e.preventDefault();e.stopImmediatePropagation();return false;},true);'

                # Block clipboard read API
                                                                              'try{if(navigator.clipboard){'
                                                                              '  Object.defineProperty(navigator.clipboard,"readText",{value:function(){'
                                                                              '    return Promise.reject("blocked");}});'
                                                                              '  Object.defineProperty(navigator.clipboard,"read",{value:function(){'
                                                                              '    return Promise.reject("blocked");}});'
                                                                              '}}catch(e){}'

                # Window blur → show persistent shield
                                                                              'window.addEventListener("blur",function(){'
                                                                              '  setTimeout(function(){'
                                                                              '    var ae=document.activeElement;'
                                                                              '    if(ae&&ae.tagName==="IFRAME")return;'
                                                                              '    showShield();nukeClipboard();'
                                                                              '  },100);'
                                                                              '});'
                                                                              'window.addEventListener("focus",function(){});'

                # Tab hidden → show persistent shield
                                                                              'document.addEventListener("visibilitychange",function(){'
                                                                              '  if(document.hidden){showShield();nukeClipboard();}});'

                # DevTools size heuristic
                                                                              'var dto=false;setInterval(function(){'
                                                                              '  var w=window.outerWidth-window.innerWidth>160;'
                                                                              '  var h=window.outerHeight-window.innerHeight>160;'
                                                                              '  if(w||h){if(!dto){dto=true;showShield();}}'
                                                                              '  else{dto=false;}},1000);'

                # Block getDisplayMedia (screen share / capture)
                                                                              'if(navigator.mediaDevices&&navigator.mediaDevices.getDisplayMedia){'
                                                                              '  var og=navigator.mediaDevices.getDisplayMedia;'
                                                                              '  navigator.mediaDevices.getDisplayMedia=function(){'
                                                                              '    showShield();return og.apply(this,arguments);};}'

                # Periodic clipboard image nuke
                                                                              'setInterval(function(){try{if(navigator.clipboard&&navigator.clipboard.read){'
                                                                              '  navigator.clipboard.read().then(function(items){'
                                                                              '    for(var i=0;i<items.length;i++)if(items[i].types)'
                                                                              '      for(var j=0;j<items[i].types.length;j++)'
                                                                              '        if(items[i].types[j].indexOf("image")!==-1)'
                                                                              '          {nukeClipboard();return;}'
                                                                              '  }).catch(function(){});}}catch(e){}},300);'

                # Watermark — traceable deterrent: if a screenshot does get
                # through (OS-level capture can't be blocked by a web page),
                # it's stamped with who viewed it and when.
                                                                              'var wm=document.getElementById("watermark");'
                                                                              'var un="' + user_name.replace('"', '\\"').replace("'", "\\'") + '";'
                                                                              'var now=new Date();'
                                                                              'var ts=now.toLocaleDateString()+" "+now.toLocaleTimeString();'
                                                                              'var wt=un+" | "+ts;var wh="";'
                                                                              'for(var i=0;i<300;i++)wh+="<span>"+wt+"</span> ";'
                                                                              'wm.innerHTML=wh;'
                          '</script></body></html>'
        )
