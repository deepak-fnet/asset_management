#!/usr/bin/env python3
"""
Asset Remote Assistance — Worker (runs alongside the installed asset agent)
===========================================================================

This runs persistently on a managed asset (launched by your existing asset
agent). It:

  * polls Odoo for remote-assistance requests by this machine's serial number;
  * when a request arrives, shows an Accept / Reject dialog ON THIS MACHINE;
  * on Accept, connects to the asset relay as the *host*, streams the screen,
    and injects the administrator's mouse / keyboard;
  * shows a small always-on-top floating window with a timer + Stop Sharing;
  * ends when the duration elapses, the user clicks Stop Sharing, or the
    administrator ends the session — then goes back to waiting for the next
    request. Nothing is shared without explicit on-machine consent.

Launch (from your asset agent, once):
    python3 asset_remote_worker.py --odoo https://odoo.example.com
        --odoo https://odoo.example.com   # serial auto-detected
Capture backends auto-select: Windows/X11 -> mss+pynput; Wayland -> portal.

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
WORKER_VERSION = "1.5-mss-backend-probe"
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
    # WAYLAND_DISPLAY being set at all is sufficient on its own. The old
    # fallback here also required DISPLAY to be ABSENT, which only ever
    # matched a Wayland session with no XWayland running at all - rare,
    # since virtually every desktop auto-starts XWayland so legacy X11 apps
    # keep working. XWayland deliberately provides a DISPLAY value for that
    # compatibility even though the session is genuinely Wayland - but
    # classic X11 screen capture (XGetImage on the root window, what mss
    # does) does not work through it: Wayland's security model intentionally
    # blocks arbitrary clients from reading the framebuffer that way, which
    # is exactly why PortalBackend exists as a separate path below. The old
    # heuristic let a real Wayland+XWayland session get misclassified as
    # X11 and handed to mss - which then failed, not intermittently once
    # XInitThreads() removed the threading race, but consistently, because
    # it isn't a race at all: XGetImage() on an XWayland root window simply
    # does not work, regardless of threading.
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def _init_x11_threading():
    """Call Xlib's XInitThreads() once, before anything opens a display.

    Two different X11 client libraries are used in this process, from two
    different threads: Tk's own bundled Xlib bindings (ConsentDialog and
    FloatingWindow, main thread) and mss's separate ctypes-based raw Xlib
    calls (MssPynputBackend, called from _net_thread - a background thread,
    since capture runs inside the asyncio loop that streams frames).

    Xlib is explicitly documented as NOT thread-safe unless XInitThreads()
    is called before the FIRST connection is opened by ANY thread in the
    process. Skipping it produces exactly the symptoms seen here: an
    intermittent mix of XOpenDisplay() and XGetImage() failures that vary
    run to run, rather than a consistent, explainable error - the classic
    signature of an unsynchronized multi-threaded Xlib race, not a
    permissions or display-availability problem.

    Must run before the first tk.Tk() and before the first mss.mss() in the
    process - so this is called as the very first thing in main(), before
    the worker loop (and therefore before any dialog or capture) starts.
    Wayland/Windows/macOS don't use Xlib this way, so this is a no-op there.
    """
    if platform.system() != "Linux" or _session_is_wayland():
        return
    try:
        import ctypes
        x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
        x11.XInitThreads()
        logger.info("XInitThreads() done (X11 is now safe to use across threads)")
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "XInitThreads() unavailable (%s) - Tk (main thread) and mss "
            "capture (background thread) may intermittently fail against "
            "X11 (XOpenDisplay/XGetImage) without it", e)


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

    # Process-lifetime shared capture handle. mss.mss() opens a fresh X11
    # client connection every time it's constructed. Creating and closing
    # one PER remote-assistance session was the actual root cause of the
    # progressive failures observed: each session left the X server with
    # one fewer available per-client resource than it started with, so the
    # very next session was more likely to fail (first XGetImage(), then
    # eventually XOpenDisplay() itself), ending in total X11 failure and,
    # once enough state was corrupted, an unrelated-looking Tk crash. mss
    # itself was never the bug - opening and closing it repeatedly within
    # one long-running process was. Creating it ONCE, on first use, and
    # reusing it for the life of the worker process avoids that churn
    # entirely; it is intentionally never closed by stop() below.
    _shared_sct = None
    _shared_sct_lock = threading.Lock()

    @classmethod
    def _get_shared_sct(cls):
        with cls._shared_sct_lock:
            if cls._shared_sct is None:
                cls._shared_sct = cls._open_working_sct()
            return cls._shared_sct

    @classmethod
    def _reset_shared_sct(cls):
        """Drop the cached handle so the next start() re-selects a backend."""
        with cls._shared_sct_lock:
            sct, cls._shared_sct = cls._shared_sct, None
        try:
            if sct is not None:
                sct.close()
        except Exception:
            pass

    @staticmethod
    def _open_working_sct():
        """Open an mss handle using a backend that can actually capture here.

        mss 10.2.0+ exposes selectable X11 backends. The default
        ("xshmgetimage") uses the MIT-SHM shared-memory extension, which is
        the fast path but is unavailable or broken in a number of real
        setups - remote/forwarded displays, hardened kernels restricting
        /dev/shm, some Xorg configurations, containers. mss documents an
        automatic fallback to XGetImage when MIT-SHM is "unavailable", but
        that detection only covers the extension being absent - not the case
        where it is advertised and then fails at capture time, which is
        exactly what "XGetImage() failed" on every single frame (while
        mss.mss() itself opens fine) looks like.

        So rather than trusting one backend, try each in turn and keep the
        first that survives a real test capture. Opening the handle is not
        proof it works - that was the trap that made this look intermittent
        - so each candidate is verified with an actual grab() before being
        accepted.

        Older mss versions do not accept the `backend` kwarg at all; that
        raises TypeError and is handled by falling through to a plain
        mss.mss(), preserving the previous behaviour on those versions.
        """
        candidates = ["default", "xgetimage", "xlib"]
        last_error = None
        for backend in candidates:
            sct = None
            try:
                try:
                    sct = mss.mss(backend=backend)
                except TypeError:
                    # mss too old for selectable backends - one attempt only.
                    sct = mss.mss()
                    backend = "legacy(no backend kwarg)"
                # Prove it actually captures. mss.mss() succeeding says
                # nothing about whether grab() will work.
                sct.grab(sct.monitors[1])
                logger.info("mss capture backend '%s' verified working", backend)
                return sct
            except Exception as e:  # noqa: BLE001
                last_error = e
                logger.warning("mss backend '%s' unusable: %s", backend, e)
                try:
                    if sct is not None:
                        sct.close()
                except Exception:
                    pass
                if backend.startswith("legacy"):
                    break
        raise RuntimeError(
            "no working mss capture backend on this display (tried %s). "
            "Last error: %s" % (", ".join(candidates), last_error))

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
        self._sct = self._get_shared_sct()
        self._mon = self._sct.monitors[1]
        self._inj = InputInjector(self._mon)
        return self._mon["width"], self._mon["height"]

    def grab_jpeg(self):
        try:
            shot = self._sct.grab(self._mon)
        except Exception:
            # The shared handle is reused for the whole process lifetime, so
            # if it goes bad it would otherwise stay bad for every future
            # session too. Drop it here; the next start() re-runs backend
            # selection from scratch rather than handing back a dead handle.
            type(self)._reset_shared_sct()
            raise
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
        # Deliberately does NOT close self._sct - it is the shared,
        # process-lifetime capture handle now, reused by the next session.
        # Only this backend instance's own per-session references are
        # dropped here.
        self._sct = None
        self._mon = None
        self._inj = None


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

    def _portal_request(self, interface, method, signature, fixed_args,
                        options):
        """Call a portal method (Request/Response pattern) and block until the
        Response signal arrives. `fixed_args` are plain Python values for the
        leading typed args; `options` is a dict of {str: GLib.Variant} for the
        trailing a{sv}."""
        GLib = self._GLib
        token = "ra%d" % (int(time.time() * 1000) % 1000000)
        options = dict(options)
        options["handle_token"] = GLib.Variant("s", token)
        sender = self._conn.get_unique_name()[1:].replace(".", "_")
        request_path = ("/org/freedesktop/portal/desktop/request/%s/%s"
                        % (sender, token))
        result = {}
        done = {"v": False}

        def on_response(conn, s, obj, iface, sig, params):
            try:
                code, results = params.unpack()
            except Exception:
                code, results = 2, {}
            result["code"] = code
            result["results"] = results
            done["v"] = True

        sub = self._conn.signal_subscribe(
            "org.freedesktop.portal.Desktop",
            "org.freedesktop.portal.Request", "Response", request_path,
            None, self._Gio.DBusSignalFlags.NONE, on_response)
        try:
            variant = GLib.Variant(signature, tuple(fixed_args) + (options,))
            self._conn.call_sync(
                "org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop", interface, method,
                variant, None, self._Gio.DBusCallFlags.NONE, -1, None)
            ctx = GLib.MainContext.default()
            waited = 0.0
            while not done["v"] and waited < 90.0:
                ctx.iteration(False)
                time.sleep(0.02)
                waited += 0.02
        finally:
            self._conn.signal_unsubscribe(sub)
        if not done.get("v"):
            raise RuntimeError("portal %s timed out (no dialog response)"
                               % method)
        if result.get("code") != 0:
            raise RuntimeError("portal %s was cancelled or denied by the user"
                               % method)
        return result.get("results", {})

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
                "org.freedesktop.portal.RemoteDesktop", "CreateSession",
                "(a{sv})", (),
                {"session_handle_token": GLib.Variant("s", st)})
            self._session = res["session_handle"]

            # 2) SelectDevices (keyboard + pointer)
            self._portal_request(
                "org.freedesktop.portal.RemoteDesktop", "SelectDevices",
                "(oa{sv})", (self._session,),
                {"types": GLib.Variant("u", self.DEVICE_KEYBOARD |
                                       self.DEVICE_POINTER)})

            # 3) ScreenCast.SelectSources (a monitor, embedded cursor)
            self._portal_request(
                "org.freedesktop.portal.ScreenCast", "SelectSources",
                "(oa{sv})", (self._session,),
                {"types": GLib.Variant("u", self.SOURCE_MONITOR),
                 "multiple": GLib.Variant("b", False),
                 "cursor_mode": GLib.Variant("u", 2)})  # 2 = embedded

            # 4) Start -> user approves; returns the stream(s)
            res = self._portal_request(
                "org.freedesktop.portal.RemoteDesktop", "Start",
                "(osa{sv})", (self._session, ""), {})
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
# ONE persistent, hidden Tk root for the whole process lifetime
# ===========================================================================
# Both ConsentDialog and FloatingWindow used to call tk.Tk() directly, each
# creating and then destroying a brand new Tcl interpreter - once per
# session, potentially many times an hour. That repeatedly proved fragile on
# this system even after fixing the specific bugs found along the way
# (uncancelled .after() callbacks, close() touching Tk from the wrong
# thread): the underlying problem is that creating/destroying multiple
# top-level Tk() interpreters within one long-running process is not fully
# supported by Tcl/Tk, which assumes one interpreter per process lifetime.
# Symptoms kept recurring in new forms ("invalid command name", then
# Tcl_AsyncDelete crashes at different points) because each patch fixed one
# specific interaction, not the underlying fragility.
#
# The fix: create exactly ONE Tk() root, once, for the entire process
# lifetime - withdrawn (invisible) since nothing is ever shown in it
# directly - and make every dialog a Toplevel(root) child instead. Toplevel
# creation/destruction within a single persistent interpreter is the
# standard, well-supported tkinter pattern for showing many dialogs over a
# long process lifetime, unlike repeatedly creating fresh Tk() instances.
_APP_ROOT = None


def _get_app_root():
    global _APP_ROOT
    if _APP_ROOT is None:
        _APP_ROOT = tk.Tk()
        _APP_ROOT.withdraw()
    return _APP_ROOT


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
        self._tick_id = None
        # Set from ANY thread to request closing. Never touched directly by
        # Tk from a background thread - _tick() (already guaranteed to run
        # on the mainloop's own thread, since Tk itself schedules it) polls
        # this flag and performs the actual teardown. Confirmed necessary:
        # even root.after(0, ...) called FROM the background thread was not
        # safe on this system and reproduced the same corruption, so nothing
        # Tcl-related is called from _net_thread at all now, not even
        # scheduling.
        self._close_requested = threading.Event()

    def run(self):
        if not _TK_OK:
            logger.info("tkinter unavailable — running without floating window")
            return
        try:
            # A Toplevel child of the one persistent app root, not a fresh
            # Tk() interpreter. See the comment above _get_app_root().
            self.root = tk.Toplevel(_get_app_root())
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
        # wait_window() blocks the calling (main) thread until this Toplevel
        # is destroyed, while still fully pumping Tcl's event loop (timers,
        # button clicks) - the correct replacement for mainloop() when using
        # a shared persistent root rather than a dedicated Tk() interpreter.
        _get_app_root().wait_window(r)

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
        # This callback is dispatched by Tk itself (button click), so it is
        # already running on the mainloop's own thread - safe to tear down
        # immediately rather than waiting for the next _tick() poll.
        self._destroy_now()

    def _tick(self):
        if not self.root:
            return
        if self._close_requested.is_set():
            # A background thread (_net_thread - viewer left, relay error,
            # deadline reached) asked us to close. This check runs on the
            # mainloop's own thread, same as the rest of _tick(), so it's
            # the only place other than the button handler that ever touches
            # self.root for teardown.
            self._destroy_now()
            return
        elapsed = int(time.time() - self._start_ts)
        text = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
        if self.minutes:
            remaining = max(0, self.minutes * 60 - elapsed)
            text += f"  (\u2264 {remaining // 60:02d}:{remaining % 60:02d})"
        self.timer_lbl.config(text=text)
        self._tick_id = self.root.after(1000, self._tick)

    def _destroy_now(self):
        """The ONLY method that actually calls root.after_cancel()/destroy().
        Only ever reached from _tick() or _stop() - both guaranteed to run on
        the mainloop's own thread. Never call this directly from
        _net_thread; use close() instead."""
        if not self.root:
            return
        try:
            if self._tick_id is not None:
                self.root.after_cancel(self._tick_id)
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        self.root = None
        self._tick_id = None

    def close(self):
        """Safe to call from ANY thread.

        _net_thread (background) calls this whenever a session ends for a
        reason other than the physical Stop Sharing click - viewer
        disconnect, relay error, deadline reached, admin-ended. Earlier
        versions of this fix had close() call root.destroy() directly, and
        then root.after(0, ...) to defer it - both still touched the Tcl
        interpreter from the wrong thread and both still reproduced
        "invalid command name ..._tick" followed eventually by
        "Tcl_AsyncDelete: async handler deleted by the wrong thread". This
        version calls NOTHING Tcl-related from a background thread, not even
        scheduling - it only sets a plain Python threading.Event, which
        _tick() (already running safely on the mainloop thread) polls.
        Bounded by the ~1s tick interval, which is an acceptable tradeoff for
        actually being correct.
        """
        self._close_requested.set()


# ===========================================================================
# Odoo client for the asset remote endpoints
# ===========================================================================
class AssetOdooClient:
    def __init__(self, odoo_url, db=None):
        self.base = (odoo_url or "").rstrip("/")
        self.db = db

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.db:
            h["X-Odoo-Database"] = self.db
        return h

    def _post(self, path, payload):
        if not self.base:
            return None
        try:
            r = requests.post(self.base + path, data=json.dumps(payload),
                              headers=self._headers(), timeout=10)
            return r.json()
        except Exception as e:  # noqa: BLE001
            logger.debug("POST %s failed: %s", path, e)
            return None

    def poll(self, serial):
        return self._post("/api/asset/remote/poll", {"serial_number": serial})

    def consent(self, token, decision):
        return self._post("/api/asset/remote/consent",
                          {"token": token, "decision": decision})

    def report(self, token, status, os_name=None, note=None):
        payload = {"token": token, "status": status}
        if os_name:
            payload["os"] = os_name
        if note:
            payload["note"] = note
        return self._post("/api/asset/remote/status", payload)

    def session_poll(self, token):
        return self._post("/api/asset/remote/session_poll", {"token": token})


# ===========================================================================
# Consent dialog shown ON THE MACHINE before any sharing starts
# ===========================================================================
class ConsentDialog:
    """Modal Accept/Reject prompt. Returns True (accept) or False (reject)."""

    def __init__(self, company, admin, minutes):
        self.company = company or "An administrator"
        self.admin = admin or ""
        self.minutes = minutes or 0
        self.result = False

    def ask(self):
        if not _TK_OK:
            # No GUI available: refuse by default (never share without consent).
            logger.warning("no GUI for consent dialog — refusing by default")
            return False
        try:
            # A Toplevel child of the one persistent app root, not a fresh
            # Tk() interpreter - see the comment above _get_app_root().
            root = tk.Toplevel(_get_app_root())
        except Exception as e:  # noqa: BLE001
            logger.warning("no display for consent dialog (%s) — refusing", e)
            return False
        root.title("Remote Assistance Request")
        root.attributes("-topmost", True)
        root.configure(bg="#0b1020")
        try:
            root.eval("tk::PlaceWindow . center")
        except Exception:
            pass

        wrap = tk.Frame(root, bg="#0b1020")
        wrap.pack(padx=24, pady=20)
        tk.Label(wrap, text="Remote Assistance Request", fg="#7dd3fc",
                 bg="#0b1020", font=("Segoe UI", 11, "bold")).pack(
                 anchor="w")
        who = self.company + ((" (%s)" % self.admin) if self.admin else "")
        tk.Label(wrap, text="%s wants to view and control this computer."
                 % who, fg="#e5e7eb", bg="#0b1020", wraplength=360,
                 justify="left", font=("Segoe UI", 10)).pack(anchor="w",
                 pady=(8, 4))
        if self.minutes:
            tk.Label(wrap, text="Requested duration: %d minutes"
                     % self.minutes, fg="#93c5fd", bg="#0b1020",
                     font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(wrap, text="You can stop sharing at any time.",
                 fg="#7286a6", bg="#0b1020",
                 font=("Segoe UI", 8)).pack(anchor="w", pady=(2, 12))

        btns = tk.Frame(wrap, bg="#0b1020")
        btns.pack(fill="x")

        def accept():
            self.result = True
            root.destroy()

        def reject():
            self.result = False
            root.destroy()

        tk.Button(btns, text="Accept", width=12, relief="flat",
                  bg="#16a34a", fg="white", command=accept).pack(
                  side="left", padx=(0, 8))
        tk.Button(btns, text="Reject", width=12, relief="flat",
                  bg="#dc2626", fg="white", command=reject).pack(side="left")
        root.protocol("WM_DELETE_WINDOW", reject)
        # wait_window() instead of mainloop() - see FloatingWindow.run()
        # for why: this blocks here until Accept/Reject destroys the
        # Toplevel, while the one persistent interpreter keeps running.
        _get_app_root().wait_window(root)
        return self.result


# ===========================================================================
# Streaming session (after consent)
# ===========================================================================
class AssetStreamingSession:
    def __init__(self, cfg, odoo):
        self.token = cfg["token"]
        self.relay_url = cfg.get("relay_url") or cfg.get("relay")
        self.company = cfg.get("company", "the administrator")
        self.minutes = int(cfg.get("minutes", 30) or 30)
        self.fps = int(cfg.get("fps", DEFAULT_FPS))
        self.quality = int(cfg.get("quality", DEFAULT_JPEG_QUALITY))
        self.max_width = int(cfg.get("max_width", DEFAULT_MAX_WIDTH))
        self.odoo = odoo
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._loop = None
        self._ws = None
        self.window = None
        self.backend = None
        self._host_info = None
        self._streaming_reported = False
        self._grab_failures = 0
        self._MAX_GRAB_FAILURES = 15  # ~4.5s of tolerance at 0.3s/retry
        self._deadline = None

    def stop(self, reason="stopped"):
        logger.info("session ending (%s)", reason)
        self._stop.set()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(lambda: None)

    def set_paused(self, value):
        self._paused.set() if value else self._paused.clear()

    async def _sender(self):
        interval = 1.0 / max(1, self.fps)
        self.backend = select_backend(self.quality, self.max_width)
        # Retry a few times before giving up. Observed pattern: a session
        # that ends cleanly is sometimes immediately followed by ANOTHER
        # session (new backend instance, same long-running worker process)
        # that fails to open the display at all, then works again on a
        # further attempt/restart. That is consistent with the X server (or
        # mss's use of the MIT-SHM extension) needing a brief moment to
        # release the previous session's resources before a fresh mss.mss()
        # can attach again - not a permanent condition, so retrying with a
        # short backoff is the correct response, not failing immediately.
        last_error = None
        w = h = None
        for attempt in range(1, 4):
            try:
                w, h = self.backend.start()
                last_error = None
                break
            except Exception as e:  # noqa: BLE001
                last_error = e
                logger.warning(
                    "capture start attempt %d/3 failed: %s", attempt, e)
                if attempt < 3:
                    await asyncio.sleep(1.0)
        if last_error is not None:
            logger.error("could not start capture after 3 attempts: %s",
                         last_error)
            # Tell Odoo the truth instead of leaving the session looking
            # live. asset.remote.session._agent_report() already handles
            # status == "error" (logs it, does not change state), so this
            # needs no server-side change.
            try:
                self.odoo.report(self.token, "error",
                                 note="Screen capture failed to start after "
                                      "3 attempts: %s" % last_error)
            except Exception:
                pass
            self._stop.set()
            return
        # Only now - capture confirmed working - tell Odoo the session is
        # actually live.
        self.odoo.report(self.token, "connected", os_name=platform.system())
        self._host_info = {"type": "host_info", "os": platform.system(),
                           "width": w, "height": h}
        await self._ws.send(json.dumps(self._host_info))
        while not self._stop.is_set():
            t0 = time.time()
            if self._deadline and time.time() > self._deadline:
                logger.info("agreed duration reached — stopping")
                self._stop.set()
                break
            if not self._paused.is_set():
                try:
                    jpg = self.backend.grab_jpeg()
                    if jpg:
                        await self._ws.send(jpg)
                    self._grab_failures = 0
                except Exception as e:  # noqa: BLE001
                    # XGetImage() has been observed to fail intermittently -
                    # sometimes on the very first frame right after start()
                    # succeeded, sometimes mid-session - and then work again
                    # moments later. Rather than end the whole session on a
                    # single blip, tolerate a short run of consecutive
                    # failures before giving up. Genuine, persistent failures
                    # (display gone, session torn down) still end the
                    # session; they just take a few hundred ms longer to be
                    # declared failed instead of one bad frame killing it.
                    self._grab_failures += 1
                    logger.warning("capture/send failed (%d/%d): %s",
                                   self._grab_failures,
                                   self._MAX_GRAB_FAILURES, e)
                    if self._grab_failures < self._MAX_GRAB_FAILURES:
                        await asyncio.sleep(0.3)
                        continue
                    logger.error(
                        "capture failing repeatedly (%d in a row) - "
                        "ending session", self._grab_failures)
                    # Without this, a capture failure mid-session just stops
                    # frames silently - the admin's viewer freezes on the
                    # last frame (or stays blank if none arrived yet) with no
                    # indication anything went wrong. Report it the same way
                    # a startup failure already is.
                    try:
                        self.odoo.report(self.token, "error",
                                         note="Capture failed repeatedly: %s" % e)
                    except Exception:
                        pass
                    break
            await asyncio.sleep(max(0, interval - (time.time() - t0)))

    async def _receiver(self):
        try:
            async for message in self._ws:
                if isinstance(message, (bytes, bytearray)):
                    continue
                try:
                    obj = json.loads(message)
                except Exception:
                    continue
                mt = obj.get("type")
                if mt == "peer" and obj.get("event") == "join" \
                        and obj.get("role") == "viewer":
                    if self._host_info:
                        try:
                            await self._ws.send(json.dumps(self._host_info))
                        except Exception:
                            pass
                    if not self._streaming_reported:
                        self._streaming_reported = True
                        self.odoo.report(self.token, "streaming")
                    continue
                if mt == "peer" and obj.get("event") == "leave" \
                        and obj.get("role") == "viewer":
                    logger.info("viewer left — ending")
                    break
                if mt == "end_session":
                    logger.info("admin ended session")
                    break
                if mt in ("mouse_move", "mouse_down", "mouse_up", "scroll",
                          "key_down", "key_up") and self.backend:
                    self.backend.inject(obj)
        finally:
            self._stop.set()

    async def _run_async(self):
        try:
            async with websockets.connect(self.relay_url, max_size=None,
                                           ping_interval=20) as ws:
                self._ws = ws
                await ws.send(json.dumps({"type": "hello", "role": "host",
                                          "token": self.token}))
                ack = json.loads(await ws.recv())
                if ack.get("type") == "error":
                    logger.error("relay rejected host: %s", ack.get("reason"))
                    return
                logger.info("connected to relay as host")
                self._deadline = time.time() + self.minutes * 60
                # NOTE: no longer reporting "connected" to Odoo here. It used
                # to fire unconditionally at this point, before the capture
                # backend had even tried to start - so when mss/pynput failed
                # with XOpenDisplay(), the Odoo session still showed live and
                # connected with nothing behind it (the "blank screen"
                # symptom). Moved into _sender(), which now only reports it
                # once the capture backend has actually started successfully.
                await asyncio.gather(self._sender(), self._receiver())
        except Exception as e:  # noqa: BLE001
            logger.warning("relay connection error: %s", e)
        finally:
            self._stop.set()

    def _poll_thread(self):
        while not self._stop.is_set():
            cmd = self.odoo.session_poll(self.token)
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
        self.odoo.report(self.token, "ended")
        if self.window:
            self.window.close()

    def run(self):
        threading.Thread(target=self._net_thread, daemon=True).start()
        threading.Thread(target=self._poll_thread, daemon=True).start()
        self.window = FloatingWindow(
            self.company, self.minutes,
            on_stop=lambda: self.stop("user clicked Stop Sharing"),
            on_pause=self.set_paused)
        if _TK_OK:
            self.window.run()
            self._stop.set()
        else:
            while not self._stop.is_set():
                time.sleep(0.5)


# ===========================================================================
# Worker: poll for requests, get consent, then stream
# ===========================================================================
class AssetRemoteWorker:
    def __init__(self, odoo_url, serial, db=None, poll_interval=5):
        self.odoo = AssetOdooClient(odoo_url, db)
        self.serial = serial
        self.poll_interval = poll_interval
        self._handled_reject = set()
        self._active = None  # token currently being streamed (if any)

    def run_forever(self):
        logger.info("Asset remote worker v%s started (serial=%s..., odoo=%s)",
                    WORKER_VERSION, (self.serial or "")[:8], self.odoo.base)
        while True:
            try:
                cmd = self.odoo.poll(self.serial)
            except Exception as e:  # noqa: BLE001
                logger.debug("poll error: %s", e)
                cmd = None
            if not cmd or cmd.get("command") == "idle":
                time.sleep(self.poll_interval)
                continue
            token = cmd.get("token")
            command = cmd.get("command")
            if command == "consent":
                if token in self._handled_reject:
                    time.sleep(self.poll_interval)
                    continue
                logger.info("remote request received — prompting user")
                ok = ConsentDialog(cmd.get("company"), cmd.get("admin"),
                                   cmd.get("minutes")).ask()
                if ok:
                    self._active = token
                    self.odoo.consent(token, "accept")
                    self._stream(cmd)
                    self._active = None        # session over; forget it
                    self._handled_reject.add(token)  # never reuse this token
                else:
                    self.odoo.consent(token, "reject")
                    self._handled_reject.add(token)
                    logger.info("user rejected the request")
            elif command == "connect":
                # We are idle (not streaming) yet Odoo reports this session as
                # already accepted/connected. That is a STALE session — the
                # customer closed it, or it is left over from a previous run or
                # another process. NEVER silently stream it (that would connect
                # with no popup). Clear it so the next request prompts again.
                logger.info("clearing stale/unaccepted session (%s)",
                            (token or "")[:8])
                try:
                    self.odoo.report(token, "ended",
                                     note="stale session cleared by agent")
                except Exception:  # noqa: BLE001
                    pass
                self._handled_reject.add(token)
                time.sleep(self.poll_interval)
            time.sleep(1)

    def _stream(self, cmd):
        try:
            AssetStreamingSession(cmd, self.odoo).run()
        except Exception as e:  # noqa: BLE001
            logger.warning("streaming session error: %s", e)
        logger.info("session finished; back to waiting for requests")


def _run(cmd):
    try:
        import subprocess
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def detect_serial():
    """Best-effort machine serial, matching what the asset agent reports.
    Windows: wmic BIOS serial (works as a normal user).
    Linux: read the file the daemon writes (BIOS serial usually needs root),
           then fall back to /sys, then MAC.
    macOS: ioreg."""
    sysname = platform.system()
    try:
        if sysname == "Windows":
            out = _run(["wmic", "bios", "get", "serialnumber"])
            for line in out.splitlines():
                s = line.strip()
                if s and s.lower() != "serialnumber" and s.upper() not in (
                        "TO BE FILLED BY O.E.M.", "0", "NONE", "DEFAULT STRING"):
                    return s
        elif sysname == "Darwin":
            out = _run(["ioreg", "-l"])
            import re
            m = re.search(r'"IOPlatformSerialNumber"\s*=\s*"([^"]+)"', out)
            if m:
                return m.group(1)
        else:  # Linux
            # 1) file written by the asset agent daemon (recommended)
            for p in ("/var/lib/asset-agent/serial",
                      "/etc/asset-agent/serial"):
                if os.path.exists(p):
                    try:
                        v = open(p).read().strip()
                        if v:
                            return v
                    except Exception:
                        pass
            # 2) /sys (often root-only, but try)
            for p in ("/sys/class/dmi/id/product_serial",
                      "/sys/class/dmi/id/board_serial"):
                try:
                    v = open(p).read().strip()
                    if v and v.upper() not in ("", "NONE", "DEFAULT STRING"):
                        return v
                except Exception:
                    pass
            # 3) MAC fallback
            mac = _run(["cat", "/sys/class/net/"])  # placeholder, ignore
    except Exception as e:  # noqa: BLE001
        logger.debug("serial detection error: %s", e)
    return ""


def _config_paths():
    paths = []
    if platform.system() == "Windows":
        pd = os.environ.get("ProgramData", r"C:\ProgramData")
        paths.append(os.path.join(pd, "AssetAgent", "remote.json"))
    else:
        paths += ["/etc/asset-agent/remote.json",
                  "/etc/asset-agent/remote.conf"]
    here = os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, "frozen", False) else __file__))
    paths.append(os.path.join(here, "remote.json"))
    return paths


def _load_config():
    ap = argparse.ArgumentParser(
        description="Asset Remote Assistance worker (runs in the user's "
                    "session, alongside the installed asset agent).")
    ap.add_argument("--odoo", help="Odoo base URL (e.g. https://odoo.co)")
    ap.add_argument("--serial", help="This machine's serial number "
                    "(auto-detected if omitted)")
    ap.add_argument("--db", help="Odoo database name (X-Odoo-Database)")
    ap.add_argument("--config", help="path to a JSON config file")
    ap.add_argument("--poll-interval", type=int, default=5)
    args = ap.parse_args()

    cfg = {}
    candidates = [args.config] if args.config else []
    candidates += _config_paths()
    for path in candidates:
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    cfg.update(json.load(fh))
                logger.info("loaded config from %s", path)
                break
            except Exception as e:  # noqa: BLE001
                logger.warning("could not read %s: %s", path, e)
    for k, env in (("odoo", "AR_ODOO"), ("serial", "AR_SERIAL"),
                   ("db", "AR_DB")):
        if os.environ.get(env):
            cfg[k] = os.environ[env]
    for k in ("odoo", "serial", "db"):
        v = getattr(args, k)
        if v:
            cfg[k] = v
    cfg["poll_interval"] = args.poll_interval
    if not cfg.get("serial"):
        cfg["serial"] = detect_serial()
    return cfg


def _acquire_single_instance():
    """Ensure only ONE worker runs per machine. Duplicate workers race over
    the same serial/relay room and cause 'connected with no popup' bugs.
    Bind a localhost port as a lock; if it's taken, another worker is live."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        s.bind(("127.0.0.1", 47654))
        s.listen(1)
        return s  # keep this ref alive for the process lifetime
    except OSError:
        logger.error("another asset remote worker is already running on this "
                     "machine — exiting this one.")
        sys.exit(0)


def main():
    _init_x11_threading()  # MUST run before any tk.Tk() or mss.mss() below.
    if not _CORE_OK:
        logger.error("missing dependencies: %s", _CORE_ERR)
        logger.error("install: pip install pillow websockets requests "
                     "(and mss pynput for X11/Windows capture)")
        sys.exit(1)
    if _TK_OK:
        try:
            _get_app_root()  # create the one persistent Tk root now, up
                             # front, so a startup failure is loud and clear
                             # here rather than surfacing confusingly deep
                             # inside the first consent dialog.
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "could not create the Tk root at startup (%s) - consent "
                "dialogs will be refused by default until a display is "
                "available", e)
    _lock = _acquire_single_instance()  # noqa: F841 (held for process life)
    cfg = _load_config()
    if not cfg.get("odoo"):
        logger.error("Need the Odoo URL (--odoo, AR_ODOO, or 'odoo' in the "
                     "config file at %s).", _config_paths()[0])
        sys.exit(1)
    if not cfg.get("serial"):
        logger.error("Could not determine this machine's serial number. "
                     "Pass --serial, or have the asset agent write it to "
                     "/var/lib/asset-agent/serial.")
        sys.exit(1)
    worker = AssetRemoteWorker(cfg["odoo"], cfg["serial"], cfg.get("db"),
                               cfg.get("poll_interval", 5))
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        logger.info("worker stopped")


if __name__ == "__main__":
    main()