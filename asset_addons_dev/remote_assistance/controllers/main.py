# -*- coding: utf-8 -*-
import base64
import io
import json
import logging
import os
import zipfile

from odoo import http, _, fields
from odoo.http import request, Response
from odoo.tools import file_open

_logger = logging.getLogger(__name__)


def _json(data, status=200):
    return Response(json.dumps(data), status=status,
                    content_type="application/json")


class RemoteAssistanceController(http.Controller):

    # =================================================================== #
    # Agent-facing JSON API (called by the temporary agent via requests)
    # =================================================================== #
    @http.route("/api/remote/agent/poll", type="http", auth="public",
                methods=["POST"], csrf=False)
    def agent_poll(self, **kw):
        payload = _read_json_body()
        token = payload.get("token")
        session = request.env["remote.assistance.session"].sudo(
        )._session_for_token(token)
        if not session:
            return _json({"ok": False, "command": "end",
                          "error": "unknown token"}, status=404)
        if session.state in ("ended", "expired", "rejected"):
            return _json({"ok": True, "command": "end"})
        # Any state from acceptance onward: give the agent what it needs to
        # connect and wait.
        return _json({
            "ok": True,
            "command": "connect",
            "relay_url": session.relay_url,
            "company": session.company_id.name,
            "customer": session.partner_id.name or "",
            "minutes": session.duration_minutes,
            "session": session.name,
            "state": session.state,
        })

    @http.route("/api/remote/agent/status", type="http", auth="public",
                methods=["POST"], csrf=False)
    def agent_status(self, **kw):
        payload = _read_json_body()
        token = payload.get("token")
        session = request.env["remote.assistance.session"].sudo(
        )._session_for_token(token)
        if not session:
            return _json({"ok": False, "error": "unknown token"}, status=404)
        session._agent_report(
            payload.get("status", ""),
            os_name=payload.get("os"),
            note=payload.get("note"))
        return _json({"ok": True, "state": session.state})

    @http.route("/api/remote/verify_token", type="http", auth="public",
                methods=["POST"], csrf=False)
    def verify_token(self, **kw):
        """Optional relay-side check: is this token allowed to open a room?"""
        payload = _read_json_body()
        session = request.env["remote.assistance.session"].sudo(
        )._session_for_token(payload.get("token"))
        valid = bool(session and session._token_is_live())
        return _json({"valid": valid})

    # =================================================================== #
    # Public consent pages (no login) — Steps 2 & 3
    # =================================================================== #
    @http.route("/remote/invite/<string:token>", type="http", auth="public",
                methods=["GET"], csrf=False, website=False)
    def invite_page(self, token, **kw):
        session = request.env["remote.assistance.session"].sudo(
        )._session_for_token(token)
        if not session:
            return _page(_("Invalid link"),
                         _("This remote assistance link is not valid."))
        if session.state in ("ended", "expired"):
            return _page(_("Link expired"),
                         _("This invitation has expired. Please ask for a "
                           "new one."))
        if session.state == "rejected":
            return _page(_("Already declined"),
                         _("You have declined this invitation."))
        if session.state not in ("sent", "waiting_response"):
            # Already accepted — send them to the download page.
            return request.redirect("/remote/download/%s" % token)
        return _invite_html(session)

    @http.route("/remote/invite/<string:token>/accept", type="http",
                auth="public", methods=["GET", "POST"], csrf=False)
    def invite_accept(self, token, **kw):
        session = request.env["remote.assistance.session"].sudo(
        )._session_for_token(token)
        if not session or session.state not in ("sent", "waiting_response"):
            return request.redirect("/remote/download/%s" % token
                                    if session else "/remote/invalid")
        session._mark_accepted()
        return request.redirect("/remote/download/%s" % token)

    @http.route("/remote/invite/<string:token>/reject", type="http",
                auth="public", methods=["GET", "POST"], csrf=False)
    def invite_reject(self, token, **kw):
        session = request.env["remote.assistance.session"].sudo(
        )._session_for_token(token)
        if session and session.state in ("sent", "waiting_response"):
            session._mark_rejected()
        return _page(_("Invitation declined"),
                     _("You have declined the remote assistance request. "
                       "No connection will be made. You can close this page."))

    @http.route("/remote/invalid", type="http", auth="public")
    def invalid(self, **kw):
        return _page(_("Invalid link"),
                     _("This remote assistance link is not valid."))

    # =================================================================== #
    # Download page + personalized agent package — Step 4
    # =================================================================== #
    @http.route("/remote/download/<string:token>", type="http",
                auth="public", methods=["GET"], csrf=False)
    def download_page(self, token, **kw):
        session = request.env["remote.assistance.session"].sudo(
        )._session_for_token(token)
        if not session:
            return _page(_("Invalid link"),
                         _("This remote assistance link is not valid."))
        if session.state in ("sent", "waiting_response"):
            return request.redirect("/remote/invite/%s" % token)
        if session.state in ("ended", "expired", "rejected"):
            return _page(_("Session closed"),
                         _("This remote assistance session is closed."))
        return _download_html(session)

    @http.route("/remote/status/<string:token>", type="http", auth="public",
                methods=["GET"], csrf=False)
    def public_status(self, token, **kw):
        """Polled by the download page so it can reflect 'agent connected'."""
        session = request.env["remote.assistance.session"].sudo(
        )._session_for_token(token)
        if not session:
            return _json({"state": "invalid"})
        return _json({"state": session.state,
                      "label": dict(session._fields["state"].selection).get(
                          session.state, session.state)})

    @http.route("/remote/agent-package/<string:token>", type="http",
                auth="public", methods=["GET"], csrf=False)
    def agent_package(self, token, **kw):
        session = request.env["remote.assistance.session"].sudo(
        )._session_for_token(token)
        if not session or session.state in ("ended", "expired", "rejected"):
            return _page(_("Session closed"),
                         _("This remote assistance session is closed."))
        # Move the workflow forward: customer is fetching the agent.
        session._mark_agent_downloading()

        # Build a personalized portable package (zip) on the fly.
        relay = session.relay_url
        odoo = session._base_url()
        cfg = {
            "token": session.session_token,
            "relay": relay,
            "odoo": odoo,
            "company": session.company_id.name or "the administrator",
            "minutes": session.duration_minutes,
            "session": session.name,
        }
        db = request.env.cr.dbname
        if db:
            cfg["db"] = db

        session_json = json.dumps(cfg, indent=2)
        company, minutes = cfg["company"], cfg["minutes"]

        # ---- prefer a prebuilt binary if one has been hosted -------------
        # Set the system parameter `remote_assistance.agent_binary_dir` to a
        # server directory containing:
        #     RemoteAssist.exe   (Windows, PyInstaller --onefile)
        #     RemoteAssist       (Linux onefile — best for a temporary run)
        #   and/or RemoteAssist.deb  (Linux installer)
        binary_dir = request.env["ir.config_parameter"].sudo().get_param(
            "remote_assistance.agent_binary_dir", "").strip()
        mt = session.machine_type

        # ---- SINGLE-FILE mode -------------------------------------------
        # If a PyInstaller one-file binary has been hosted, serve it as ONE
        # double-click file with the session token embedded in the filename
        # (RemoteAssist~<token>.exe / RemoteAssist~<token>). The relay/Odoo
        # address is baked into the binary at build time, so no session.json
        # and no zip are needed. Rebuild the binary if the relay IP changes.
        if binary_dir and os.path.isdir(binary_dir):
            single = sext = sctype = None
            if mt == "windows":
                c = os.path.join(binary_dir, "RemoteAssist.exe")
                if os.path.isfile(c):
                    single, sext = c, ".exe"
                    sctype = "application/vnd.microsoft.portable-executable"
            else:
                c = os.path.join(binary_dir, "RemoteAssist")
                if os.path.isfile(c):
                    single, sext, sctype = c, "", "application/octet-stream"
            if single:
                with open(single, "rb") as fh:
                    data = fh.read()
                fname = "RemoteAssist~%s%s" % (session.session_token, sext)
                return request.make_response(data, headers=[
                    ("Content-Type", sctype),
                    ("Content-Disposition",
                     'attachment; filename="%s"' % fname),
                    ("Content-Length", len(data)),
                ])

        binary_path = binary_arc = launcher_name = launcher_body = None
        if binary_dir and os.path.isdir(binary_dir):
            if mt == "windows":
                cand = os.path.join(binary_dir, "RemoteAssist.exe")
                if os.path.isfile(cand):
                    binary_path, binary_arc = cand, "RemoteAssist.exe"
                    launcher_name, launcher_body = "run.bat", (
                        "@echo off\r\ncd /d \"%~dp0\"\r\n"
                        "start \"\" RemoteAssist.exe\r\n")
            else:  # ubuntu / linux
                one = os.path.join(binary_dir, "RemoteAssist")
                deb = os.path.join(binary_dir, "RemoteAssist.deb")
                if os.path.isfile(one):
                    binary_path, binary_arc = one, "RemoteAssist"
                    launcher_name, launcher_body = "run.sh", (
                        "#!/usr/bin/env bash\ncd \"$(dirname \"$0\")\"\n"
                        "chmod +x ./RemoteAssist\n"
                        "./RemoteAssist --config ./session.json\n")
                elif os.path.isfile(deb):
                    binary_path, binary_arc = deb, "RemoteAssist.deb"
                    launcher_name, launcher_body = "run.sh", (
                        "#!/usr/bin/env bash\ncd \"$(dirname \"$0\")\"\n"
                        "echo 'Installing Remote Assist (needs your "
                        "password)...'\n"
                        "sudo apt-get install -y ./RemoteAssist.deb || "
                        "sudo dpkg -i ./RemoteAssist.deb\n"
                        "RemoteAssist --config \"$(pwd)/session.json\"\n")

        buf = io.BytesIO()
        if binary_path:
            with open(binary_path, "rb") as fh:
                binary_bytes = fh.read()
            readme = (
                "Remote Assistance - Temporary Agent\n"
                "===================================\n\n"
                "One-time agent for a single support session with %s.\n"
                "No Python or install required.\n\n"
                "  Windows:  double-click RemoteAssist.exe (or run.bat)\n"
                "  Ubuntu:   in a terminal, run:  bash run.sh\n\n"
                "A small window shows you are connected, with a timer and a\n"
                "'Stop Sharing' button. The session ends automatically after\n"
                "%s minutes. Nothing permanent is left on your computer.\n"
            ) % (company, minutes)
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zi = zipfile.ZipInfo(binary_arc)
                zi.external_attr = 0o755 << 16          # executable bit
                zf.writestr(zi, binary_bytes)
                zf.writestr("session.json", session_json)
                if launcher_name:
                    li = zipfile.ZipInfo(launcher_name)
                    li.external_attr = 0o755 << 16
                    zf.writestr(li, launcher_body)
                zf.writestr("README.txt", readme)
        else:
            # ---- fallback: portable Python package -----------------------
            # Odoo 19: odoo.modules.module.get_module_resource was removed.
            # odoo.tools.file_open resolves a path relative to the addons
            # paths and refuses to escape them.
            agent_src = ""
            try:
                with file_open("remote_assistance/agent_src/"
                               "remote_assist_portable.py",
                               "r") as fh:
                    agent_src = fh.read()
            except (FileNotFoundError, OSError):
                _logger.error("Portable agent source is missing from the "
                              "module; the downloaded package will not run.")
            run_sh = (
                "#!/usr/bin/env bash\n"
                "cd \"$(dirname \"$0\")\"\n"
                "PY=\"\"\n"
                "for c in python3.12 python3.11 python3.10 python3; do\n"
                "  command -v \"$c\" >/dev/null 2>&1 && { PY=\"$c\"; break; }\n"
                "done\n"
                "if [ -z \"$PY\" ]; then echo 'Python 3 is required "
                "(python.org).'; read -p 'Press Enter...'; exit 1; fi\n"
                "echo \"Using $PY\"\n"
                "$PY -c 'import tkinter' >/dev/null 2>&1 || "
                "sudo apt-get install -y python3-tk || true\n"
                "$PY -m pip install --user --break-system-packages "
                "mss pillow pynput websockets requests >/dev/null 2>&1 || "
                "$PY -m pip install --user mss pillow pynput websockets "
                "requests || true\n"
                "echo 'Starting Remote Assistance...'\n"
                "$PY remote_assist_portable.py\n"
            )
            run_bat = (
                "@echo off\r\n"
                "cd /d \"%~dp0\"\r\n"
                "echo Installing Remote Assistance dependencies...\r\n"
                "python -m pip install mss pillow pynput websockets requests\r\n"
                "echo Starting Remote Assistance...\r\n"
                "python remote_assist_portable.py\r\n"
                "pause\r\n"
            )
            readme = (
                "Remote Assistance - Temporary Agent (portable)\n"
                "==============================================\n\n"
                "One-time agent for a session with %s. Requires Python 3.8+.\n\n"
                "  Windows:  double-click run.bat\n"
                "  Ubuntu:   run in a terminal:  bash run.sh\n\n"
                "Ends automatically after %s minutes. Installs nothing.\n"
            ) % (company, minutes)
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("remote_assist_portable.py", agent_src)
                zf.writestr("session.json", session_json)
                zf.writestr("run.sh", run_sh)
                zf.writestr("run.bat", run_bat)
                zf.writestr("README.txt", readme)

        data = buf.getvalue()
        fname = "RemoteAssist-%s.zip" % (session.name or "session").replace(
            "/", "-")
        return request.make_response(data, headers=[
            ("Content-Type", "application/zip"),
            ("Content-Disposition", 'attachment; filename="%s"' % fname),
            ("Content-Length", len(data)),
        ])

    # =================================================================== #
    # Admin viewer — Step 7  (login required, ownership enforced)
    # =================================================================== #
    @http.route("/remote_assistance/viewer/<string:token>", type="http",
                auth="user", methods=["GET"], csrf=False)
    def viewer(self, token, **kw):
        session = request.env["remote.assistance.session"].sudo(
        )._session_for_token(token)
        if not session:
            return request.not_found()
        user = request.env.user
        if session.admin_user_id.id != user.id and not user.has_group(
                "base.group_system"):
            return _page(_("Not allowed"),
                         _("You are not the administrator for this session."))
        return _viewer_html(session)

    # =================================================================== #
    # Recording upload (from the viewer's MediaRecorder)
    # =================================================================== #
    @http.route("/remote_assistance/recording/upload/<string:token>",
                type="http", auth="user", methods=["POST"], csrf=False)
    def recording_upload(self, token, **kw):
        session = request.env["remote.assistance.session"].sudo(
        )._session_for_token(token)
        if not session:
            return _json({"ok": False, "error": "unknown token"}, status=404)
        user = request.env.user
        if (session.admin_user_id.id != user.id
                and not user.has_group("base.group_system")):
            return _json({"ok": False, "error": "forbidden"}, status=403)
        upload = request.httprequest.files.get("file")
        if not upload:
            return _json({"ok": False, "error": "no file"}, status=400)
        data = upload.read()
        if not data:
            return _json({"ok": False, "error": "empty"}, status=400)
        stamp = fields.Datetime.now().strftime("%Y%m%d-%H%M%S")
        fname = "%s-%s.webm" % (session.name or "session", stamp)
        att = request.env["ir.attachment"].sudo().create({
            "name": fname,
            "datas": base64.b64encode(data),
            "mimetype": "video/webm",
            "res_model": "remote.assistance.session",
            "res_id": session.id,
        })
        try:
            duration = float(kw.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        rec = request.env["remote.assistance.recording"].sudo().create({
            "name": fname,
            "session_id": session.id,
            "attachment_id": att.id,
            "duration": duration,
            "recorded_by": user.id,
        })
        session.sudo().message_post(
            body=_("Screen recording saved (%s).", fname))
        return _json({"ok": True, "id": rec.id})
def _read_json_body():
    try:
        raw = request.httprequest.get_data(as_text=True) or "{}"
        return json.loads(raw)
    except Exception:
        # Fall back to form params.
        return dict(request.params)


def _page(title, message):
    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title>
<style>
 body{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;
   background:#0b1020;color:#e5e7eb;display:grid;place-items:center;
   min-height:100vh}
 .card{max-width:460px;background:#141b2e;border:1px solid #24304d;
   border-radius:16px;padding:32px;text-align:center}
 h1{font-size:20px;margin:0 0 12px}
 p{color:#9fb0cd;line-height:1.5}
</style></head><body><div class="card"><h1>%(title)s</h1>
<p>%(message)s</p></div></body></html>""" % {"title": title,
                                             "message": message}
    return Response(html, content_type="text/html")


def _invite_html(session):
    accept = "/remote/invite/%s/accept" % session.session_token
    reject = "/remote/invite/%s/reject" % session.session_token
    vals = {
        "company": session.company_id.name or _("The administrator"),
        "reason": session.reason or "",
        "minutes": session.duration_minutes,
        "accept": accept,
        "reject": reject,
        "customer": session.partner_id.name or "",
    }
    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Remote Assistance Request</title>
<style>
 body{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;
   background:#0b1020;color:#e5e7eb;display:grid;place-items:center;
   min-height:100vh;padding:20px}
 .card{max-width:520px;width:100%%;background:#141b2e;border:1px solid #24304d;
   border-radius:18px;padding:34px;box-shadow:0 20px 60px rgba(0,0,0,.45)}
 .badge{display:inline-block;font-size:12px;letter-spacing:.08em;
   text-transform:uppercase;color:#7dd3fc;background:#0e2233;
   border:1px solid #164a63;padding:5px 10px;border-radius:999px}
 h1{font-size:22px;margin:16px 0 6px}
 .who{color:#93c5fd;font-weight:600}
 .row{margin:18px 0;padding:14px 16px;background:#0e1526;border-radius:12px;
   border:1px solid #1f2a44}
 .row .k{font-size:12px;color:#8296b8;text-transform:uppercase;
   letter-spacing:.06em}
 .row .v{margin-top:4px;color:#e5e7eb}
 .btns{display:flex;gap:12px;margin-top:26px}
 a.btn{flex:1;text-align:center;padding:14px 16px;border-radius:12px;
   text-decoration:none;font-weight:600}
 .accept{background:#16a34a;color:#fff}
 .reject{background:#334155;color:#e5e7eb}
 .fine{margin-top:18px;font-size:12px;color:#7286a6;line-height:1.5}
</style></head><body>
<div class="card">
  <span class="badge">Remote Assistance</span>
  <h1><span class="who">%(company)s</span> has requested permission to
      remotely assist your computer.</h1>
  <div class="row"><div class="k">Reason</div>
    <div class="v">%(reason)s</div></div>
  <div class="row"><div class="k">Requested Duration</div>
    <div class="v">%(minutes)s Minutes</div></div>
  <div class="btns">
    <a class="btn accept" href="%(accept)s">Accept</a>
    <a class="btn reject" href="%(reject)s">Reject</a>
  </div>
  <p class="fine">If you accept, you will be asked to download and run a small
  one-time program so the administrator can see your screen. You can stop the
  session at any time, and nothing is left installed afterwards.</p>
</div></body></html>""" % vals
    return Response(html, content_type="text/html")


def _download_html(session):
    token = session.session_token
    os_label = dict(session._fields["machine_type"].selection).get(
        session.machine_type, session.machine_type)
    binary_hint = ("RemoteAssist.deb" if session.machine_type == "ubuntu"
                   else "RemoteAssist.exe")
    vals = {
        "company": session.company_id.name or _("The administrator"),
        "os_label": os_label,
        "binary_hint": binary_hint,
        "pkg": "/remote/agent-package/%s" % token,
        "status": "/remote/status/%s" % token,
    }
    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Download Remote Assistance Agent</title>
<style>
 body{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;
   background:#0b1020;color:#e5e7eb;display:grid;place-items:center;
   min-height:100vh;padding:20px}
 .card{max-width:540px;width:100%%;background:#141b2e;border:1px solid #24304d;
   border-radius:18px;padding:34px}
 h1{font-size:22px;margin:0 0 6px}
 p{color:#9fb0cd;line-height:1.55}
 .step{margin:14px 0;padding:14px 16px;background:#0e1526;border-radius:12px;
   border:1px solid #1f2a44}
 .n{display:inline-block;width:24px;height:24px;border-radius:50%%;
   background:#1d4ed8;color:#fff;text-align:center;line-height:24px;
   font-size:13px;margin-right:8px}
 a.dl{display:inline-block;margin-top:8px;background:#2563eb;color:#fff;
   text-decoration:none;padding:14px 22px;border-radius:12px;font-weight:600}
 .status{margin-top:22px;padding:14px 16px;border-radius:12px;
   background:#0e2233;border:1px solid #164a63;color:#7dd3fc;font-weight:600}
 .dot{display:inline-block;width:9px;height:9px;border-radius:50%%;
   background:#38bdf8;margin-right:8px;animation:pulse 1.4s infinite}
 @keyframes pulse{0%%,100%%{opacity:.35}50%%{opacity:1}}
 code{background:#0b1220;padding:2px 6px;border-radius:6px;color:#c7d2fe}
</style></head><body>
<div class="card">
  <h1>You accepted — one more step</h1>
  <p>To let <b>%(company)s</b> assist you, download and run the temporary
     Remote Assistance agent for your computer (%(os_label)s).</p>

  <div class="step"><span class="n">1</span>Download the agent package.
     <br><a class="dl" href="%(pkg)s">Download for %(os_label)s</a></div>
  <div class="step"><span class="n">2</span>Unzip it, then run
     <code>run.bat</code> (Windows) or <code>bash run.sh</code> (Ubuntu).
     A prebuilt <code>%(binary_hint)s</code> may also be provided by your
     administrator.</div>
  <div class="step"><span class="n">3</span>A small window will show you are
     connected, with a timer and <b>Stop Sharing</b>. Keep it open during the
     session.</div>

  <div class="status"><span class="dot"></span>
     <span id="st">Waiting for you to run the agent…</span></div>

  <p style="margin-top:18px;font-size:12px;color:#7286a6">This agent runs only
     for this session and installs nothing permanent. You can close it (Stop
     Sharing) at any time.</p>
</div>
<script>
 var STATUS="%(status)s";
 function poll(){
   fetch(STATUS).then(function(r){return r.json();}).then(function(d){
     var el=document.getElementById("st");
     if(d.state==="agent_connected"){el.textContent=
        "Agent connected. Waiting for the administrator to start…";}
     else if(d.state==="preparing"){el.textContent=
        "The administrator is starting the session…";}
     else if(d.state==="connected"){el.textContent=
        "Connected. Your screen is being shared.";}
     else if(d.state==="ended"||d.state==="expired"){el.textContent=
        "The session has ended.";}
     else if(d.label){el.textContent=d.label;}
   }).catch(function(){});
 }
 setInterval(poll,3000);poll();
</script>
</body></html>""" % vals
    return Response(html, content_type="text/html")


def _viewer_html(session):
    token = session.session_token
    relay = session.relay_url
    customer = (session.partner_id.name or session.customer_email or
                _("Customer"))
    js_url = "/remote_assistance/static/src/js/remote_viewer.js"
    css_url = "/remote_assistance/static/src/css/remote_viewer.css"
    vals = {
        "token": token, "relay": relay, "customer": customer,
        "js": js_url, "css": css_url,
        "duration": session.duration_minutes,
    }
    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Remote Desktop — %(customer)s</title>
<link rel="stylesheet" href="%(css)s">
</head>
<body data-token="%(token)s" data-relay="%(relay)s" data-customer="%(customer)s">
  <div id="ra-topbar">
    <div class="ra-left">
      <span id="ra-dot" class="ra-dot ra-warn"></span>
      <span id="ra-title">%(customer)s</span>
      <span id="ra-status">initialising…</span>
    </div>
    <div class="ra-right">
      <span id="ra-timer" class="ra-meter">00:00</span>
      <span id="ra-quality" class="ra-meter">– fps</span>
      <button id="ra-rec" class="ra-btn">● Record</button>
      <button id="ra-fit" class="ra-btn">Fit</button>
      <button id="ra-end" class="ra-btn ra-danger">End Session</button>
    </div>
  </div>
  <div id="ra-stage">
    <canvas id="ra-canvas"></canvas>
    <div id="ra-overlay"><div id="ra-overlay-text">Connecting…</div></div>
  </div>
  <script src="%(js)s"></script>
</body></html>""" % vals
    return Response(html, content_type="text/html")
