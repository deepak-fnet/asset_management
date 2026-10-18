import base64
import logging
import os
import time
import urllib.parse

from odoo import _, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request, content_disposition
from werkzeug.wrappers import Response

from ..models import engine

_logger = logging.getLogger(__name__)

# last address logged per (user, tab) in this worker, so a page is logged once
_logged = {}


class RemoteBrowser(http.Controller):

    def _check(self):
        if not request.env.user.has_group('remote_browser.group_rb_user'):
            raise AccessError(_("You are not allowed to use the Network Browser."))
        return request.env['rb.engine']

    def _entry(self, start=False, width=1280, height=800):
        entry = self._check()._session(start=start, width=width, height=height)
        if not entry:
            raise UserError(_("The browser was closed. Open it again."))
        return entry

    def _log(self, tid, url, title):
        if not url or url.startswith(('about:', 'chrome', 'data:')):
            return
        key = (request.env.cr.dbname, request.env.uid, tid)
        if _logged.get(key) == url:
            return
        _logged[key] = url
        request.env['rb.visit'].sudo().create({
            'user_id': request.env.uid, 'url': url[:2000], 'title': (title or '')[:300],
            'host': urllib.parse.urlsplit(url).hostname or '',
        })

    # ---------------------------------------------------------------- session
    @http.route('/remote_browser/open', type='jsonrpc', auth='user')
    def open(self, width=1280, height=800):
        env = self._check()
        entry = env._session(start=True, width=width, height=height)
        port = entry['port']
        tabs = engine.tabs(port)
        if not tabs:
            tabs = [engine.new_tab(port)]
        engine.set_size(port, tabs[0]['id'], width, height)
        s = env._settings()
        marks = request.env['rb.bookmark'].search(['|', ('user_id', '=', False), ('user_id', '=', request.env.uid)])
        return {
            'tabs': tabs,
            'server': os.uname().nodename,
            'allow_all': s['allow_all'],
            'patterns': s['patterns'],
            'bookmarks': [{'id': b.id, 'name': b.name, 'url': b.url, 'color': b.color or '#3b6fd8', 'mine': bool(b.user_id)}
                          for b in marks],
        }

    @http.route('/remote_browser/close', type='jsonrpc', auth='user')
    def close(self):
        self._check()._close()
        return True

    # ---------------------------------------------------------------- screen
    @http.route('/remote_browser/frame.jpg', type='http', auth='user', methods=['GET'])
    def frame_jpg(self, tid, last=0, w=1280, h=800, q=70, wait=1.0):
        """Long-poll for the next screen: raw JPEG bytes, or 204 when nothing changed in `wait` seconds.

        w/h may be smaller than the page (the client scales the picture up) to save bandwidth.
        """
        entry = self._entry()
        try:
            cast = engine.screencast(entry['port'], tid, w, h, q)
            seq, data, loading = cast.wait(int(last or 0), min(float(wait or 1), 2.0))
        except Exception as e:  # noqa: BLE001
            return request.make_response(str(e), [('Content-Type', 'text/plain'), ('Cache-Control', 'no-store')],
                                         status=409)
        headers = [('Cache-Control', 'no-store'), ('X-Seq', str(seq)), ('X-Loading', '1' if loading else '0')]
        if seq == int(last or 0) or not data:
            return request.make_response(b'', headers, status=204)
        return request.make_response(base64.b64decode(data), headers + [('Content-Type', 'image/jpeg')])

    # JPEG quality while the page moves; the stream steps between them with the network speed
    LEVELS = [85, 80, 72, 64, 55, 45]

    @http.route('/remote_browser/stream', type='http', auth='user', methods=['GET'])
    def stream(self, tid, w=None, h=None, mode='auto'):
        """One long response that pushes every new frame as it is captured.

        Each frame: 4-byte length + JPEG (length 0 = keep-alive). The server times how long each
        frame takes to leave and lowers the quality on a slow network, raises it on a quick one.
        When the page stops moving it sends one sharp picture (quality 92).
        """
        port = self._entry()['port']
        levels = self.LEVELS
        lo, hi = {'sharp': (0, 1), 'fast': (3, 5)}.get(mode, (0, len(levels) - 1))
        start = {'sharp': 0, 'fast': 4}.get(mode, 1)

        def pack(jpeg):
            return len(jpeg).to_bytes(4, 'big') + jpeg

        engine.freeze_others(port, tid)

        def frames():
            level, quick, sharp_sent, beat = start, 0, True, time.time()
            cap = engine.capturer(port, tid, levels[level])
            last, end, sent_at = 0, time.time() + 55, 0
            while time.time() < end:
                seq, data = cap.wait(last, 0.3)
                if seq != last and data:
                    last, sharp_sent = seq, False
                    gap = 1 / 45 - (time.time() - sent_at)  # at most 45 frames a second
                    if gap > 0:
                        time.sleep(gap)
                        seq, data = cap.wait(last - 1, 0)  # the newest frame after the pause
                        last = seq
                    sent_at = t0 = time.time()
                    yield pack(base64.b64decode(data))
                    spent = time.time() - t0  # the previous frame had to leave before we got here
                    if spent > 0.12 and level < hi:
                        level, quick = level + 1, 0
                    elif spent < 0.03 and level > lo:
                        quick += 1
                        if quick > 20:
                            level, quick = level - 1, 0
                    cap = engine.capturer(port, tid, levels[level])
                elif not sharp_sent and seq == last:
                    # the page is still: one crisp picture
                    try:
                        data, _digest = engine.screenshot(port, tid, 92)
                        yield pack(base64.b64decode(data))
                    except Exception:  # noqa: BLE001
                        pass
                    sharp_sent, beat = True, time.time()
                elif time.time() - beat > 10:
                    beat = time.time()
                    yield pack(b'')

        headers = {'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no', 'Content-Type': 'application/octet-stream'}
        return Response(frames(), status=200, headers=headers, direct_passthrough=True)

    @http.route('/remote_browser/state', type='jsonrpc', auth='user')
    def state(self, tid):
        """Tabs, address, title and back/forward of the shown tab (asked a few times a second)."""
        port = self._entry()['port']
        tabs = engine.tabs(port)
        me = next((t for t in tabs if t['id'] == tid), {})
        try:
            idx, entries = engine.history(port, tid)
        except Exception:  # noqa: BLE001
            idx, entries = 0, [None]
        self._log(tid, me.get('url'), me.get('title'))
        try:
            loading = engine.evaluate(port, tid, 'document.readyState') != 'complete'
        except Exception:  # noqa: BLE001
            loading = False
        return {'tabs': tabs, 'url': me.get('url', ''), 'title': me.get('title', ''), 'missing': not me,
                'loading': loading,
                'can_back': idx > 0, 'can_forward': idx < len(entries) - 1}

    @http.route('/remote_browser/resize', type='jsonrpc', auth='user')
    def resize(self, tid, width, height):
        engine.set_size(self._entry()['port'], tid, width, height)
        return True

    @http.route('/remote_browser/zoom', type='jsonrpc', auth='user')
    def zoom(self, tid, zoom, width, height):
        return engine.set_zoom(self._entry()['port'], tid, zoom, width, height)

    # ---------------------------------------------------------------- input
    @http.route('/remote_browser/input', type='jsonrpc', auth='user')
    def input(self, tid, events):
        port = self._entry()['port']
        engine.poke(port, tid)
        engine.dispatch(port, tid, events or [])
        return True

    @http.route('/remote_browser/copy', type='jsonrpc', auth='user')
    def copy(self, tid):
        return engine.evaluate(self._entry()['port'], tid, engine.SELECTION_JS) or ''

    # ---------------------------------------------------------------- navigation
    @http.route('/remote_browser/navigate', type='jsonrpc', auth='user')
    def navigate(self, tid, url):
        env = self._check()
        try:
            url = engine.normalize_url(url)
        except ValueError as e:
            return {'error': str(e)}
        s = env._settings()
        if url != 'about:blank' and not engine.host_allowed(url, s['patterns'], s['allow_all']):
            return {'error': _("%s is not on the allowed list. Ask an administrator to add it.",
                               urllib.parse.urlsplit(url).hostname)}
        engine.navigate(self._entry()['port'], tid, url)
        return {'url': url}

    @http.route('/remote_browser/nav', type='jsonrpc', auth='user')
    def nav(self, tid, action):
        port = self._entry()['port']
        if action == 'back':
            engine.go(port, tid, -1)
        elif action == 'forward':
            engine.go(port, tid, 1)
        elif action == 'reload':
            engine.reload(port, tid)
        elif action == 'stop':
            engine.stop(port, tid)
        return True

    # ---------------------------------------------------------------- tabs
    @http.route('/remote_browser/tab', type='jsonrpc', auth='user')
    def tab(self, action, tid=None, url=None, width=None, height=None):
        port = self._entry()['port']
        res = {}
        if action == 'new':
            res['tab'] = engine.new_tab(port, 'about:blank')
        elif action == 'duplicate':
            current = engine.evaluate(port, tid, 'location.href') or 'about:blank'
            res['tab'] = engine.new_tab(port, current)
        elif action == 'close':
            engine.close_tab(port, tid)
            if not engine.tabs(port):
                res['tab'] = engine.new_tab(port)
        elif action == 'activate':
            engine.activate(port, tid)
        target = (res.get('tab') or {}).get('id') or (tid if action == 'activate' else None)
        if target:
            engine.activate(port, target)
            if width and height:
                engine.set_size(port, target, width, height)
        res['tabs'] = engine.tabs(port)
        return res

    # ---------------------------------------------------------------- bookmarks
    @http.route('/remote_browser/bookmark', type='jsonrpc', auth='user')
    def bookmark(self, url, name=None, remove_id=None):
        self._check()
        Mark = request.env['rb.bookmark']
        if remove_id:
            mark = Mark.browse(int(remove_id)).exists()
            if mark and mark.user_id == request.env.user:
                mark.unlink()
        else:
            Mark.create({'name': (name or url)[:80], 'url': url, 'user_id': request.env.uid})
        marks = Mark.search(['|', ('user_id', '=', False), ('user_id', '=', request.env.uid)])
        return [{'id': b.id, 'name': b.name, 'url': b.url, 'color': b.color or '#3b6fd8', 'mine': bool(b.user_id)}
                for b in marks]

    # ---------------------------------------------------------------- downloads
    @http.route('/remote_browser/downloads', type='jsonrpc', auth='user')
    def downloads(self):
        self._check()
        folder = engine.downloads_dir(request.env['rb.engine']._key())
        out = []
        if os.path.isdir(folder):
            for name in sorted(os.listdir(folder), key=lambda n: -os.path.getmtime(os.path.join(folder, n))):
                full = os.path.join(folder, name)
                out.append({'name': name, 'size': os.path.getsize(full),
                            'done': not name.endswith('.crdownload')})
        return out

    @http.route('/remote_browser/download', type='http', auth='user')
    def download(self, name):
        self._check()
        folder = engine.downloads_dir(request.env['rb.engine']._key())
        name = os.path.basename(name)
        full = os.path.join(folder, name)
        if not name or not os.path.isfile(full):
            return request.not_found()
        with open(full, 'rb') as f:
            data = f.read()
        return request.make_response(data, [('Content-Type', 'application/octet-stream'),
                                            ('Content-Disposition', content_disposition(name))])
