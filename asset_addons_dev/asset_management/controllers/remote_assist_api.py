# -*- coding: utf-8 -*-
import json
import logging

from odoo import http, _
from odoo.http import request

_logger = logging.getLogger(__name__)


def _json(data, status=200):
    return request.make_response(
        json.dumps(data), status=status,
        headers=[("Content-Type", "application/json")])


def _body():
    try:
        return json.loads(request.httprequest.data or "{}")
    except Exception:
        return dict(request.params)


class AssetRemoteController(http.Controller):

    # ---- agent worker polls by serial number -------------------------- #
    @http.route("/api/asset/remote/poll", type="http", auth="public",
                methods=["POST", "GET"], csrf=False)
    def poll(self, **kw):
        data = _body()
        serial = (data.get("serial_number") or "").strip()
        if not serial:
            return _json({"command": "idle", "error": "serial_number required"})
        asset = request.env["asset.asset"].sudo().search(
            [("serial_number", "=", serial)], limit=1)
        if not asset:
            return _json({"command": "idle", "error": "unknown serial"})
        session = request.env["asset.remote.session"].sudo().search([
            ("asset_id", "=", asset.id),
            ("state", "in", ("requested", "accepted", "agent_connected",
                             "connected"))], order="create_date desc", limit=1)
        if not session:
            return _json({"command": "idle"})
        payload = {
            "token": session.session_token,
            "relay_url": session.relay_url,
            "company": session.company_id.name,
            "admin": session.admin_user_id.name,
            "minutes": session.duration_minutes,
            "session": session.name,
            "state": session.state,
            "session_type": session.session_type,
            # Terminal sessions are created already 'accepted' (see
            # action_request_ssh_terminal()) specifically so they never hit
            # the consent branch here - "command" only ever comes back
            # "consent" for a screen-share session still in 'requested'.
            "command": "consent" if session.state == "requested" else "connect",
        }
        return _json(payload)

    @http.route("/api/asset/remote/consent", type="http", auth="public",
                methods=["POST"], csrf=False)
    def consent(self, **kw):
        data = _body()
        session = request.env["asset.remote.session"].sudo()._for_token(
            data.get("token"))
        if not session:
            return _json({"ok": False, "error": "unknown token"}, status=404)
        decision = "accept" if data.get("decision") == "accept" else "reject"
        session._consent(decision)
        return _json({"ok": True, "state": session.state})

    @http.route("/api/asset/remote/status", type="http", auth="public",
                methods=["POST"], csrf=False)
    def status(self, **kw):
        data = _body()
        session = request.env["asset.remote.session"].sudo()._for_token(
            data.get("token"))
        if not session:
            return _json({"ok": False, "error": "unknown token"}, status=404)
        session._agent_report(data.get("status", ""), os_name=data.get("os"),
                              note=data.get("note"))
        return _json({"ok": True, "state": session.state})

    @http.route("/api/asset/remote/session_poll", type="http", auth="public",
                methods=["POST"], csrf=False)
    def session_poll(self, **kw):
        data = _body()
        session = request.env["asset.remote.session"].sudo()._for_token(
            data.get("token"))
        if not session or session.state in ("ended", "expired", "rejected"):
            return _json({"command": "end"})
        return _json({"command": "connect"})

    @http.route("/api/asset/remote/verify_token", type="http", auth="public",
                methods=["POST"], csrf=False)
    def verify_token(self, **kw):
        session = request.env["asset.remote.session"].sudo()._for_token(
            _body().get("token"))
        return _json({"valid": bool(session and session._token_is_live())})

    # ---- admin viewer ------------------------------------------------- #
    @http.route("/asset_remote/viewer/<string:token>", type="http",
                auth="user", methods=["GET"], csrf=False)
    def viewer(self, token, **kw):
        session = request.env["asset.remote.session"].sudo()._for_token(token)
        if not session:
            return request.not_found()
        user = request.env.user
        if session.admin_user_id.id != user.id and not user.has_group(
                "base.group_system"):
            return request.make_response(
                _("You are not the administrator for this session."),
                headers=[("Content-Type", "text/plain")])
        if session.session_type == "terminal":
            return _terminal_viewer_html(session)
        return _viewer_html(session)


def _viewer_html(session):
    customer = session.asset_id.display_name or _("Asset")
    vals = {
        "token": session.session_token, "relay": session.relay_url,
        "customer": customer,
        "js": "/asset_management/static/src/js/remote_viewer.js",
        "css": "/asset_management/static/src/css/remote_viewer.css",
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
    return request.make_response(html, headers=[("Content-Type",
                                                 "text/html")])


def _terminal_viewer_html(session):
    customer = session.asset_id.display_name or _("Asset")
    vals = {
        "token": session.session_token, "relay": session.relay_url,
        "customer": customer,
        "js": "/asset_management/static/src/js/ssh_terminal_viewer.js",
        "css": "/asset_management/static/src/css/ssh_terminal_viewer.css",
    }
    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SSH Terminal — %(customer)s</title>
<link rel="stylesheet" href="%(css)s">
</head>
<body data-token="%(token)s" data-relay="%(relay)s" data-customer="%(customer)s">
  <div id="ra-toolbar">
    <span id="ra-dot" class="ra-dot ra-warn"></span>
    <span id="ra-title">%(customer)s</span>
    <span id="ra-status">initialising…</span>
    <span class="ra-spacer"></span>
    <button id="ra-end" class="ra-btn ra-danger">End Session</button>
  </div>
  <div id="ra-stage">
    <pre id="ra-term"></pre>
    <div id="ra-overlay"><div id="ra-overlay-text">Connecting…</div></div>
  </div>
  <script src="%(js)s"></script>
</body></html>""" % vals
    return request.make_response(html, headers=[("Content-Type",
                                                 "text/html")])
