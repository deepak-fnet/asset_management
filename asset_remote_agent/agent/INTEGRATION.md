# Agent-side integration (Approach B — user-session worker)

The remote screen/keyboard cannot run inside your existing agent, because that
daemon runs as **root / SYSTEM** and a background service has no access to the
logged-in user's screen (Windows Session 0 isolation; Linux has no DISPLAY;
macOS TCC). So the remote worker runs as a **small process in the user's
session**, started at login. Your existing daemon stays as-is except for one
optional line on Linux.

`asset_remote_worker.py` reuses the same capture/input engine as the temporary
agent (Windows/X11 via mss+pynput, Wayland via the screen-share portal). It:
polls Odoo for a request for *this machine's serial*, shows an Accept/Reject
dialog on the machine, and streams on Accept.

The worker needs two things: the **Odoo URL** (+ DB) and this machine's
**serial number**. Odoo matches the serial to the asset (same identity your
agent already reports).

---
## Ubuntu

1. **Ship the worker** with your agent files (your `.deb` already installs to
   `/usr/local/bin/asset-agent/`). Add the worker there:
   ```
   usr/local/bin/asset-agent/asset_remote_worker.py
   ```
2. **Write the org config** during install (postinst) so the worker knows the
   Odoo URL + DB:
   ```
   /etc/asset-agent/remote.json      # {"odoo":"https://…","db":"TEST"}
   ```
   (Use `agent/remote.example.json` as the template.)
3. **Autostart in the user session** — install this file (from
   `agent/autostart/`) via the `.deb`:
   ```
   /etc/xdg/autostart/asset-remote.desktop
   ```
4. **Hand the serial to the user session.** The BIOS serial usually needs
   root, and the worker runs as the user — so have the daemon write it once.
   In your `agent.py` `main()`, right after `serial = get_serial_number()`,
   add:
   ```python
   try:
       os.makedirs("/var/lib/asset-agent", exist_ok=True)
       with open("/var/lib/asset-agent/serial", "w") as _f:
           _f.write(serial or "")
   except Exception:
       pass
   ```
   The worker reads `/var/lib/asset-agent/serial` automatically.
5. **Dependencies on the machine:**
   ```
   pip install pillow websockets requests mss pynput
   # Wayland capture also needs the system stack:
   sudo apt-get install python3-gi gstreamer1.0-pipewire \
       gir1.2-gst-plugins-base-1.0 python3-tk
   ```
   Use a Python 3.8+ `python3` (not an old pyenv 3.6). Adjust the `Exec=` path
   in the .desktop if your python is elsewhere.

Test without rebuilding the .deb:
```
echo '{"odoo":"http://<odoo-host>:8015","db":"TEST"}' | sudo tee /etc/asset-agent/remote.json
python3 /usr/local/bin/asset-agent/asset_remote_worker.py --serial "$(cat /var/lib/asset-agent/serial)"
```

---
## Windows

1. **Build the worker exe** on Windows (PyInstaller cannot cross-compile):
   ```
   pip install pyinstaller pillow websockets requests mss pynput
   pyinstaller --onefile --noconsole --name RemoteAssistWorker ^
     --hidden-import pynput.keyboard._win32 ^
     --hidden-import pynput.mouse._win32 ^
     asset_remote_worker.py
   ```
2. **Add to your Inno Setup installer** — see `agent/autostart/inno_snippet.iss`.
   It ships `RemoteAssistWorker.exe`, writes `remote.json` to
   `C:\ProgramData\AssetAgent\`, and adds an `HKLM…\Run` entry so the worker
   starts in each user's session at logon.
3. The worker reads its serial via `wmic bios get serialnumber` (works as a
   normal user), and the Odoo URL/DB from
   `C:\ProgramData\AssetAgent\remote.json`.

Windows needs no Xorg/Wayland handling — capture works in the user session
directly.

---
## Flow (both OSes)

1. Admin: open the asset in Odoo → **Connect Remote** (header button) — or
   select assets in the list and use the same button.
2. The worker's next poll returns the request → **Accept / Reject** on the
   machine.
3. Reject → status *Rejected*. Accept → the machine shares; status →
   *Agent Connected*.
4. Admin: **Asset Menu ▸ Remote Sessions** → open the session →
   **Start Remote Session** → browser viewer with view + control.
5. Either side ends it; the worker returns to waiting.

## Relay

Run the dedicated relay (separate from any other) on port 8766, and set the
Odoo parameter `asset_remote.relay_url`:
```
cd relay && python3 relay.py --host 0.0.0.0 --port 8766
# Odoo: asset_remote.relay_url = ws://<relay-host>:8766   (wss:// in prod)
```

## Limits / notes

- Consent is mandatory and happens on the machine every session.
- One active session per asset; the relay pairs one host ↔ one viewer.
- Over HTTPS Odoo, use `wss://` for the relay (browsers block `ws://` on https).
- Wayland shows a one-time "share your screen" system dialog (needs the
  GStreamer packages above). X11/Windows use mss+pynput. **The Wayland path is
  still experimental — validate on your hardware.**
- macOS deferred: it additionally requires the user to grant Screen Recording +
  Accessibility (TCC) to the worker; not wired up yet.
