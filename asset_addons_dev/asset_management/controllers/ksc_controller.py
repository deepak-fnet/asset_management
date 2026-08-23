"""
Odoo HTTP controller for the Kaspersky Security Center (KSC) deploy integration.

Exposes JSON endpoints:
  POST /api/antivirus/ksc/test       — test KSC connectivity
  POST /api/antivirus/ksc/deploy     — trigger remote installation task(s)
  GET  /api/antivirus/ksc/status/<task_id> — poll task status
"""
import json
import logging

from odoo import http
from odoo.http import request

from ..models.ksc_service import KasperskySecurityCenterService, KSCError

_logger = logging.getLogger(__name__)


def _get_ksc_service():
    """
    Build a KasperskySecurityCenterService from the singleton antivirus KSC config.
    Returns (svc, error_dict) — error_dict is None on success.
    """
    config = request.env["antivirus.ksc.config"].sudo().get_config()
    if not config:
        return None, {"ok": False, "message": "No antivirus configuration found. Please create one first."}

    if not config.ksc_url:
        return None, {"ok": False, "message": "KSC Server URL is not configured. Please set it in the Devices tab."}
    if not config.username or not config.password:
        return None, {"ok": False, "message": "KSC credentials (username/password) are not configured."}

    svc = KasperskySecurityCenterService(
        base_url=config.ksc_url,
        username=config.username,
        password=config.password,
        verify_ssl=config.verify_ssl,
    )
    return svc, None


class KSCController(http.Controller):

    @http.route("/api/antivirus/ksc/test", type='jsonrpc', auth="user", methods=["POST"], csrf=False)
    def test_connection(self, **kwargs):
        """Test connectivity to the KSC server."""
        svc, err = _get_ksc_service()
        if err:
            return err
        result = svc.test_connection()
        return result

    @http.route("/api/antivirus/ksc/deploy", type='jsonrpc', auth="user", methods=["POST"], csrf=False)
    def deploy(self, **kwargs):
        """
        Trigger a KSC remote installation task for one or more devices.
        """
        params = kwargs
        device_ids = params.get("device_ids") or []
        if not device_ids and params.get("device_id"):
            device_ids = [params["device_id"]]

        if not device_ids:
            return {"ok": False, "message": "No device IDs provided."}

        # Load config once
        config = request.env["antivirus.ksc.config"].sudo().get_config()
        if not config or not config.ksc_url:
            return {"ok": False, "message": "KSC not configured."}

        package_name = config.package_name or "Kaspersky Endpoint Security"

        # Fetch asset records
        assets = request.env["asset.asset"].sudo().browse(device_ids)

        svc = KasperskySecurityCenterService(
            base_url=config.ksc_url,
            username=config.username,
            password=config.password,
            verify_ssl=config.verify_ssl,
        )

        results = []
        for asset in assets:
            hostname = asset.asset_name or asset.asset_code or str(asset.id)
            result = {"device_id": asset.id, "hostname": hostname}
            try:
                task_id = svc.create_install_task(hostname, package_name)
                result["task_id"] = task_id
                result["status"] = "pending"
                _logger.info("KSC deploy task %s created for %s", task_id, hostname)
            except Exception as e:
                result["task_id"] = None
                result["error"] = str(e)
                result["status"] = "failed"
                _logger.error("KSC deploy failed for %s: %s", hostname, e)
            results.append(result)

        return {"ok": True, "results": results}

    @http.route("/api/antivirus/ksc/status/<string:task_id>", type='jsonrpc', auth="user", methods=["GET", "POST"], csrf=False)
    def task_status(self, task_id, **kwargs):
        """
        Poll the status of a KSC remote installation task.
        """
        svc, err = _get_ksc_service()
        if err:
            return err

        try:
            status = svc.get_task_status(task_id)
            return {"ok": True, **status}
        except Exception as e:
            return {"ok": False, "message": str(e), "status": "failed", "progress": 0}

    @http.route("/api/antivirus/ksc/config", type='jsonrpc', auth="user", methods=["POST"], csrf=False)
    def save_config(self, **kwargs):
        """
        Save KSC connection settings from the dashboard UI to the singleton model.
        """
        params = kwargs

        # Map frontend field names to backend model field names
        field_map = {
            "ksc_server_url": "ksc_url",
            "ksc_username": "username",
            "ksc_password": "password",
            "ksc_package_name": "package_name",
            "ksc_verify_ssl": "verify_ssl"
        }

        vals = {}
        for frontend_field, backend_field in field_map.items():
            if frontend_field in params:
                vals[backend_field] = params[frontend_field]

        if not vals:
            return {"ok": False, "message": "No fields to save."}

        config = request.env["antivirus.ksc.config"].sudo().get_config()
        if config:
            config.write(vals)
        else:
            request.env["antivirus.ksc.config"].sudo().create(vals)

        return {"ok": True, "message": "KSC configuration saved."}
