"""The browser engine: one headless Chrome per Odoo user, driven over the DevTools protocol.

Nothing here keeps state in memory that a request depends on: the Chrome processes are listed
in a small JSON file (port, pid, profile), so any Odoo worker can serve any request. Screens are
taken on demand (Page.captureScreenshot) and input is sent as DevTools events.

Network limits are enforced by Chrome itself with a PAC file: hosts that are not allowed go to a
dead proxy, for pages, links, scripts and images alike.
"""
import base64
import fcntl
import fnmatch
import hashlib
import json
import logging
import os
import re
import resource
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager

try:
    import websocket  # pip package "websocket-client"
except ImportError:  # the module still installs; opening the browser says what is missing
    websocket = None

from odoo.tools import config

_logger = logging.getLogger(__name__)

BASE = os.path.join(config['data_dir'], 'remote_browser')
REGISTRY = os.path.join(BASE, 'sessions.json')
CHROME_CANDIDATES = ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser']


# ---------------------------------------------------------------- registry of running browsers
@contextmanager
def registry():
    os.makedirs(BASE, exist_ok=True)
    with open(REGISTRY + '.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(REGISTRY) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        before = json.dumps(data, sort_keys=True)
        yield data
        if json.dumps(data, sort_keys=True) == before:
            return
        tmp = REGISTRY + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, REGISTRY)


def safe(key):
    return re.sub(r'[^A-Za-z0-9_]', '_', key)


def http_json(port, path, method='GET', timeout=5):
    req = urllib.request.Request('http://127.0.0.1:%s%s' % (port, path), method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    try:
        return json.loads(body) if body else None
    except ValueError:
        return body.decode(errors='replace')  # /json/activate and /json/close answer in plain text


def alive(entry):
    try:
        os.kill(entry['pid'], 0)
        http_json(entry['port'], '/json/version', timeout=2)
        return True
    except Exception:  # noqa: BLE001
        return False


def find_chrome(path=None):
    for name in ([path] if path else []) + CHROME_CANDIDATES:
        full = shutil.which(name) if name else None
        if full:
            return full
    return None


def pac_for(patterns):
    """A PAC script: allowed hosts go DIRECT, everything else to a dead proxy."""
    checks = ' || '.join('shExpMatch(host, %s)' % json.dumps(p.strip().lower()) for p in patterns if p.strip())
    js = 'function FindProxyForURL(url, host) { host = host.toLowerCase(); if (%s) return "DIRECT"; return "PROXY 127.0.0.1:9"; }' % (checks or 'false')
    return 'data:application/x-javascript-config;base64,' + base64.b64encode(js.encode()).decode()


def host_allowed(url, patterns, allow_all=False):
    if allow_all:
        return True
    host = (urllib.parse.urlsplit(url).hostname or '').lower()
    return any(fnmatch.fnmatch(host, p.strip().lower()) for p in patterns if p.strip())


def normalize_url(text):
    text = (text or '').strip()
    if not text or text in ('about:blank', 'newtab'):
        return 'about:blank'
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', text):
        text = 'http://' + text
    if urllib.parse.urlsplit(text).scheme not in ('http', 'https'):
        raise ValueError("Only http and https addresses can be opened.")
    return text


def _free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def downloads_dir(key):
    return os.path.join(BASE, 'downloads_' + safe(key))


def _lift_memory_limit():
    """Odoo caps its own address space (limit_memory_*); Chrome reserves far more and would crash."""
    hard = resource.getrlimit(resource.RLIMIT_AS)[1]
    resource.setrlimit(resource.RLIMIT_AS, (hard, hard))


def launch(key, chrome, patterns, allow_all, width=1280, height=800):
    """Start a fresh Chrome for this user; returns the registry entry."""
    if websocket is None:
        raise RuntimeError("The Python package websocket-client is missing on this server: "
                           "pip install websocket-client, then restart Odoo.")
    profile = os.path.join(BASE, 'profile_' + safe(key))
    shutil.rmtree(profile, ignore_errors=True)
    os.makedirs(os.path.join(profile, 'Default'), exist_ok=True)
    dl = downloads_dir(key)
    shutil.rmtree(dl, ignore_errors=True)
    os.makedirs(dl, exist_ok=True)
    with open(os.path.join(profile, 'Default', 'Preferences'), 'w') as f:
        json.dump({'download': {'default_directory': dl, 'prompt_for_download': False, 'directory_upgrade': True},
                   'profile': {'default_content_setting_values': {'notifications': 2, 'geolocation': 2}},
                   'savefile': {'default_directory': dl}}, f)
    args = [
        chrome, '--headless=new', '--remote-debugging-address=127.0.0.1',
        '--user-data-dir=' + profile, '--no-first-run', '--no-default-browser-check',
        '--disable-extensions', '--disable-sync', '--disable-background-networking', '--disable-component-update',
        '--disable-features=Translate,MediaRouter,OptimizationHints', '--password-store=basic',
        '--window-size=%d,%d' % (width, height), '--force-device-scale-factor=1',
        '--ignore-certificate-errors',  # internal servers mostly have self-signed certificates
        # headless Chrome paints ~12 frames/s by default; unlimited it reaches 100+ (we cap the stream instead)
        '--disable-frame-rate-limit', '--disable-gpu-vsync',
    ]
    if not allow_all:
        # loopback is normally never proxied: make the allow-list rule it too
        args += ['--proxy-bypass-list=<-loopback>', '--proxy-pac-url=' + pac_for(patterns)]
    for extra in ([], ['--no-sandbox']):
        port = _free_port()
        proc = subprocess.Popen(args + extra + ['--remote-debugging-port=%d' % port, 'about:blank'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                                preexec_fn=_lift_memory_limit)
        entry = {'pid': proc.pid, 'port': port, 'profile': profile, 'started': time.time(), 'last': time.time()}
        for _i in range(40):
            time.sleep(0.25)
            if proc.poll() is not None:
                break
            if alive(entry):
                if extra:
                    _logger.warning("Remote browser: Chrome runs without its sandbox on this server")
                return entry
        kill(entry)
    raise RuntimeError("Chrome did not start. Check the Chrome path in the Network Browser settings.")


def kill(entry):
    try:
        os.killpg(entry['pid'], signal.SIGTERM)
    except Exception:  # noqa: BLE001
        try:
            os.kill(entry['pid'], signal.SIGTERM)
        except Exception:  # noqa: BLE001
            pass
    _drop_conns(entry.get('port'))
    shutil.rmtree(entry.get('profile') or '/nonexistent', ignore_errors=True)


# ---------------------------------------------------------------- DevTools connections (cached per worker)
_conns = {}
_conns_lock = threading.Lock()


class CDP:
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=15, suppress_origin=True)
        self.lock = threading.Lock()
        self.seq = 0

    def call(self, method, /, _timeout=15, **params):
        with self.lock:
            self.seq += 1
            mid = self.seq
            self.ws.send(json.dumps({'id': mid, 'method': method, 'params': params}))
            end = time.time() + _timeout
            while time.time() < end:
                msg = json.loads(self.ws.recv())
                if msg.get('id') == mid:
                    if 'error' in msg:
                        raise RuntimeError(msg['error'].get('message', 'DevTools error'))
                    return msg.get('result') or {}
            raise TimeoutError(method)


def _drop_conns(port):
    if not port:
        return
    with _conns_lock:
        for url in [u for u in _conns if ':%s/' % port in u]:
            try:
                _conns.pop(url).ws.close()
            except Exception:  # noqa: BLE001
                pass


def cdp(url):
    with _conns_lock:
        conn = _conns.get(url)
        if conn is None:
            conn = _conns[url] = CDP(url)
        return conn


def call(url, method, /, **params):
    """One DevTools call; reconnects once when the cached socket died."""
    try:
        return cdp(url).call(method, **params)
    except (websocket.WebSocketException, OSError, BrokenPipeError):
        with _conns_lock:
            _conns.pop(url, None)
        return cdp(url).call(method, **params)


def page_ws(port, tid):
    return 'ws://127.0.0.1:%s/devtools/page/%s' % (port, tid)


def browser_ws(port):
    return http_json(port, '/json/version')['webSocketDebuggerUrl']


# ---------------------------------------------------------------- tabs and pages
def tabs(port):
    return [{'id': t['id'], 'title': t.get('title') or '', 'url': t.get('url') or ''}
            for t in http_json(port, '/json/list') if t.get('type') == 'page']


def new_tab(port, url='about:blank'):
    t = http_json(port, '/json/new?' + urllib.parse.quote(url, safe=':/?&=%#@+,;'), method='PUT')
    return {'id': t['id'], 'title': t.get('title') or '', 'url': t.get('url') or url}


def close_tab(port, tid):
    _drop_conns_url(page_ws(port, tid))
    http_json(port, '/json/close/' + tid)


def _drop_conns_url(url):
    with _conns_lock:
        conn = _conns.pop(url, None)
    if conn:
        try:
            conn.ws.close()
        except Exception:  # noqa: BLE001
            pass


def activate(port, tid):
    http_json(port, '/json/activate/' + tid)


def set_size(port, tid, width, height):
    """Make the page area exactly width x height (what the user sees, so clicks land right)."""
    if (port, tid) in _zoom:
        set_zoom(port, tid, _zoom[(port, tid)], width, height)
    width, height = int(width), int(height)
    bws = browser_ws(port)
    win = call(bws, 'Browser.getWindowForTarget', targetId=tid)['windowId']
    call(bws, 'Browser.setWindowBounds', windowId=win, bounds={'width': width, 'height': height})
    # headless Chrome keeps an invisible window frame inside those bounds: measure it and add it back
    inner = evaluate(port, tid, '[innerWidth, innerHeight]') or [width, height]
    if (port, tid) not in _zoom and (inner[0] != width or inner[1] != height):
        call(bws, 'Browser.setWindowBounds', windowId=win,
             bounds={'width': width + width - inner[0], 'height': height + height - inner[1]})


def screenshot(port, tid, quality=62):
    data = call(page_ws(port, tid), 'Page.captureScreenshot', format='jpeg', quality=quality,
                optimizeForSpeed=True)['data']
    return data, hashlib.md5(data.encode()).hexdigest()


def history(port, tid):
    h = call(page_ws(port, tid), 'Page.getNavigationHistory')
    return h['currentIndex'], h['entries']


def evaluate(port, tid, expression):
    res = call(page_ws(port, tid), 'Runtime.evaluate', expression=expression, returnByValue=True)
    return (res.get('result') or {}).get('value')


def navigate(port, tid, url):
    call(page_ws(port, tid), 'Page.navigate', url=url)


def go(port, tid, step):
    idx, entries = history(port, tid)
    target = idx + step
    if 0 <= target < len(entries):
        call(page_ws(port, tid), 'Page.navigateToHistoryEntry', entryId=entries[target]['id'])


def reload(port, tid):
    call(page_ws(port, tid), 'Page.reload', ignoreCache=False)


def stop(port, tid):
    call(page_ws(port, tid), 'Page.stopLoading')


# page zoom per tab, like Chrome's own zoom: a narrower layout drawn at a higher pixel ratio,
# so the picture keeps the same size and the page gets bigger (or smaller)
_zoom = {}


def set_zoom(port, tid, zoom, width, height):
    zoom = max(0.25, min(float(zoom), 3.0))
    url = page_ws(port, tid)
    if abs(zoom - 1) < 0.01:
        _zoom.pop((port, tid), None)
        call(url, 'Emulation.clearDeviceMetricsOverride')
    else:
        _zoom[(port, tid)] = zoom
        call(url, 'Emulation.setDeviceMetricsOverride', width=max(1, round(width / zoom)),
             height=max(1, round(height / zoom)), deviceScaleFactor=zoom, mobile=False)
    return zoom


def dispatch(port, tid, events):
    """Mouse and keyboard events, in order."""
    url = page_ws(port, tid)
    zoom = _zoom.get((port, tid), 1.0)
    for ev in events:
        kind = ev.get('kind')
        if kind == 'mouse':
            params = {k: ev[k] for k in ('type', 'x', 'y', 'button', 'buttons', 'clickCount', 'modifiers',
                                          'deltaX', 'deltaY') if k in ev}
            if zoom != 1.0:  # screen pixels -> page pixels
                params['x'] = params.get('x', 0) / zoom
                params['y'] = params.get('y', 0) / zoom
            call(url, 'Input.dispatchMouseEvent', **params)
        elif kind == 'key':
            base = {k: ev[k] for k in ('key', 'code', 'windowsVirtualKeyCode', 'modifiers', 'location') if k in ev}
            text = ev.get('text')
            if text:
                call(url, 'Input.dispatchKeyEvent', type='keyDown', text=text, unmodifiedText=text, **base)
            else:
                call(url, 'Input.dispatchKeyEvent', type='rawKeyDown', **base)
            call(url, 'Input.dispatchKeyEvent', type='keyUp', **base)
        elif kind == 'text' and ev.get('text'):
            call(url, 'Input.insertText', text=ev['text'])


# ---------------------------------------------------------------- live screen (screencast)
_casts = {}
_casts_lock = threading.Lock()


class Screencast:
    """Chrome pushes a JPEG whenever the page changes; we keep the newest one.

    Its own DevTools connection, read by a background thread. It stops by itself when nobody
    asked for a frame for a minute, or when the tab goes away.
    """

    def __init__(self, port, tid, width, height, quality):
        self.key = (port, tid)
        self.ws = websocket.create_connection(page_ws(port, tid), timeout=30, suppress_origin=True)
        self.cond = threading.Condition()
        self.frame = None
        self.seq = 0
        self.loading = False
        self.used = time.time()
        self.size = (width, height)
        self.quality = quality
        self.mid = 0
        self._send('Page.enable')
        self._start()
        threading.Thread(target=self._run, name='rb-cast', daemon=True).start()

    def _send(self, method, **params):
        self.mid += 1
        self.ws.send(json.dumps({'id': self.mid, 'method': method, 'params': params}))

    def _start(self):
        self._send('Page.startScreencast', format='jpeg', quality=self.quality,
                   maxWidth=int(self.size[0]), maxHeight=int(self.size[1]), everyNthFrame=1)

    def configure(self, width, height, quality):
        if (width, height) != self.size or quality != self.quality:
            self.size = (width, height)
            self.quality = quality
            self._send('Page.stopScreencast')
            self._start()

    def _run(self):
        try:
            while time.time() - self.used < 60:
                try:
                    msg = json.loads(self.ws.recv())
                except websocket.WebSocketTimeoutException:
                    continue
                method = msg.get('method')
                if method == 'Page.screencastFrame':
                    p = msg['params']
                    self._send('Page.screencastFrameAck', sessionId=p['sessionId'])
                    with self.cond:
                        self.frame = p['data']
                        self.seq += 1
                        self.cond.notify_all()
                elif method in ('Page.frameStartedLoading', 'Page.frameStoppedLoading'):
                    with self.cond:
                        self.loading = method == 'Page.frameStartedLoading'
                        self.seq += 1 if not self.loading else 0
                        self.cond.notify_all()
                elif method in ('Inspector.detached', 'Inspector.targetCrashed'):
                    break
        except Exception:  # noqa: BLE001
            pass
        finally:
            with _casts_lock:
                if _casts.get(self.key) is self:
                    _casts.pop(self.key, None)
            try:
                self.ws.close()
            except Exception:  # noqa: BLE001
                pass
            with self.cond:
                self.cond.notify_all()

    def wait(self, after, timeout):
        """The newest frame once it is newer than `after` (or when `timeout` runs out)."""
        self.used = time.time()
        with self.cond:
            if self.seq <= after:
                self.cond.wait(timeout)
            return self.seq, self.frame, self.loading


def screencast(port, tid, width, height, quality=70):
    width, height, quality = int(width), int(height), max(20, min(int(quality), 90))
    with _casts_lock:
        cast = _casts.get((port, tid))
        if cast is None:
            cast = _casts[(port, tid)] = Screencast(port, tid, width, height, quality)
    cast.configure(width, height, quality)
    return cast


# ---------------------------------------------------------------- live screen (parallel screenshots)
_caps = {}
_caps_lock = threading.Lock()
LOOPS = 3          # screenshot loops per shown tab: ~40 frames/s while the page moves
IDLE_GAP = 0.12    # when the page is still, one loop looks for changes this often


class Capturer:
    """The shown tab, captured by several screenshot loops on their own DevTools connections.

    Chrome's screencast tops out near 11 frames/s headless; screenshots taken in parallel reach
    about 40. Unchanged pictures are dropped, a capture that finishes after a newer one is ignored,
    and when the page is still only one loop keeps watching. It stops 30 s after its last viewer.
    """

    def __init__(self, port, tid):
        self.key = (port, tid)
        self.cond = threading.Condition()
        self.frame = None
        self.seq = 0
        self.digest = None
        self.order = 0      # order in which captures started
        self.taken = 0      # newest capture that was looked at
        self.quality = 80
        self.used = self.changed = self.burst = time.time()
        set_lifecycle(port, tid, 'active')
        conns = [websocket.create_connection(page_ws(port, tid), timeout=15, suppress_origin=True)
                 for _i in range(LOOPS)]
        self.alive = LOOPS
        for i, ws in enumerate(conns):
            threading.Thread(target=self._loop, args=(ws, i), name='rb-capture', daemon=True).start()

    def poke(self):
        """Input just arrived: capture at full speed right away."""
        self.burst = time.time()

    def _active(self):
        return time.time() - max(self.changed, self.burst) < 1.5

    def _loop(self, ws, idx):
        mid = 0
        try:
            while time.time() - self.used < 30:
                if not self._active():
                    time.sleep(IDLE_GAP if idx == 0 else 0.03)
                    if idx:
                        continue
                with self.cond:
                    self.order += 1
                    mine = self.order
                mid += 1
                ws.send(json.dumps({'id': mid, 'method': 'Page.captureScreenshot',
                                    'params': {'format': 'jpeg', 'quality': self.quality, 'optimizeForSpeed': True}}))
                while True:
                    msg = json.loads(ws.recv())
                    if msg.get('id') == mid:
                        break
                data = (msg.get('result') or {}).get('data')
                if not data:
                    time.sleep(0.1)
                    continue
                digest = hashlib.md5(data.encode()).digest()
                with self.cond:
                    if mine < self.taken:
                        continue  # an older capture that finished late
                    self.taken = mine
                    if digest == self.digest:
                        continue
                    self.digest, self.frame = digest, data
                    self.seq += 1
                    self.changed = time.time()
                    self.cond.notify_all()
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
            with self.cond:
                self.alive -= 1
                self.cond.notify_all()
            if self.alive <= 0:
                with _caps_lock:
                    if _caps.get(self.key) is self:
                        _caps.pop(self.key, None)
                # nobody watches any more: freeze the page so it stops painting (Chrome runs unthrottled)
                try:
                    set_lifecycle(*self.key, 'frozen')
                except Exception:  # noqa: BLE001
                    pass

    def wait(self, after, timeout):
        self.used = time.time()
        with self.cond:
            if self.seq <= after:
                self.cond.wait(timeout)
            return self.seq, self.frame


def set_lifecycle(port, tid, state):
    call(page_ws(port, tid), 'Page.setWebLifecycleState', state=state)


def freeze_others(port, tid):
    """Only the shown tab may paint; the others are frozen until shown again."""
    for t in tabs(port):
        if t['id'] != tid and (port, t['id']) not in _caps:
            try:
                set_lifecycle(port, t['id'], 'frozen')
            except Exception:  # noqa: BLE001
                pass


def capturer(port, tid, quality=80):
    with _caps_lock:
        cap = _caps.get((port, tid))
        if cap is None or cap.alive <= 0:
            cap = _caps[(port, tid)] = Capturer(port, tid)
    cap.quality = max(30, min(int(quality), 95))
    return cap


def poke(port, tid):
    cap = _caps.get((port, tid))
    if cap:
        cap.poke()


SELECTION_JS = """(() => {
  const a = document.activeElement;
  if (a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA') && a.selectionStart !== a.selectionEnd)
    return a.value.substring(a.selectionStart, a.selectionEnd);
  return String(window.getSelection() || '');
})()"""

STATE_JS = "[document.readyState, location.href, document.title]"
