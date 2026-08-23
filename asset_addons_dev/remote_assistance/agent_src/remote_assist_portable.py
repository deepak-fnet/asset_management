#!/usr/bin/env python3
"""
Remote Assistance — Temporary Agent (customer side)
===================================================

A customer downloads and runs this ONCE, for a single support session. It:

  * reads the session parameters (token / relay / Odoo URL / duration) from a
    `session.json` sitting next to it, or from --flags / environment variables;
  * authenticates the session token with Odoo and reports its status;
  * connects to the relay as the *host*, streams the screen, and injects the
    administrator's mouse / keyboard;
  * shows a small always-on-top floating window: "Connected to <company>",
    a session timer, Stop Sharing, and Minimize;
  * stops automatically when the agreed duration elapses, when the customer
    clicks Stop Sharing, or when the administrator ends the session.

Capture / control backends (auto-selected)
-------------------------------------------
  * Windows and Linux/X11  ->  mss (capture) + pynput (input)   [tested]
  * Linux/Wayland          ->  xdg-desktop-portal RemoteDesktop
                               + PipeWire/GStreamer (capture + input)
                               Shows the system "share your screen" dialog.
                               Needs: python3-gi, gstreamer1.0-pipewire,
                               gir1.2-gst-plugins-base-1.0.

It installs nothing permanent and exits when its window closes.
"""

import argparse
import io
import json
import logging
import os
import platform
import sys
import threading
import time

logger = logging.getLogger("remote_assist_agent")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [remote_assist] %(levelname)s %(message)s")

# ---- hard dependencies (needed on every platform) -------------------------
_CORE_OK = True
_CORE_ERR = None
try:
    import asyncio
    from PIL import Image
    import websockets
    import requests
except Exception as e:  # noqa: BLE001
    _CORE_OK = False
    _CORE_ERR = e

# ---- optional: mss + pynput (X11 / Windows backend) -----------------------
_MSS_OK = True
try:
    import mss  # noqa: F401
    from pynput import mouse as _pynput_mouse
    from pynput import keyboard as _pynput_keyboard
except Exception:  # noqa: BLE001
    _MSS_OK = False

# ---- optional: tkinter (floating window) ----------------------------------
try:
    import tkinter as tk
    _TK_OK = True
except Exception:
    _TK_OK = False

DEFAULT_FPS = 8
DEFAULT_JPEG_QUALITY = 55
DEFAULT_MAX_WIDTH = 1600


def _session_is_wayland():
    if platform.system() != "Linux":
        return False
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return True
    # Fallback heuristic: Wayland socket present but no X display.
    return bool(os.environ.get("WAYLAND_DISPLAY")) and not os.environ.get("DISPLAY")


# ===========================================================================
# Input injection for the mss/pynput backend
# ===========================================================================
class InputInjector:
    """Turns viewer JSON events into real OS input via pynput (X11/Windows)."""

    def __init__(self, monitor):
        self.mon = monitor
        self.mouse = _pynput_mouse.Controller()
        self.keyboard = _pynput_keyboard.Controller()
        self._buttons = {
            "left": _pynput_mouse.Button.left,
            "middle": _pynput_mouse.Button.middle,
            "right": _pynput_mouse.Button.right,
        }

    def _abs(self, nx, ny):
        x = self.mon["left"] + int(max(0.0, min(1.0, nx)) * self.mon["width"])
        y = self.mon["top"] + int(max(0.0, min(1.0, ny)) * self.mon["height"])
        return x, y

    def _key(self, name):
        if name is None:
            return None
        if len(name) == 1:
            return name
        special = {
            "Enter": _pynput_keyboard.Key.enter,
            "Backspace": _pynput_keyboard.Key.backspace,
            "Tab": _pynput_keyboard.Key.tab,
            "Escape": _pynput_keyboard.Key.esc,
            "Shift": _pynput_keyboard.Key.shift,
            "Control": _pynput_keyboard.Key.ctrl,
            "Alt": _pynput_keyboard.Key.alt,
            "Meta": _pynput_keyboard.Key.cmd,
            "ArrowUp": _pynput_keyboard.Key.up,
            "ArrowDown": _pynput_keyboard.Key.down,
            "ArrowLeft": _pynput_keyboard.Key.left,
            "ArrowRight": _pynput_keyboard.Key.right,
            "Delete": _pynput_keyboard.Key.delete,
            "Home": _pynput_keyboard.Key.home,
            "End": _pynput_keyboard.Key.end,
            "PageUp": _pynput_keyboard.Key.page_up,
            "PageDown": _pynput_keyboard.Key.page_down,
            "CapsLock": _pynput_keyboard.Key.caps_lock,
            " ": _pynput_keyboard.Key.space,
            "Spacebar": _pynput_keyboard.Key.space,
        }
        for i in range(1, 13):
            special[f"F{i}"] = getattr(_pynput_keyboard.Key, f"f{i}")
        return special.get(name, None)

    def handle(self, evt):
        try:
            t = evt.get("type")
            if t == "mouse_move":
                self.mouse.position = self._abs(evt["x"], evt["y"])
            elif t == "mouse_down":
                self.mouse.position = self._abs(evt["x"], evt["y"])
                self.mouse.press(self._buttons.get(evt.get("button", "left"),
                                                   _pynput_mouse.Button.left))
            elif t == "mouse_up":
                self.mouse.position = self._abs(evt["x"], evt["y"])
                self.mouse.release(self._buttons.get(evt.get("button", "left"),
                                                     _pynput_mouse.Button.left))
            elif t == "scroll":
                self.mouse.scroll(0, int(evt.get("dy", 0)))
            elif t in ("key_down", "key_up"):
                k = self._key(evt.get("key"))
                if k is None:
                    return
                if t == "key_down":
                    self.keyboard.press(k)
                else:
                    self.keyboard.release(k)
        except Exception as e:  # noqa: BLE001
            logger.debug("input injection error: %s", e)


# ===========================================================================
# Backend interface:  start() -> (w, h) ; grab_jpeg() -> bytes|None ;
#                     inject(evt) ; stop()
# ===========================================================================
class MssPynputBackend:
    """Tested backend for Windows and Linux/X11."""
    name = "mss+pynput"

    def __init__(self, quality, max_width):
        self.quality = quality
        self.max_width = max_width
        self._sct = None
        self._mon = None
        self._inj = None

    def start(self):
        if not _MSS_OK:
            raise RuntimeError("mss/pynput not available. Install: "
                               "pip install mss pynput")
        self._sct = mss.mss()
        self._mon = self._sct.monitors[1]
        self._inj = InputInjector(self._mon)
        return self._mon["width"], self._mon["height"]

    def grab_jpeg(self):
        shot = self._sct.grab(self._mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        if img.width > self.max_width:
            ratio = self.max_width / img.width
            img = img.resize((self.max_width, int(img.height * ratio)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.quality)
        return buf.getvalue()

    def inject(self, evt):
        if self._inj:
            self._inj.handle(evt)

    def stop(self):
        try:
            if self._sct:
                self._sct.close()
        except Exception:
            pass


# X keysyms for the Wayland backend's keyboard injection.
_XKEYSYM = {
    "Enter": 0xFF0D, "Backspace": 0xFF08, "Tab": 0xFF09, "Escape": 0xFF1B,
    "Shift": 0xFFE1, "Control": 0xFFE3, "Alt": 0xFFE9, "Meta": 0xFFEB,
    "ArrowUp": 0xFF52, "ArrowDown": 0xFF54, "ArrowLeft": 0xFF51,
    "ArrowRight": 0xFF53, "Delete": 0xFFFF, "Home": 0xFF50, "End": 0xFF57,
    "PageUp": 0xFF55, "PageDown": 0xFF56, "CapsLock": 0xFFE5,
    " ": 0x0020, "Spacebar": 0x0020,
}
for _i in range(1, 13):
    _XKEYSYM[f"F{_i}"] = 0xFFBD + _i  # F1 == 0xFFBE

# evdev button codes.
_BTN = {"left": 0x110, "middle": 0x112, "right": 0x111}


class PortalBackend:
    """Wayland backend using xdg-desktop-portal RemoteDesktop + PipeWire.

    EXPERIMENTAL: this shows the system screen-share dialog and needs the
    GStreamer/PipeWire stack present. Validate on real Wayland hardware.
    """
    name = "wayland-portal"
    DEVICE_KEYBOARD = 1
    DEVICE_POINTER = 2
    SOURCE_MONITOR = 1

    def __init__(self, quality, max_width):
        self.quality = quality
        self.max_width = max_width
        self._lock = threading.Lock()
        self._frame = None            # (w, h, rgb_bytes)
        self._ready = threading.Event()
        self._failed = None
        self._w = 0
        self._h = 0
        self._thread = None
        self._loop = None
        self._conn = None             # Gio.DBusConnection
        self._session = None          # session object path
        self._node_id = None          # PipeWire node id
        self._pipeline = None
        self._Gst = None
        self._GLib = None
        self._Gio = None

    # -- public interface --------------------------------------------------
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(90):
            raise RuntimeError("Wayland screen-share was not approved in time "
                               "(no response to the portal dialog).")
        if self._failed:
            raise RuntimeError(self._failed)
        return self._w, self._h

    def grab_jpeg(self):
        with self._lock:
            fr = self._frame
        if not fr:
            return None
        w, h, data = fr
        expected = w * h * 3
        if len(data) != expected and h:
            stride = len(data) // h
            data = b"".join(data[i * stride:i * stride + w * 3]
                            for i in range(h))
        try:
            img = Image.frombytes("RGB", (w, h), data)
        except Exception:
            return None
        if img.width > self.max_width:
            ratio = self.max_width / img.width
            img = img.resize((self.max_width, int(img.height * ratio)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.quality)
        return buf.getvalue()

    def inject(self, evt):
        if not self._loop or not self._session:
            return
        self._GLib.idle_add(self._do_inject, evt)

    def stop(self):
        try:
            if self._pipeline:
                self._pipeline.set_state(self._Gst.State.NULL)
        except Exception:
            pass
        try:
            if self._loop:
                self._loop.quit()
        except Exception:
            pass

    # -- internals (run in the GLib thread) --------------------------------
    def _do_inject(self, evt):
        try:
            t = evt.get("type")
            if t == "mouse_move":
                x = max(0.0, min(1.0, evt.get("x", 0))) * self._w
                y = max(0.0, min(1.0, evt.get("y", 0))) * self._h
                self._rd_call("NotifyPointerMotionAbsolute",
                              self._GLib.Variant("(oa{sv}udd)",
                              (self._session, {}, self._node_id, x, y)))
            elif t in ("mouse_down", "mouse_up"):
                btn = _BTN.get(evt.get("button", "left"), 0x110)
                state = 1 if t == "mouse_down" else 0
                self._rd_call("NotifyPointerButton",
                              self._GLib.Variant("(oa{sv}iu)",
                              (self._session, {}, btn, state)))
            elif t == "scroll":
                steps = int(evt.get("dy", 0))
                if steps:
                    self._rd_call("NotifyPointerAxisDiscrete",
                                  self._GLib.Variant("(oa{sv}ui)",
                                  (self._session, {}, 0, -steps)))
            elif t in ("key_down", "key_up"):
                keysym = self._keysym(evt.get("key"))
                if keysym is not None:
                    state = 1 if t == "key_down" else 0
                    self._rd_call("NotifyKeyboardKeysym",
                                  self._GLib.Variant("(oa{sv}iu)",
                                  (self._session, {}, keysym, state)))
        except Exception as e:  # noqa: BLE001
            logger.debug("portal inject error: %s", e)
        return False  # don't repeat the idle callback

    def _keysym(self, name):
        if not name:
            return None
        if len(name) == 1:
            return ord(name)
        return _XKEYSYM.get(name)

    def _rd_call(self, method, params):
        self._conn.call_sync(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.RemoteDesktop",
            method, params, None,
            self._Gio.DBusCallFlags.NONE, -1, None)

    def _portal_request(self, interface, method, args_before_options, options):
        """Call a portal method that uses the Request/Response pattern and
        block (via a nested main context) until the Response arrives."""
        token = "ra%d" % (int(time.time() * 1000) % 1000000)
        options = dict(options)
        options["handle_token"] = self._GLib.Variant("s", token)
        sender = self._conn.get_unique_name()[1:].replace(".", "_")
        request_path = ("/org/freedesktop/portal/desktop/request/%s/%s"
                        % (sender, token))
        result = {}
        done = {"v": False}

        def on_response(conn, s, obj, iface, sig, params):
            code, results = params.unpack()
            result["code"] = code
            result["results"] = results
            done["v"] = True

        sub = self._conn.signal_subscribe(
            "org.freedesktop.portal.Desktop",
            "org.freedesktop.portal.Request", "Response", request_path,
            None, self._Gio.DBusSignalFlags.NONE, on_response)
        try:
            # Build the (args..., options) tuple variant.
            variant = self._build_call_variant(args_before_options, options)
            self._conn.call_sync(
                "org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop", interface, method,
                variant, None, self._Gio.DBusCallFlags.NONE, -1, None)
            ctx = self._GLib.MainContext.default()
            waited = 0.0
            while not done["v"] and waited < 90.0:
                ctx.iteration(False)
                time.sleep(0.02)
                waited += 0.02
        finally:
            self._conn.signal_unsubscribe(sub)
        if not done.get("v"):
            raise RuntimeError("portal %s timed out" % method)
        if result.get("code") != 0:
            raise RuntimeError("portal %s was cancelled/denied" % method)
        return result.get("results", {})

    def _build_call_variant(self, args_before_options, options):
        GLib = self._GLib
        # Compose a tuple variant of the leading args plus an a{sv} options.
        children = list(args_before_options)
        children.append(GLib.Variant("a{sv}", options))
        return GLib.Variant.new_tuple(*children)

    def _run(self):
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst, GLib, Gio
            self._Gst, self._GLib, self._Gio = Gst, GLib, Gio
            Gst.init(None)
            self._loop = GLib.MainLoop()
            self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)

            # 1) RemoteDesktop.CreateSession
            st = "ra_s%d" % (int(time.time() * 1000) % 1000000)
            res = self._portal_request(
                "org.freedesktop.portal.RemoteDesktop", "CreateSession", [],
                {"session_handle_token": GLib.Variant("s", st)})
            self._session = res["session_handle"]

            # 2) SelectDevices (keyboard + pointer)
            self._portal_request(
                "org.freedesktop.portal.RemoteDesktop", "SelectDevices",
                [GLib.Variant("o", self._session)],
                {"types": GLib.Variant("u", self.DEVICE_KEYBOARD |
                                       self.DEVICE_POINTER)})

            # 3) ScreenCast.SelectSources (a monitor, embedded cursor)
            self._portal_request(
                "org.freedesktop.portal.ScreenCast", "SelectSources",
                [GLib.Variant("o", self._session)],
                {"types": GLib.Variant("u", self.SOURCE_MONITOR),
                 "multiple": GLib.Variant("b", False),
                 "cursor_mode": GLib.Variant("u", 2)})  # 2 = embedded

            # 4) Start -> user approves; returns the stream(s)
            res = self._portal_request(
                "org.freedesktop.portal.RemoteDesktop", "Start",
                [GLib.Variant("o", self._session), GLib.Variant("s", "")], {})
            streams = res.get("streams") or []
            if not streams:
                raise RuntimeError("portal returned no screen stream")
            self._node_id = streams[0][0]
            props = streams[0][1] if len(streams[0]) > 1 else {}
            size = props.get("size")
            if size:
                self._w, self._h = int(size[0]), int(size[1])

            # 5) OpenPipeWireRemote -> fd
            fd_list = None
            variant = GLib.Variant("(oa{sv})", (self._session, {}))
            reply, fd_list = self._conn.call_with_unix_fd_list_sync(
                "org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop",
                "org.freedesktop.portal.ScreenCast", "OpenPipeWireRemote",
                variant, GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, -1,
                None, None)
            fd_index = reply.unpack()[0]
            fd = fd_list.get(fd_index)

            # 6) GStreamer pipeline: pipewiresrc -> RGB -> appsink
            desc = ("pipewiresrc fd=%d path=%s do-timestamp=true ! "
                    "videoconvert ! video/x-raw,format=RGB ! "
                    "appsink name=sink emit-signals=true max-buffers=2 drop=true"
                    % (fd, self._node_id))
            self._pipeline = Gst.parse_launch(desc)
            sink = self._pipeline.get_by_name("sink")
            sink.connect("new-sample", self._on_sample)
            self._pipeline.set_state(Gst.State.PLAYING)

            if not self._w or not self._h:
                # Will be corrected from the first sample's caps.
                self._w, self._h = 1920, 1080
            self._ready.set()
            self._loop.run()
        except Exception as e:  # noqa: BLE001
            self._failed = ("Wayland capture failed: %s. Ensure "
                            "gstreamer1.0-pipewire and python3-gi are "
                            "installed, or log in with 'Ubuntu on Xorg'." % e)
            logger.warning(self._failed)
            self._ready.set()

    def _on_sample(self, sink):
        try:
            sample = sink.emit("pull-sample")
            if not sample:
                return self._Gst.FlowReturn.OK
            buf = sample.get_buffer()
            caps = sample.get_caps()
            s = caps.get_structure(0)
            w = s.get_value("width")
            h = s.get_value("height")
            ok, minfo = buf.map(self._Gst.MapFlags.READ)
            if ok:
                data = bytes(minfo.data)
                buf.unmap(minfo)
                with self._lock:
                    self._frame = (w, h, data)
                    self._w, self._h = w, h
        except Exception as e:  # noqa: BLE001
            logger.debug("sample error: %s", e)
        return self._Gst.FlowReturn.OK


def select_backend(quality, max_width):
    """Pick the capture/input backend for this machine."""
    if _session_is_wayland():
        logger.info("Wayland session detected — using the screen-share portal")
        return PortalBackend(quality, max_width)
    logger.info("using mss/pynput capture backend")
    return MssPynputBackend(quality, max_width)


# ===========================================================================
# Floating control window (customer): Stop Sharing + Minimize + timer
# ===========================================================================
class FloatingWindow:
    def __init__(self, company_name, minutes, on_stop, on_pause):
        self.company = company_name or "the administrator"
        self.minutes = minutes or 0
        self.on_stop = on_stop
        self.on_pause = on_pause
        self.root = None
        self._start_ts = time.time()
        self._paused = False
        self._minimized = False
        self._drag = (0, 0)

    def run(self):
        if not _TK_OK:
            logger.info("tkinter unavailable — running without floating window")
            return
        try:
            self.root = tk.Tk()
        except Exception as e:  # noqa: BLE001
            logger.info("no display for floating window (%s) — head-less", e)
            self.root = None
            return
        r = self.root
        r.title("Remote Assistance")
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.configure(bg="#101826")
        try:
            r.attributes("-alpha", 0.96)
        except Exception:
            pass
        r.geometry("+40+40")

        self.bar = tk.Frame(r, bg="#101826")
        self.bar.pack(fill="both", expand=True, padx=10, pady=8)

        title = tk.Label(self.bar, text="\u25cf  Connected to", fg="#4ade80",
                         bg="#101826", font=("Segoe UI", 9, "bold"))
        title.grid(row=0, column=0, sticky="w")
        who = tk.Label(self.bar, text=self.company, fg="#e5e7eb",
                       bg="#101826", font=("Segoe UI", 9, "bold"))
        who.grid(row=0, column=1, sticky="w", padx=(4, 0))

        self.timer_lbl = tk.Label(self.bar, text="00:00", fg="#93c5fd",
                                   bg="#101826", font=("Consolas", 10))
        self.timer_lbl.grid(row=0, column=2, padx=(12, 0))

        self.min_btn = tk.Button(self.bar, text="_", width=2, relief="flat",
                                  bg="#334155", fg="white",
                                  command=self._toggle_min)
        self.min_btn.grid(row=0, column=3, padx=(8, 0))

        self.btns = tk.Frame(self.bar, bg="#101826")
        self.btns.grid(row=1, column=0, columnspan=4, pady=(8, 0), sticky="we")
        self.pause_btn = tk.Button(self.btns, text="Pause", width=8,
                                   relief="flat", bg="#334155", fg="white",
                                   command=self._pause)
        self.pause_btn.pack(side="left", padx=(0, 6))
        tk.Button(self.btns, text="Stop Sharing", width=12, relief="flat",
                  bg="#dc2626", fg="white",
                  command=self._stop).pack(side="left")

        for w in (self.bar, title, who):
            w.bind("<Button-1>", self._press)
            w.bind("<B1-Motion>", self._move)

        self._tick()
        r.mainloop()

    def _press(self, e):
        self._drag = (e.x, e.y)

    def _move(self, e):
        x = self.root.winfo_x() + (e.x - self._drag[0])
        y = self.root.winfo_y() + (e.y - self._drag[1])
        self.root.geometry(f"+{x}+{y}")

    def _toggle_min(self):
        self._minimized = not self._minimized
        if self._minimized:
            self.btns.grid_remove()
            self.timer_lbl.grid_remove()
            self.min_btn.config(text="\u25a1")
        else:
            self.btns.grid()
            self.timer_lbl.grid()
            self.min_btn.config(text="_")

    def _pause(self):
        self._paused = not self._paused
        self.pause_btn.config(text="Resume" if self._paused else "Pause")
        if self.on_pause:
            self.on_pause(self._paused)

    def _stop(self):
        if self.on_stop:
            self.on_stop()
        self.close()

    def _tick(self):
        if not self.root:
            return
        elapsed = int(time.time() - self._start_ts)
        text = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
        if self.minutes:
            remaining = max(0, self.minutes * 60 - elapsed)
            text += f"  (\u2264 {remaining // 60:02d}:{remaining % 60:02d})"
        self.timer_lbl.config(text=text)
        self.root.after(1000, self._tick)

    def close(self):
        if self.root:
            try:
                self.root.destroy()
            except Exception:
                pass
            self.root = None


# ===========================================================================
# Odoo reporting
# ===========================================================================
class OdooClient:
    def __init__(self, odoo_url, token, db=None):
        self.base = (odoo_url or "").rstrip("/")
        self.token = token
        self.db = db

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.db:
            h["X-Odoo-Database"] = self.db
        return h

    def report(self, status, os_name=None, note=None):
        if not self.base:
            return None
        url = f"{self.base}/api/remote/agent/status"
        payload = {"token": self.token, "status": status}
        if os_name:
            payload["os"] = os_name
        if note:
            payload["note"] = note
        try:
            requests.post(url, data=json.dumps(payload),
                          headers=self._headers(), timeout=10)
        except Exception as e:  # noqa: BLE001
            logger.debug("status report failed: %s", e)

    def poll(self):
        if not self.base:
            return None
        url = f"{self.base}/api/remote/agent/poll"
        try:
            r = requests.post(url, data=json.dumps({"token": self.token}),
                              headers=self._headers(), timeout=10)
            return r.json()
        except Exception as e:  # noqa: BLE001
            logger.debug("poll failed: %s", e)
            return None


# ===========================================================================
# The session
# ===========================================================================
class TempAgentSession:
    def __init__(self, cfg):
        self.relay_url = cfg["relay"]
        self.token = cfg["token"]
        self.company = cfg.get("company", "the administrator")
        self.minutes = int(cfg.get("minutes", 30) or 30)
        self.fps = int(cfg.get("fps", DEFAULT_FPS))
        self.quality = int(cfg.get("quality", DEFAULT_JPEG_QUALITY))
        self.max_width = int(cfg.get("max_width", DEFAULT_MAX_WIDTH))
        self.odoo = OdooClient(cfg.get("odoo"), self.token, cfg.get("db"))

        self._stop = threading.Event()
        self._paused = threading.Event()
        self._loop = None
        self._ws = None
        self.window = None
        self.backend = None
        self._host_info = None
        self._streaming_reported = False
        self._deadline = None

    def stop(self, reason="stopped"):
        logger.info("session ending (%s)", reason)
        self._stop.set()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(lambda: None)

    def set_paused(self, value):
        if value:
            self._paused.set()
        else:
            self._paused.clear()

    async def _sender(self):
        frame_interval = 1.0 / max(1, self.fps)
        self.backend = select_backend(self.quality, self.max_width)
        try:
            width, height = self.backend.start()
        except Exception as e:  # noqa: BLE001
            logger.error("could not start screen capture: %s", e)
            self._stop.set()
            return
        self._host_info = {
            "type": "host_info", "os": platform.system(),
            "width": width, "height": height,
        }
        await self._ws.send(json.dumps(self._host_info))
        while not self._stop.is_set():
            start = time.time()
            if self._deadline and time.time() > self._deadline:
                logger.info("agreed duration reached — stopping")
                self._stop.set()
                break
            if not self._paused.is_set():
                try:
                    jpg = self.backend.grab_jpeg()
                    if jpg:
                        await self._ws.send(jpg)
                except Exception as e:  # noqa: BLE001
                    logger.warning("capture/send failed: %s", e)
                    break
            dt = time.time() - start
            await asyncio.sleep(max(0, frame_interval - dt))

    async def _receiver(self):
        try:
            async for message in self._ws:
                if isinstance(message, (bytes, bytearray)):
                    continue
                try:
                    obj = json.loads(message)
                except Exception:
                    continue
                mtype = obj.get("type")
                if mtype == "peer" and obj.get("event") == "join" \
                        and obj.get("role") == "viewer":
                    if self._host_info and self._ws:
                        try:
                            await self._ws.send(json.dumps(self._host_info))
                        except Exception:
                            pass
                    if not self._streaming_reported:
                        self._streaming_reported = True
                        self.odoo.report("streaming")
                    continue
                if mtype == "peer" and obj.get("event") == "leave" \
                        and obj.get("role") == "viewer":
                    logger.info("viewer left — ending session")
                    break
                if mtype == "end_session":
                    logger.info("admin ended session")
                    break
                if mtype in ("mouse_move", "mouse_down", "mouse_up",
                             "scroll", "key_down", "key_up"):
                    if self.backend:
                        self.backend.inject(obj)
        finally:
            self._stop.set()

    async def _run_async(self):
        try:
            async with websockets.connect(self.relay_url, max_size=None,
                                           ping_interval=20) as ws:
                self._ws = ws
                await ws.send(json.dumps({
                    "type": "hello", "role": "host", "token": self.token}))
                ack = json.loads(await ws.recv())
                if ack.get("type") == "error":
                    logger.error("relay rejected host: %s", ack.get("reason"))
                    return
                logger.info("connected to relay as host")
                self._deadline = time.time() + self.minutes * 60
                self.odoo.report("connected", os_name=platform.system())
                await asyncio.gather(self._sender(), self._receiver())
        except Exception as e:  # noqa: BLE001
            logger.warning("relay connection error: %s", e)
        finally:
            self._stop.set()

    def _poll_thread(self):
        while not self._stop.is_set():
            cmd = self.odoo.poll()
            if cmd and cmd.get("command") == "end":
                logger.info("server requested end")
                self.stop("ended by administrator")
                break
            self._stop.wait(5)

    def _net_thread(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_async())
        finally:
            self._loop.close()
        if self.backend:
            self.backend.stop()
        self.odoo.report("ended")
        if self.window:
            self.window.close()

    def start(self):
        if not _CORE_OK:
            logger.error("missing dependencies: %s", _CORE_ERR)
            logger.error("install: pip install pillow websockets requests")
            return
        threading.Thread(target=self._net_thread, daemon=True).start()
        threading.Thread(target=self._poll_thread, daemon=True).start()
        self.window = FloatingWindow(
            self.company, self.minutes,
            on_stop=lambda: self.stop("customer clicked Stop Sharing"),
            on_pause=self.set_paused,
        )
        if _TK_OK:
            self.window.run()
            self._stop.set()
        else:
            while not self._stop.is_set():
                time.sleep(0.5)


# ===========================================================================
# Config loading + entry point
# ===========================================================================
# These may be stamped in at BUILD TIME by build_linux.sh / build_windows.bat
# (they rewrite the __BAKE_* placeholders). This lets a single double-click
# binary carry its relay/Odoo address with no session.json.
BAKED = {
    "relay": "__BAKE_RELAY__",
    "odoo": "__BAKE_ODOO__",
    "db": "__BAKE_DB__",
    "company": "__BAKE_COMPANY__",
    "minutes": "__BAKE_MINUTES__",
}


def _baked():
    out = {}
    for k, v in BAKED.items():
        if v and not v.startswith("__BAKE_"):
            out[k] = v
    return out


def _token_from_filename():
    """A single-file build carries the token in its own name, e.g.
    RemoteAssist~<token>.exe  ->  <token>. (base64url tokens never contain
    '~', so it's a safe separator.)"""
    prog = os.path.basename(
        sys.executable if getattr(sys, "frozen", False) else __file__)
    name = os.path.splitext(prog)[0]
    if "~" in name:
        tok = name.rsplit("~", 1)[1].strip()
        if len(tok) >= 12:
            return tok
    return None


def _load_config():
    ap = argparse.ArgumentParser(description="Remote Assistance temporary agent")
    ap.add_argument("--config", help="path to session.json")
    ap.add_argument("--token")
    ap.add_argument("--relay")
    ap.add_argument("--odoo")
    ap.add_argument("--db")
    ap.add_argument("--company")
    ap.add_argument("--minutes", type=int)
    args = ap.parse_args()

    # 1) baked-in build-time defaults (relay/odoo/db/company)
    cfg = _baked()
    # 2) token embedded in this file's own name (single-file mode)
    fn_tok = _token_from_filename()
    if fn_tok:
        cfg["token"] = fn_tok
        logger.info("using token from filename")

    here = os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, "frozen", False) else __file__))
    # 3) session.json next to the program (zip mode) overrides baked values
    candidates = [args.config] if args.config else []
    candidates += [os.path.join(here, "session.json"),
                   os.path.join(os.getcwd(), "session.json")]
    for path in candidates:
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    cfg.update(json.load(fh))
                logger.info("loaded config from %s", path)
                break
            except Exception as e:  # noqa: BLE001
                logger.warning("could not read %s: %s", path, e)
    for k, env in (("token", "RA_TOKEN"), ("relay", "RA_RELAY"),
                   ("odoo", "RA_ODOO"), ("db", "RA_DB"),
                   ("company", "RA_COMPANY"), ("minutes", "RA_MINUTES")):
        if os.environ.get(env):
            cfg[k] = os.environ[env]
    for k in ("token", "relay", "odoo", "db", "company", "minutes"):
        v = getattr(args, k)
        if v:
            cfg[k] = v
    if isinstance(cfg.get("minutes"), str) and cfg["minutes"].isdigit():
        cfg["minutes"] = int(cfg["minutes"])
    return cfg


def main():
    cfg = _load_config()
    if not cfg.get("token") or not cfg.get("relay"):
        logger.error("No session token / relay configured. Expected a "
                     "session.json next to this program, or --token/--relay.")
        if _TK_OK:
            try:
                r = tk.Tk()
                r.title("Remote Assistance")
                tk.Label(r, text="This Remote Assistance agent is missing its "
                                 "session file.\nPlease download it again from "
                                 "the link in your email.",
                         justify="left", padx=20, pady=20).pack()
                r.mainloop()
            except Exception:
                pass
        sys.exit(1)
    logger.info("Starting Remote Assistance session for %s (%s min)",
                cfg.get("company", "administrator"), cfg.get("minutes", 30))
    TempAgentSession(cfg).start()
    logger.info("Remote Assistance session finished. Nothing was left "
                "installed on this computer.")


if __name__ == "__main__":
    main()
