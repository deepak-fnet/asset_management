from odoo import http, fields, _
from odoo.http import request
import logging
import json
import os
import requests
from datetime import timezone
import uuid

_logger = logging.getLogger(__name__)


def reverse_geocode(lat, lon):
    """
    Convert GPS coordinates to human-readable address using Nominatim API.
    Free, no API key required.

    Args:
        lat (float): Latitude
        lon (float): Longitude

    Returns:
        dict: Address components or None if failed
    """
    try:
        url = "https://nominatim.openstreetmap.org/reverse"
        params = {
            'lat': lat,
            'lon': lon,
            'format': 'json',
            'addressdetails': 1,
            'zoom': 18  # Street level detail
        }
        headers = {
            'User-Agent': 'OdooAssetManagement/1.0 (asset-tracking)',
            'Accept-Language': 'en'  # Get results in English
        }

        response = requests.get(url, params=params, headers=headers, timeout=10)

        if response.status_code == 200:
            data = response.json()
            address = data.get('address', {})

            # Extract address components
            city = (address.get('city') or
                   address.get('town') or
                   address.get('village') or
                   address.get('municipality') or '')

            area = (address.get('suburb') or
                   address.get('neighbourhood') or
                   address.get('residential') or '')

            state = (address.get('state') or
                    address.get('province') or
                    address.get('region') or '')

            country = address.get('country', '')

            # Build full address
            full_address = data.get('display_name', '')

            _logger.info(f"Reverse geocoding successful: {city}, {state}, {country}")

            return {
                'address': full_address,
                'city': city,
                'state': state,
                'country': country,
                'area': area
            }
        else:
            _logger.warning(f"Reverse geocoding failed: HTTP {response.status_code}")

    except requests.exceptions.Timeout:
        _logger.warning("Reverse geocoding timed out")
    except Exception as e:
        _logger.warning(f"Reverse geocoding error: {e}")

    return None


class AssetAgentAPIController(http.Controller):
    """
    API endpoint for laptop monitoring agent.
    Receives hardware/software snapshots every 10 minutes.
    """

    def _normalize_platform(self, payload):
        """
        Normalize platform value from agent payload.
        Returns one of: 'windows', 'linux', 'macos', 'unknown'
        """
        platform = payload.get("platform", "").lower().strip()
        
        # Valid platforms
        if platform in ['windows', 'linux', 'macos']:
            return platform
        
        # Fallback detection from OS name
        os_name = payload.get("os_name", "").lower()
        
        if 'windows' in os_name:
            return 'windows'
        elif 'ubuntu' in os_name or 'linux' in os_name or 'debian' in os_name or 'centos' in os_name or 'rhel' in os_name:
            return 'linux'
        elif 'macos' in os_name or 'mac os' in os_name or 'darwin' in os_name:
            return 'macos'
        
        return 'unknown'

    def _parse_datetime(self, dt_str):
        """
        Normalize datetime string to Odoo format (%Y-%m-%d %H:%M:%S).
        Supports ISO 8601 and Odoo format.
        """
        if not dt_str:
            return fields.Datetime.now()

        try:
            # dateutil is a standard Odoo dependency
            from dateutil import parser
            dt = parser.parse(dt_str)
            # Odoo Datetime fields expect a string in UTC '%Y-%m-%d %H:%M:%S'
            if dt.tzinfo:
                dt = dt.astimezone(timezone.utc)
            return fields.Datetime.to_string(dt.replace(tzinfo=None))
        except Exception as e:
            _logger.warning(f"Failed to parse datetime '{dt_str}': {str(e)}. Using now().")
            return fields.Datetime.now()

    def _normalize_coordinates(self, payload):
        """
        Validate and pass through latitude/longitude from agent payload.

        TRUST agent data AS-IS. NO reverse geocoding, NO IP re-lookup, NO guessing.

        Expected payload fields from agent (supports both naming conventions):
        - location_latitude / latitude (Float)
        - location_longitude / longitude (Float)
        - location_source ("gps" / "ip" / "windows" / "unavailable")
        """
        # Support both naming conventions: "location_latitude" OR "latitude"
        lat = payload.get("location_latitude") or payload.get("latitude")
        lon = payload.get("location_longitude") or payload.get("longitude")
        location_source = payload.get("location_source", "unavailable")

        # Validate and convert coordinates
        if lat is not None and lon is not None:
            try:
                lat_float = float(lat)
                lon_float = float(lon)
                # Basic coordinate validation
                if -90 <= lat_float <= 90 and -180 <= lon_float <= 180:
                    payload["latitude"] = lat_float
                    payload["longitude"] = lon_float
                    _logger.info(f"[Controller] Coordinates: lat={lat_float}, lon={lon_float}, source={location_source}")
                else:
                    _logger.warning(f"[Controller] Invalid coordinate range: lat={lat_float}, lon={lon_float}")
                    payload["latitude"] = None
                    payload["longitude"] = None
                    payload["location_source"] = "unavailable"
            except (ValueError, TypeError) as e:
                _logger.warning(f"[Controller] Failed to convert coordinates: lat={lat}, lon={lon}, error={e}")
                payload["latitude"] = None
                payload["longitude"] = None
                payload["location_source"] = "unavailable"

    @http.route(
        '/api/laptop_monitor',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False
    )
    def laptop_monitor_sync(self, **kwargs):
        try:
            payload = json.loads(request.httprequest.data or "{}")

            serial_number = payload.get("serial_number")
            if not serial_number:
                return request.make_response(
                    json.dumps({"success": False, "message": "serial_number is required"}),
                    headers=[('Content-Type', 'application/json')]
                )

            platform = self._normalize_platform(payload)
            payload['platform'] = platform

            # Normalize coordinates from various agent formats to location_latitude/location_longitude
            self._normalize_coordinates(payload)

            public_ip = payload.get("public_ip") or request.httprequest.environ.get(
                'REMOTE_ADDR', 'Unknown'
            )

            local_ip = payload.get("local_ip")
            file_browser_port = payload.get("file_browser_port", 8000)
            connectivity_ip = f"{local_ip}:{file_browser_port}" if local_ip else public_ip

            payload.update({
                'public_ip': public_ip,
                'ip_address': connectivity_ip,
                'last_sync_time': fields.Datetime.now()
            })

            LiveMonitoring = request.env["asset.live.monitoring"].sudo()
            AssetModel = request.env["asset.asset"].sudo()

            # First sync the asset (creates it if it doesn't exist)
            result = AssetModel.sync_from_agent(payload)

            # Then update live metrics (now the asset exists)
            LiveMonitoring.update_live_metrics(serial_number, {
                "cpu_usage": payload.get("cpu_usage", 0.0),
                "memory_usage": payload.get("memory_usage", 0.0),
                "storage_usage": payload.get("storage_usage", 0.0),
                "battery_level": payload.get("battery_level", 0.0),
                "ip_address": connectivity_ip,
            })

            return request.make_response(
                json.dumps(result),
                headers=[('Content-Type', 'application/json')]
            )


        except Exception as e:
            request.env.cr.rollback()
            _logger.exception("Agent sync error")
            return request.make_response(
                json.dumps({"success": False, "message": str(e)}),
                headers=[('Content-Type', 'application/json')],
                status=500,
            )

    @http.route(
        '/api/laptop_monitor/live',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False
    )
    def laptop_monitor_live(self, **kwargs):
        """Live metrics endpoint for real-time monitoring."""
        try:
            payload = json.loads(request.httprequest.data or "{}")
            
            serial_number = payload.get("serial_number")
            if not serial_number:
                return request.make_response(
                    json.dumps({"status": "error", "message": "serial_number required"}),
                    headers=[('Content-Type', 'application/json')]
                )
            
            now = fields.Datetime.now()
            
            local_ip = payload.get("local_ip")
            file_browser_port = payload.get("file_browser_port", 8000)
            
            # Update live monitoring record
            LiveMonitoring = request.env["asset.live.monitoring"].sudo()
            monitoring_record = LiveMonitoring.search([("serial_number", "=", serial_number)], limit=1)
            
            if not monitoring_record:
                return request.make_response(
                    json.dumps({"status": "error", "message": "Asset not found"}),
                    headers=[('Content-Type', 'application/json')]
                )
            
            update_vals = {
                "cpu_usage_percent": payload.get("cpu_usage_percent", 0.0),
                "ram_usage_percent": payload.get("ram_usage_percent", 0.0),
                "disk_usage_percent": payload.get("disk_usage_percent", 0.0),
                "network_upload_mbps": payload.get("network_upload_mbps", 0.0),
                "network_download_mbps": payload.get("network_download_mbps", 0.0),
                "battery_percentage": payload.get("battery_percentage", 0.0),
                "heartbeat": now,
                "last_heartbeat": now
            }
            if local_ip:
                update_vals["ip_address"] = f"{local_ip}:{file_browser_port}"
                
            monitoring_record.write(update_vals)
            
            # Update asset record
            Asset = request.env["asset.asset"].sudo()
            asset = Asset.search([("serial_number", "=", serial_number)], limit=1)
            if asset:
                asset.write({
                    "last_sync_time": now,
                    "last_agent_sync": now
                })
            
            return request.make_response(
                json.dumps({"status": "ok"}),
                headers=[('Content-Type', 'application/json')]
            )
            
        except Exception as e:
            _logger.error(f"Live metrics error: {str(e)}", exc_info=True)
            return request.make_response(
                json.dumps({"status": "error", "message": str(e)}),
                headers=[('Content-Type', 'application/json')]
            )

    @http.route(
        '/api/live_monitoring/all',
        type='jsonrpc',
        auth='user',
        methods=['GET', 'POST']
    )
    def get_all_live_monitoring(self):
        try:
            LiveMonitoring = request.env["asset.live.monitoring"].sudo()
            return {
                "success": True,
                "data": LiveMonitoring.get_all_live_metrics()
            }
        except Exception as e:
            _logger.error(f"Error fetching live monitoring data: {str(e)}", exc_info=True)
            return {
                "success": False,
                "message": str(e)
            }

    @http.route(
        '/api/camera_monitor',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False
    )
    def camera_monitor_update(self, **kwargs):
        """
        Receive camera monitoring data from CCTV agent
        """
        try:
            payload = json.loads(request.httprequest.data or "{}")
            cameras_data = payload.get('cameras', [])
            
            Camera = request.env['asset.camera'].sudo()
            
            for camera_data in cameras_data:
                # Find existing camera by camera_code (primary) or IP address (fallback)
                camera_code = camera_data.get('camera_code')
                ip_address = camera_data.get('ip_address')
                
                existing_camera = False
                if camera_code:
                    existing_camera = Camera.search([('camera_code', '=', camera_code)], limit=1)
                
                if not existing_camera and ip_address:
                    existing_camera = Camera.search([('camera_ip', '=', ip_address)], limit=1)
                
                if existing_camera:
                    # Interpret ping_status from agent's is_online field
                    # (Do NOT calculate ping again - just interpret agent data)
                    is_online_value = camera_data.get('is_online', False)
                    ping_status = 'ok' if is_online_value else 'fail'
                    
                    # Extract ping/response time
                    ping_ms = camera_data.get('ping_ms')
                    if ping_ms is None:
                        ping_ms = camera_data.get('response_time')
                    if ping_ms is None:
                        ping_ms = 0.0

                    # Update existing camera
                    update_vals = {
                        'name': camera_data.get('name', existing_camera.name),
                        'location': camera_data.get('location', existing_camera.location),
                        'camera_model': camera_data.get('camera_model', existing_camera.camera_model),
                        'port': camera_data.get('port', 554),
                        'protocol': camera_data.get('protocol', 'rtsp'),
                        'resolution': camera_data.get('resolution', '1920x1080'),
                        'fps': camera_data.get('fps', 25),
                        'is_online': is_online_value,
                        'ping_status': ping_status,  # Diagnostic field from is_online
                        'ping_ms': ping_ms,          # Stored ping value
                        'status': camera_data.get('status', 'unknown'),
                        'stream_status': camera_data.get('stream_status', 'unavailable'),
                        'stream_message': camera_data.get('stream_message', ''),
                        'http_accessible': camera_data.get('http_accessible', False),
                        'http_message': camera_data.get('http_message', ''),
                        'is_recording': camera_data.get('is_recording', False),
                        'cctv_recording_status': camera_data.get('recording_status', 'unknown'),
                        'motion_detected': camera_data.get('motion_detected', False),
                        'storage_total_gb': camera_data.get('storage_total_gb', 0),
                        'storage_used_gb': camera_data.get('storage_used_gb', 0),
                        'storage_free_gb': camera_data.get('storage_free_gb', 0),
                        'agent_version': camera_data.get('agent_version', ''),
                        'agent_hostname': payload.get('agent_hostname', ''),
                        'last_check': self._parse_datetime(camera_data.get('last_check'))
                    }
                    
                    # Detect status changes and create events
                    if existing_camera.is_online != camera_data.get('is_online'):
                        if camera_data.get('is_online'):
                            # Camera came online
                            self._create_camera_event(existing_camera.id, 'online', 'Camera came online', 'info')
                            # TASK 4: Auto-resolve previous offline events
                            self._auto_resolve_offline_events(existing_camera.id)
                        else:
                            # Camera went offline
                            self._create_camera_event(existing_camera.id, 'offline', 'Camera went offline', 'critical')
                    
                    if camera_data.get('motion_detected') and not existing_camera.motion_detected:
                        # Motion detected
                        self._create_camera_event(existing_camera.id, 'motion', 'Motion detected', 'info')
                        update_vals['last_motion_time'] = fields.Datetime.now()
                    
                    existing_camera.write(update_vals)
                    
                else:
                    # Interpret ping_status from agent's is_online field for new cameras
                    is_online_value = camera_data.get('is_online', False)
                    ping_status = 'ok' if is_online_value else 'fail'
                    
                    # Extract ping/response time
                    ping_ms = camera_data.get('ping_ms')
                    if ping_ms is None:
                        ping_ms = camera_data.get('response_time')
                    if ping_ms is None:
                        ping_ms = 0.0

                    # Create new camera
                    Camera.create({
                        'name': camera_data.get('name', 'Unknown Camera'),
                        'camera_code': camera_data.get('camera_code', 'New'),
                        'camera_ip': camera_data.get('ip_address'),
                        'ip_address': camera_data.get('ip_address'),
                        'port': camera_data.get('port', 554),
                        'location': camera_data.get('location', ''),
                        'camera_model': camera_data.get('camera_model', 'Unknown'),
                        'protocol': camera_data.get('protocol', 'rtsp'),
                        'resolution': camera_data.get('resolution', '1920x1080'),
                        'fps': camera_data.get('fps', 25),
                        'is_online': is_online_value,
                        'ping_status': ping_status,  # Diagnostic field from is_online
                        'ping_ms': ping_ms,          # Stored ping value
                        'status': camera_data.get('status', 'unknown'),
                        'stream_status': camera_data.get('stream_status', 'unavailable'),
                        'is_recording': camera_data.get('is_recording', False),
                        'cctv_recording_status': camera_data.get('recording_status', 'unknown'),
                        'motion_detected': camera_data.get('motion_detected', False),
                        'agent_version': camera_data.get('agent_version', ''),
                        'agent_hostname': payload.get('agent_hostname', ''),
                        'last_check': self._parse_datetime(camera_data.get('last_check'))
                    })
            
            return request.make_response(
                json.dumps({'status': 'success', 'message': 'Camera data updated successfully'}),
                headers=[('Content-Type', 'application/json')]
            )
            
        except Exception as e:
            _logger.error(f"Error updating camera data: {str(e)}")
            return request.make_response(
                json.dumps({'status': 'error', 'message': str(e)}),
                headers=[('Content-Type', 'application/json')]
            )

    def _create_camera_event(self, camera_id, event_type, message, severity):
        """Helper to create camera events"""
        try:
            CameraEvent = request.env['camera.event'].sudo()
            CameraEvent.create({
                'camera_id': camera_id,
                'event_type': event_type,
                'event_message': message,
                'severity': severity,
                'event_time': fields.Datetime.now()
            })
        except:
            pass

    def _auto_resolve_offline_events(self, camera_id):
        """
        Auto-resolve previous offline events when camera comes back online.

        TASK 4: Alert Lifecycle - auto-resolve previous offline events.

        Args:
            camera_id (int): ID of the camera that came back online
        """
        try:
            CameraEvent = request.env['camera.event'].sudo()
            CameraEvent.auto_resolve_offline_events(camera_id)
        except Exception as e:
            _logger.warning(f"Failed to auto-resolve offline events: {e}")

    @http.route(
        '/api/agent/version',
        type='http',
        auth='public',
        methods=['GET'],
        csrf=False
    )
    def agent_version(self, **params):
        platform = params.get('platform', '').lower().strip()
        
        if not platform:
            user_agent = request.httprequest.headers.get('User-Agent', '').lower()
            
            if 'windows' in user_agent or 'win32' in user_agent or 'win64' in user_agent:
                platform = 'windows'
            elif 'linux' in user_agent or 'ubuntu' in user_agent or 'x11' in user_agent:
                platform = 'linux'
            elif 'mac' in user_agent or 'darwin' in user_agent or 'macintosh' in user_agent:
                platform = 'macos'
            else:
                platform = 'windows'
        
        base_url = request.httprequest.host_url.rstrip('/')
        download_urls = {
            'windows': f"{base_url}/downloads/AssetAgent_latest.exe",
            'linux': f"{base_url}/downloads/AssetAgent_ubuntu_latest.tar.gz",
            'macos': f"{base_url}/downloads/AssetAgent_macos_latest.pkg",
            'cctv': f"{base_url}/downloads/AssetAgent_cctv_latest.tar.gz",
        }
        
        download_url = download_urls.get(platform, download_urls['windows'])
        
        return request.make_response(
            json.dumps({
                "latest_version": "1.2.0",
                "download_url": download_url,
                "platform": platform
            }),
            headers=[('Content-Type', 'application/json')]
        )

    @http.route('/api/test', type='http', auth='public', methods=['GET'], csrf=False)
    def test_endpoint(self):
        """Simple test endpoint"""
        return request.make_response(
            json.dumps({"status": "ok", "message": "API test endpoint is working!"}),
            headers=[('Content-Type', 'application/json')]
        )

    @http.route('/downloads/<string:filename>', type='http', auth='public', methods=['GET'], csrf=False)
    def download_agent(self, filename, **kwargs):
        """Serve agent executable files from the downloads folder."""
        module_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        file_path = os.path.join(module_path, 'downloads', filename)

        if not os.path.isfile(file_path):
            return request.not_found()

        with open(file_path, 'rb') as f:
            file_data = f.read()

        return request.make_response(
            file_data,
            headers=[
                ('Content-Type', 'application/octet-stream'),
                ('Content-Disposition', f'attachment; filename="{filename}"'),
                ('Content-Length', str(len(file_data)))
            ]
        )

    # ========================================================================
    # WINDOWS UPDATE API ENDPOINTS
    # ========================================================================

    @http.route('/api/asset/updates/report', type='http', auth='public', methods=['POST'], csrf=False)
    def windows_update_report(self, **kwargs):
        """Agent reports available + installed Windows updates to Odoo."""
        try:
            payload = json.loads(request.httprequest.data or "{}")
            serial_number = (payload.get("serial_number") or "").strip()
            updates = payload.get("updates", [])
            installed_kbs = payload.get("installed_kbs", [])

            if not serial_number:
                return request.make_response(
                    json.dumps({"success": False, "message": "serial_number is required"}),
                    headers=[('Content-Type', 'application/json')]
                )

            Asset = request.env["asset.asset"].sudo()
            asset = Asset.search([("serial_number", "=", serial_number)], limit=1)

            if not asset:
                _logger.warning(f"[WU Report] Asset not found: {serial_number}")
                return request.make_response(
                    json.dumps({"success": False, "message": "Asset not found"}),
                    headers=[('Content-Type', 'application/json')]
                )

            WindowsUpdate = request.env["asset.windows.update"].sudo()
            created = 0
            skipped = 0
            marked_installed = 0

            for upd in updates:
                kb_number = (upd.get("kb_number") or "").strip()
                if not kb_number:
                    continue

                existing = WindowsUpdate.search([("asset_id", "=", asset.id), ("kb_number", "=", kb_number)], limit=1)

                if existing:
                    if existing.status == 'pending':
                        existing.write({"detected_date": fields.Date.today()})
                    skipped += 1
                else:
                    WindowsUpdate.create({
                        "asset_id": asset.id, "kb_number": kb_number,
                        "title": (upd.get("title") or kb_number)[:255],
                        "description": (upd.get("description") or "")[:500],
                        "severity": upd.get("severity") or "optional",
                        "size": upd.get("size") or "", "version": upd.get("version") or "",
                        "detected_date": fields.Date.today(), "status": "pending",
                    })
                    created += 1

            for kb_number in installed_kbs:
                kb_number = kb_number.strip()
                if not kb_number:
                    continue

                existing = WindowsUpdate.search([("asset_id", "=", asset.id), ("kb_number", "=", kb_number)], limit=1)

                if existing:
                    if existing.status not in ('installed', 'uninstalled'):
                        existing.write({"status": "installed", "action_date": fields.Datetime.now()})
                        marked_installed += 1
                else:
                    WindowsUpdate.create({
                        "asset_id": asset.id, "kb_number": kb_number,
                        "title": f"Windows Update {kb_number}", "severity": "optional",
                        "status": "installed", "detected_date": fields.Date.today(),
                        "action_date": fields.Datetime.now(),
                    })
                    marked_installed += 1

            return request.make_response(
                json.dumps({"success": True, "created": created, "skipped": skipped, "marked_installed": marked_installed}),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            _logger.error(f"[WU Report] Error: {e}", exc_info=True)
            return request.make_response(
                json.dumps({"success": False, "message": str(e)}),
                headers=[('Content-Type', 'application/json')]
            )

    @http.route('/api/asset/updates/instructions', type='http', auth='public', methods=['GET'], csrf=False)
    def windows_update_instructions(self, **kwargs):
        """Agent polls Odoo for admin instructions."""
        try:
            serial_number = request.httprequest.args.get("serial_number", "").strip()
            if not serial_number:
                return request.make_response(
                    json.dumps({"success": False, "message": "serial_number is required"}),
                    headers=[('Content-Type', 'application/json')]
                )

            Asset = request.env["asset.asset"].sudo()
            asset = Asset.search([("serial_number", "=", serial_number)], limit=1)
            if not asset:
                return request.make_response(
                    json.dumps({"success": True, "is_locked": False, "blocklist": [], "push_list": [], "uninstall_list": [], "cancel_list": []}),
                    headers=[('Content-Type', 'application/json')]
                )

            is_locked = bool(asset.windows_update_locked)
            WindowsUpdate = request.env["asset.windows.update"].sudo()

            blocked = WindowsUpdate.search([("asset_id", "=", asset.id), ("status", "=", "blocked")])
            blocklist = [u.kb_number for u in blocked if u.kb_number]

            push = WindowsUpdate.search([("asset_id", "=", asset.id), ("status", "=", "installing")])
            push_list = [u.kb_number for u in push if u.kb_number]

            uninstall = WindowsUpdate.search([("asset_id", "=", asset.id), ("status", "=", "uninstalling")])
            uninstall_list = [u.kb_number for u in uninstall if u.kb_number]

            cancel = WindowsUpdate.search([("asset_id", "=", asset.id), ("status", "=", "cancelling")])
            cancel_list = [u.kb_number for u in cancel if u.kb_number]

            return request.make_response(
                json.dumps({"success": True, "is_locked": is_locked, "blocklist": blocklist, "push_list": push_list, "uninstall_list": uninstall_list, "cancel_list": cancel_list}),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            _logger.error(f"[WU Instructions] Error: {e}", exc_info=True)
            return request.make_response(
                json.dumps({"success": False, "is_locked": False, "blocklist": [], "push_list": [], "uninstall_list": [], "cancel_list": []}),
                headers=[('Content-Type', 'application/json')]
            )

    @http.route('/api/asset/updates/result', type='http', auth='public', methods=['POST'], csrf=False)
    def windows_update_result(self, **kwargs):
        """Agent reports installation result back to Odoo."""
        try:
            payload = json.loads(request.httprequest.data or "{}")
            serial_number = (payload.get("serial_number") or "").strip()
            kb_number = (payload.get("kb_number") or "").strip()
            status = (payload.get("status") or "").strip()

            if not serial_number or not kb_number or not status:
                return request.make_response(
                    json.dumps({"success": False, "message": "serial_number, kb_number and status are required"}),
                    headers=[('Content-Type', 'application/json')]
                )

            if status not in ('installed', 'failed', 'uninstalled', 'cancelled'):
                return request.make_response(
                    json.dumps({"success": False, "message": "status must be installed/uninstalled/cancelled/failed"}),
                    headers=[('Content-Type', 'application/json')]
                )

            Asset = request.env["asset.asset"].sudo()
            asset = Asset.search([("serial_number", "=", serial_number)], limit=1)
            if not asset:
                return request.make_response(
                    json.dumps({"success": False, "message": "Asset not found"}),
                    headers=[('Content-Type', 'application/json')]
                )

            WindowsUpdate = request.env["asset.windows.update"].sudo()
            record = WindowsUpdate.search([("asset_id", "=", asset.id), ("kb_number", "=", kb_number)], limit=1)
            if not record:
                return request.make_response(
                    json.dumps({"success": False, "message": f"{kb_number} not found"}),
                    headers=[('Content-Type', 'application/json')]
                )

            record.write({"status": status, "action_date": fields.Datetime.now()})
            return request.make_response(
                json.dumps({"success": True, "kb_number": kb_number, "status": status}),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            _logger.error(f"[WU Result] Error: {e}", exc_info=True)
            return request.make_response(
                json.dumps({"success": False, "message": str(e)}),
                headers=[('Content-Type', 'application/json')]
            )

    # ========================================================================
    # LINUX OS UPGRADE API ENDPOINTS
    # See asset_management/models/asset_os_upgrade.py and
    # agent_snippets/os_upgrade.py for the reference agent-side implementation.
    # ========================================================================

    @http.route('/api/asset/os_upgrade/instructions', type='http', auth='public', methods=['GET'], csrf=False)
    def os_upgrade_instructions(self, **kwargs):
        """Agent polls Odoo to see if a distro release upgrade is due."""
        try:
            serial_number = request.httprequest.args.get("serial_number", "").strip()
            if not serial_number:
                return request.make_response(
                    json.dumps({"success": False, "message": "serial_number is required"}),
                    headers=[('Content-Type', 'application/json')]
                )

            pending = request.env["asset.os.upgrade.request"].sudo().get_pending_for_serial(serial_number)
            if not pending:
                return request.make_response(
                    json.dumps({"success": True, "action": "none"}),
                    headers=[('Content-Type', 'application/json')]
                )

            return request.make_response(
                json.dumps({"success": True, "action": "upgrade", **pending}),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            _logger.error(f"[OS Upgrade Instructions] Error: {e}", exc_info=True)
            return request.make_response(
                json.dumps({"success": False, "action": "none"}),
                headers=[('Content-Type', 'application/json')]
            )

    @http.route('/api/asset/os_upgrade/result', type='http', auth='public', methods=['POST'], csrf=False)
    def os_upgrade_result(self, **kwargs):
        """Agent reports progress/result of a distro release upgrade."""
        try:
            payload = json.loads(request.httprequest.data or "{}")
            serial_number = (payload.get("serial_number") or "").strip()
            request_id = payload.get("request_id")
            status = (payload.get("status") or "").strip()

            if not serial_number or not request_id or not status:
                return request.make_response(
                    json.dumps({"success": False, "message": "serial_number, request_id and status are required"}),
                    headers=[('Content-Type', 'application/json')]
                )

            result = request.env["asset.os.upgrade.request"].sudo().report_result(
                serial_number=serial_number,
                request_id=int(request_id),
                status=status,
                message=payload.get("message"),
                new_os_version=payload.get("new_os_version"),
                new_os_codename=payload.get("new_os_codename"),
            )
            return request.make_response(
                json.dumps(result),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            _logger.error(f"[OS Upgrade Result] Error: {e}", exc_info=True)
            return request.make_response(
                json.dumps({"success": False, "message": str(e)}),
                headers=[('Content-Type', 'application/json')]
            )

    # ========================================================================
    # APPLICATION UNINSTALL API ENDPOINTS
    # ========================================================================

    @http.route('/api/asset/apps/uninstall_command', type='http', auth='public', methods=['GET'], csrf=False)
    def app_uninstall_command(self, **kwargs):
        """
        Agent polls Odoo for application uninstall commands.
        Returns list of applications to uninstall with their details.
        """
        try:
            serial_number = request.httprequest.args.get("serial_number", "").strip()
            if not serial_number:
                return request.make_response(
                    json.dumps({"success": False, "message": "serial_number is required"}),
                    headers=[('Content-Type', 'application/json')]
                )

            Asset = request.env["asset.asset"].sudo()
            asset = Asset.search([("serial_number", "=", serial_number)], limit=1)
            
            if not asset:
                _logger.warning(f"[APP UNINSTALL] Asset not found: {serial_number}")
                return request.make_response(
                    json.dumps({"success": True, "uninstall_list": []}),
                    headers=[('Content-Type', 'application/json')]
                )

            InstalledApp = request.env["asset.installed.application"].sudo()
            apps_to_uninstall = InstalledApp.search([
                ("asset_id", "=", asset.id),
                ("uninstall_status", "=", "uninstalling")
            ])

            uninstall_list = []
            for app in apps_to_uninstall:
                uninstall_list.append({
                    "app_id": app.id,
                    "name": app.name or "",
                    "publisher": app.publisher or "",
                    "version": app.version or "",
                    "installed_date": app.installed_date or "",
                    "size": app.size or 0,
                })

            _logger.info(
                f"[APP UNINSTALL] Sending {len(uninstall_list)} uninstall commands to {serial_number}"
            )
            
            return request.make_response(
                json.dumps({
                    "success": True,
                    "uninstall_list": uninstall_list
                }),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            _logger.error(f"[APP UNINSTALL] Error: {e}", exc_info=True)
            return request.make_response(
                json.dumps({"success": False, "uninstall_list": []}),
                headers=[('Content-Type', 'application/json')]
            )

    @http.route('/api/asset/apps/uninstall_result', type='http', auth='public', methods=['POST'], csrf=False)
    def app_uninstall_result(self, **kwargs):
        """
        Agent reports application uninstall result back to Odoo.
        """
        try:
            payload = json.loads(request.httprequest.data or "{}")
            serial_number = (payload.get("serial_number") or "").strip()
            app_name = (payload.get("app_name") or "").strip()
            app_publisher = (payload.get("app_publisher") or "").strip()
            app_version = (payload.get("app_version") or "").strip()
            status = (payload.get("status") or "").strip()
            error_message = payload.get("error_message", "")

            if not serial_number or not app_name or not status:
                return request.make_response(
                    json.dumps({
                        "success": False,
                        "message": "serial_number, app_name, and status are required"
                    }),
                    headers=[('Content-Type', 'application/json')]
                )

            if status not in ('uninstalled', 'failed'):
                return request.make_response(
                    json.dumps({
                        "success": False,
                        "message": "status must be 'uninstalled' or 'failed'"
                    }),
                    headers=[('Content-Type', 'application/json')]
                )

            Asset = request.env["asset.asset"].sudo()
            asset = Asset.search([("serial_number", "=", serial_number)], limit=1)
            
            if not asset:
                _logger.warning(f"[APP UNINSTALL RESULT] Asset not found: {serial_number}")
                return request.make_response(
                    json.dumps({"success": False, "message": "Asset not found"}),
                    headers=[('Content-Type', 'application/json')]
                )

            InstalledApp = request.env["asset.installed.application"].sudo()
            app_record = InstalledApp.search([
                ("asset_id", "=", asset.id),
                ("name", "=", app_name),
                ("publisher", "=", app_publisher),
                ("version", "=", app_version),
            ], limit=1)

            if not app_record:
                # Try to find by name only if publisher/version don't match
                app_record = InstalledApp.search([
                    ("asset_id", "=", asset.id),
                    ("name", "=", app_name),
                ], limit=1)

            if not app_record:
                _logger.warning(
                    f"[APP UNINSTALL RESULT] Application not found: {app_name} "
                    f"(publisher: {app_publisher}, version: {app_version})"
                )
                return request.make_response(
                    json.dumps({
                        "success": False,
                        "message": f"Application '{app_name}' not found"
                    }),
                    headers=[('Content-Type', 'application/json')]
                )

            # Update the application record
            update_vals = {
                "uninstall_status": status,
                "uninstall_date": fields.Datetime.now(),
            }
            if status == 'failed' and error_message:
                update_vals["uninstall_error_message"] = error_message

            app_record.write(update_vals)

            # Create audit log entry
            AuditLog = request.env["asset.audit.log"].sudo()
            action_type = 'uninstall_success' if status == 'uninstalled' else 'uninstall_failed'
            AuditLog.create({
                "asset_id": asset.id,
                "action": action_type,
                "description": f"Application uninstall {status}: {app_name} (v{app_version}) by {app_publisher}. Error: {error_message}" if error_message else f"Application uninstall {status}: {app_name} (v{app_version}) by {app_publisher}",
                "user_id": self.env.uid,
            })

            _logger.info(
                f"[APP UNINSTALL RESULT] {serial_number}: {app_name} -> {status}"
            )

            return request.make_response(
                json.dumps({
                    "success": True,
                    "app_name": app_name,
                    "status": status
                }),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            _logger.error(f"[APP UNINSTALL RESULT] Error: {e}", exc_info=True)
            return request.make_response(
                json.dumps({"success": False, "message": str(e)}),
                headers=[('Content-Type', 'application/json')]
            )

    # ========================================================================
    # FOLDER LOCK API ENDPOINTS (NEW)
    # ========================================================================

    @http.route('/api/asset/locks/instructions', type='http', auth='public', methods=['GET'], csrf=False)
    def folder_lock_instructions(self, **kwargs):
        """Agent polls for locked folder list."""
        try:
            serial_number = request.httprequest.args.get("serial_number", "").strip()
            if not serial_number:
                return request.make_response(
                    json.dumps({"success": False, "message": "serial_number required"}),
                    headers=[('Content-Type', 'application/json')]
                )

            Asset = request.env["asset.asset"].sudo()
            asset = Asset.search([("serial_number", "=", serial_number)], limit=1)
            if not asset:
                return request.make_response(
                    json.dumps({"success": True, "locked_folders": []}),
                    headers=[('Content-Type', 'application/json')]
                )

            FolderLock = request.env["asset.folder.lock"].sudo()
            locks = FolderLock.search([("asset_id", "=", asset.id)])

            locked_folders = []
            for lock in locks:
                locked_folders.append({"path": lock.folder_path, "is_locked": lock.is_locked})

            return request.make_response(
                json.dumps({"success": True, "locked_folders": locked_folders}),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            _logger.error(f"[FOLDER LOCK] Error: {e}", exc_info=True)
            return request.make_response(
                json.dumps({"success": False, "locked_folders": []}),
                headers=[('Content-Type', 'application/json')]
            )

    @http.route('/api/asset/locks/violation', type='http', auth='public', methods=['POST'], csrf=False)
    def folder_lock_violation(self, **kwargs):
        """Agent reports folder access violation."""
        try:
            payload = json.loads(request.httprequest.data or "{}")
            serial_number = (payload.get("serial_number") or "").strip()
            folder_path = (payload.get("folder_path") or "").strip()
            username = (payload.get("username") or "").strip()
            process_name = (payload.get("process_name") or "").strip()

            if not serial_number or not folder_path:
                return request.make_response(
                    json.dumps({"success": False, "message": "serial_number and folder_path required"}),
                    headers=[('Content-Type', 'application/json')]
                )

            Asset = request.env["asset.asset"].sudo()
            asset = Asset.search([("serial_number", "=", serial_number)], limit=1)
            if not asset:
                return request.make_response(
                    json.dumps({"success": False, "message": "Asset not found"}),
                    headers=[('Content-Type', 'application/json')]
                )

            FolderLock = request.env["asset.folder.lock"].sudo()
            lock = FolderLock.search([("asset_id", "=", asset.id), ("folder_path", "=", folder_path)], limit=1)

            if not lock:
                lock = FolderLock.create({"asset_id": asset.id, "folder_path": folder_path, "is_locked": True})

            Violation = request.env["asset.folder.violation"].sudo()
            Violation.create({
                "folder_lock_id": lock.id,
                "attempt_date": fields.Datetime.now(),
                "username": username,
                "process_name": process_name,
                "action_taken": "blocked",
            })

            return request.make_response(
                json.dumps({"success": True}),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            _logger.error(f"[FOLDER LOCK] Error: {e}", exc_info=True)
            return request.make_response(
                json.dumps({"success": False, "message": str(e)}),
                headers=[('Content-Type', 'application/json')]
            )

    @http.route('/api/asset/security/report', type='http', auth='public',
                methods=['POST'], csrf=False)
    def security_event_report(self, **kwargs):
        """Agent reports an OS-level security event: login/logout, a failed
        login, a username/password change, an account created/deleted, a
        sudo/admin grant, or a locked path's permissions getting overridden
        (e.g. `chmod 777` on something the file-access lock set to 000).

        Deliberately one generic endpoint for all of these rather than one
        route per event type: they share the same shape (which asset, which
        of the fixed `action` values on asset.audit.log, what changed, a
        human-readable description) and none of them need a bespoke
        response - unlike e.g. /api/asset/locks/instructions, which returns
        a policy the agent has to act on.
        """
        try:
            payload = json.loads(request.httprequest.data or "{}")
            serial_number = (payload.get("serial_number") or "").strip()
            action = (payload.get("action") or "").strip()
            description = payload.get("description") or ""
            old_value = payload.get("old_value")
            new_value = payload.get("new_value")

            valid_actions = dict(
                request.env["asset.audit.log"]._fields["action"].selection
            )
            if not serial_number or action not in valid_actions:
                return request.make_response(
                    json.dumps({"success": False,
                               "message": "serial_number required and action "
                                          "must be one of %s"
                                          % list(valid_actions)}),
                    headers=[('Content-Type', 'application/json')]
                )

            asset = request.env["asset.asset"].sudo().search(
                [("serial_number", "=", serial_number)], limit=1)
            if not asset:
                return request.make_response(
                    json.dumps({"success": False, "message": "Asset not found"}),
                    headers=[('Content-Type', 'application/json')]
                )

            asset._log_security_event(action, description, old_value, new_value)
            return request.make_response(
                json.dumps({"success": True}),
                headers=[('Content-Type', 'application/json')]
            )
        except Exception as e:
            _logger.error(f"[SECURITY EVENT] Error: {e}", exc_info=True)
            return request.make_response(
                json.dumps({"success": False, "message": str(e)}),
                headers=[('Content-Type', 'application/json')]
            )

    # ========================================================================
    # FILE ACCESS POLICY API ENDPOINTS
    # ========================================================================

    @http.route('/api/asset/file_access/scan', type='http', auth='public', methods=['POST'], csrf=False)
    def file_access_scan(self, **kwargs):
        """
        Agent sends a full scanned file list from the user's standard folders.
        Old records are replaced with the new snapshot.

        Hardened for fleet load:
          - Batched insert (200 rows per chunk) with commit between batches,
            so heartbeat / live-metrics requests on the same asset row are not
            blocked by one giant transaction.
          - Hard cap on incoming records (defensive — agent already caps,
            but this protects Odoo from a malformed payload).
          - Old records deleted in batches too, for the same reason.
          - Cursor rollback on failure so the worker stays healthy.
        """
        # --- Tunables --------------------------------------------------------
        BATCH_SIZE = 200  # rows per insert / unlink chunk
        MAX_RECORDS_PER_SCAN = 2000  # never accept more than this from one agent

        try:
            payload = json.loads(request.httprequest.data or "{}")
            serial_number = (payload.get("serial_number") or "").strip()
            records = payload.get("records", []) or []
            scanned_at = payload.get("scanned_at")

            if not serial_number:
                return request.make_response(
                    json.dumps({"success": False, "message": "serial_number is required"}),
                    headers=[('Content-Type', 'application/json')]
                )

            # Defensive cap — protect Odoo from a runaway agent payload
            if len(records) > MAX_RECORDS_PER_SCAN:
                _logger.warning(
                    f"[FILE ACCESS SCAN] {serial_number} sent {len(records)} records; "
                    f"truncating to {MAX_RECORDS_PER_SCAN}"
                )
                records = records[:MAX_RECORDS_PER_SCAN]

            Asset = request.env["asset.asset"].sudo()
            asset = Asset.search([("serial_number", "=", serial_number)], limit=1)
            if not asset:
                _logger.warning(f"[FILE ACCESS SCAN] Asset not found: {serial_number}")
                return request.make_response(
                    json.dumps({"success": False, "message": "Asset not found"}),
                    headers=[('Content-Type', 'application/json')]
                )

            FileAccessRecord = request.env["asset.file.access.record"].sudo()
            Policy = request.env["asset.file.access.policy"].sudo()

            # Pre-fetch blocked paths so we can mark new rows without N+1 queries
            blocked_paths = set(Policy.search([
                ("asset_id", "=", asset.id),
                ("is_blocked", "=", True),
            ]).mapped("path"))

            scan_time = self._parse_datetime(scanned_at) if scanned_at else fields.Datetime.now()

            # --- STEP 1: Delete old records in chunks ---------------------------
            # A single .unlink() on tens of thousands of rows holds a long lock
            # that blocks heartbeats. We loop and commit per batch.
            old_records = FileAccessRecord.search([("asset_id", "=", asset.id)])
            total_old = len(old_records)
            for i in range(0, total_old, BATCH_SIZE):
                old_records[i:i + BATCH_SIZE].unlink()
                request.env.cr.commit()  # release lock between batches

            # --- STEP 2: Build the new vals_list (no DB writes yet) -------------
            # The valid_folders set MUST stay in sync with the Selection on
            # asset.file.access.record.parent_folder.
            valid_folders = {'Desktop', 'Documents', 'Downloads', 'Pictures', 'Trash'}

            vals_list = []
            for record in records:
                record_type = (record.get("type") or record.get("record_type") or "file")
                name = (record.get("name") or "").strip()
                path = (record.get("path") or "").strip()
                parent_folder = (record.get("parent_folder") or "").strip()
                size_kb = float(record.get("size_kb") or 0.0)
                last_modified = record.get("last_modified")

                if not name or not path:
                    continue

                # If agent didn't classify, try to infer from path
                if parent_folder not in valid_folders:
                    inferred = None
                    low = path.lower()
                    for vf in valid_folders:
                        if f"/{vf.lower()}/" in low or low.endswith(f"/{vf.lower()}"):
                            inferred = vf
                            break
                    if not inferred:
                        continue  # Skip records we cannot classify
                    parent_folder = inferred

                last_mod_dt = self._parse_datetime(last_modified) if last_modified else None

                # parent_path is the folder containing this record. Use what
                # agent sent; otherwise derive from path.
                parent_path = (record.get("parent_path") or "").strip()
                if not parent_path and path:
                    norm_path = path.replace('\\', '/')
                    if '/' in norm_path:
                        parent_path = '/'.join(norm_path.split('/')[:-1])
                    else:
                        parent_path = ''

                vals_list.append({
                    "asset_id": asset.id,
                    "record_type": record_type,
                    "name": name,
                    "path": path,
                    "parent_path": parent_path or False,
                    "parent_folder": parent_folder,
                    "size_kb": size_kb,
                    "last_modified": last_mod_dt,
                    "scanned_at": scan_time,
                    "is_blocked": path in blocked_paths,
                })

            # --- STEP 3: Insert new records in chunks ---------------------------
            # ORM batched-create is fine — the issue is transaction length,
            # which we cut by committing each batch.
            created_count = 0
            for i in range(0, len(vals_list), BATCH_SIZE):
                FileAccessRecord.create(vals_list[i:i + BATCH_SIZE])
                request.env.cr.commit()  # release lock between batches
                created_count += len(vals_list[i:i + BATCH_SIZE])

            # --- STEP 4: Stamp the asset, in its own short transaction ----------
            asset.write({"last_file_access_scan": scan_time})
            request.env.cr.commit()

            _logger.info(
                f"[FILE ACCESS SCAN] {serial_number}: deleted {total_old}, "
                f"inserted {created_count} (cap={MAX_RECORDS_PER_SCAN}, batch={BATCH_SIZE})"
            )
            return request.make_response(
                json.dumps({
                    "success": True,
                    "records_received": len(records),
                    "records_synced": created_count,
                    "records_deleted": total_old,
                }),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            # Cursor might be in aborted state — rollback so the worker survives
            try:
                request.env.cr.rollback()
            except Exception:
                pass
            _logger.error(f"[FILE ACCESS SCAN] Error: {e}", exc_info=True)
            return request.make_response(
                json.dumps({"success": False, "message": str(e)}),
                headers=[('Content-Type', 'application/json')],
                status=500,
            )

    @http.route('/api/asset/file_access/delta', type='http', auth='public', methods=['POST'], csrf=False)
    def file_access_delta(self, **kwargs):
        """
        Agent sends incremental file changes (created / deleted / moved).
        Use this between full scans to keep records fresh without resending everything.

        Payload:
            serial_number: str
            created: list of file record dicts (same format as /scan records)
            deleted: list of {"path": str} objects
            moved:   list of {"old_path": str, "new_path": str, "new_name": str}
            scanned_at: ISO datetime string (optional)
        """
        try:
            payload = json.loads(request.httprequest.data or "{}")
            serial_number = (payload.get("serial_number") or "").strip()

            if not serial_number:
                return request.make_response(
                    json.dumps({"success": False, "message": "serial_number is required"}),
                    headers=[('Content-Type', 'application/json')]
                )

            Asset = request.env["asset.asset"].sudo()
            asset = Asset.search([("serial_number", "=", serial_number)], limit=1)
            if not asset:
                return request.make_response(
                    json.dumps({"success": False, "message": "Asset not found"}),
                    headers=[('Content-Type', 'application/json')]
                )

            FileAccessRecord = request.env["asset.file.access.record"].sudo()
            Policy = request.env["asset.file.access.policy"].sudo()
            blocked_paths = set(Policy.search([
                ("asset_id", "=", asset.id),
                ("is_blocked", "=", True),
            ]).mapped("path"))

            scanned_at = payload.get("scanned_at")
            delta_time = self._parse_datetime(scanned_at) if scanned_at else fields.Datetime.now()
            valid_folders = {'Desktop', 'Documents', 'Downloads', 'Pictures', 'Trash'}

            created_count = 0
            deleted_count = 0
            moved_count = 0

            # ── CREATED ─────────────────────────────────────────────────────
            for rec in payload.get("created", []):
                name = (rec.get("name") or "").strip()
                path = (rec.get("path") or "").strip()
                if not name or not path:
                    continue
                parent_folder = (rec.get("parent_folder") or "").strip()
                if parent_folder not in valid_folders:
                    for vf in valid_folders:
                        if vf.lower() in path.lower():
                            parent_folder = vf
                            break
                    else:
                        continue
                existing = FileAccessRecord.search([
                    ("asset_id", "=", asset.id), ("path", "=", path)
                ], limit=1)
                if not existing:
                    FileAccessRecord.create({
                        "asset_id": asset.id,
                        "record_type": (rec.get("type") or "file"),
                        "name": name,
                        "path": path,
                        "parent_folder": parent_folder,
                        "size_kb": float(rec.get("size_kb") or 0.0),
                        "last_modified": self._parse_datetime(rec.get("last_modified")) if rec.get("last_modified") else None,
                        "scanned_at": delta_time,
                        "is_blocked": path in blocked_paths,
                    })
                    created_count += 1

            # ── DELETED ─────────────────────────────────────────────────────
            deleted_paths = [
                (d.get("path") or "").strip()
                for d in payload.get("deleted", [])
                if (d.get("path") or "").strip()
            ]
            if deleted_paths:
                records_to_delete = FileAccessRecord.search([
                    ("asset_id", "=", asset.id),
                    ("path", "in", deleted_paths),
                ])
                deleted_count = len(records_to_delete)
                records_to_delete.unlink()

            # ── MOVED ───────────────────────────────────────────────────────
            for mv in payload.get("moved", []):
                old_path = (mv.get("old_path") or "").strip()
                new_path = (mv.get("new_path") or "").strip()
                new_name = (mv.get("new_name") or "").strip()
                if not old_path or not new_path:
                    continue
                existing = FileAccessRecord.search([
                    ("asset_id", "=", asset.id), ("path", "=", old_path)
                ], limit=1)
                if existing:
                    write_vals = {
                        "path": new_path,
                        "scanned_at": delta_time,
                        "is_blocked": new_path in blocked_paths,
                    }
                    if new_name:
                        write_vals["name"] = new_name
                    existing.write(write_vals)
                    moved_count += 1

            # Update last_file_access_scan timestamp
            asset.write({"last_file_access_scan": delta_time})

            _logger.info(
                f"[FILE ACCESS DELTA] {serial_number}: "
                f"+{created_count} created, -{deleted_count} deleted, ~{moved_count} moved"
            )
            return request.make_response(
                json.dumps({
                    "success": True,
                    "created": created_count,
                    "deleted": deleted_count,
                    "moved": moved_count,
                }),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            _logger.error(f"[FILE ACCESS DELTA] Error: {e}", exc_info=True)
            return request.make_response(
                json.dumps({"success": False, "message": str(e)}),
                headers=[('Content-Type', 'application/json')]
            )

    @http.route('/api/asset/file_access/policy', type='http', auth='public', methods=['GET'], csrf=False)
    def file_access_policy(self, **kwargs):
        """
        Agent polls for file access policy (which paths to block).
        """
        try:
            serial_number = request.httprequest.args.get("serial_number", "").strip()
            if not serial_number:
                return request.make_response(
                    json.dumps({"success": False, "message": "serial_number is required"}),
                    headers=[('Content-Type', 'application/json')]
                )

            Asset = request.env["asset.asset"].sudo()
            asset = Asset.search([("serial_number", "=", serial_number)], limit=1)
            if not asset:
                return request.make_response(
                    json.dumps({"success": True, "blocked_paths": []}),
                    headers=[('Content-Type', 'application/json')]
                )

            try:
                FileAccessPolicy = request.env["asset.file.access.policy"].sudo()
                policies = FileAccessPolicy.search([
                    ("asset_id", "=", asset.id),
                    ("is_blocked", "=", True)
                ])

                blocked_paths = [policy.path for policy in policies if policy.path]

                _logger.info(f"[FILE ACCESS] Policy sent to {serial_number}: {len(blocked_paths)} blocked paths")
                return request.make_response(
                    json.dumps({"success": True, "blocked_paths": blocked_paths}),
                    headers=[('Content-Type', 'application/json')]
                )

            except KeyError:
                _logger.info(f"[FILE ACCESS] Policy requested for {serial_number} (not configured)")
                return request.make_response(
                    json.dumps({"success": True, "blocked_paths": []}),
                    headers=[('Content-Type', 'application/json')]
                )

        except Exception as e:
            _logger.error(f"[FILE ACCESS] Policy error: {e}", exc_info=True)
            return request.make_response(
                json.dumps({"success": False, "blocked_paths": []}),
                headers=[('Content-Type', 'application/json')]
            )

    @http.route('/api/asset/file_access/violation', type='http', auth='public', methods=['POST'], csrf=False)
    def file_access_violation(self, **kwargs):
        """
        Agent reports file access violation when user tries to access blocked file.
        """
        try:
            payload = json.loads(request.httprequest.data or "{}")
            serial_number = (payload.get("serial_number") or "").strip()
            path = (payload.get("path") or "").strip()
            folder = (payload.get("folder") or "").strip()
            filename = (payload.get("filename") or "").strip()
            action_taken = (payload.get("action_taken") or "blocked").strip()
            timestamp = payload.get("timestamp")

            if not serial_number or not path:
                return request.make_response(
                    json.dumps({"success": False, "message": "serial_number and path are required"}),
                    headers=[('Content-Type', 'application/json')]
                )

            Asset = request.env["asset.asset"].sudo()
            asset = Asset.search([("serial_number", "=", serial_number)], limit=1)
            if not asset:
                return request.make_response(
                    json.dumps({"success": False, "message": "Asset not found"}),
                    headers=[('Content-Type', 'application/json')]
                )

            try:
                FileAccessViolation = request.env["asset.file.access.violation"].sudo()
                
                violation_time = self._parse_datetime(timestamp) if timestamp else fields.Datetime.now()

                FileAccessViolation.create({
                    "asset_id": asset.id,
                    "path": path,
                    "folder": folder,
                    "filename": filename,
                    "action_taken": action_taken,
                    "violation_time": violation_time,
                })

                _logger.info(f"[FILE ACCESS] Violation logged for {serial_number}: {path}")
                return request.make_response(
                    json.dumps({"success": True}),
                    headers=[('Content-Type', 'application/json')]
                )

            except KeyError:
                _logger.info(f"[FILE ACCESS] Violation received for {serial_number} (storage not configured)")
                return request.make_response(
                    json.dumps({"success": True, "message": "Violation received (storage not configured)"}),
                    headers=[('Content-Type', 'application/json')]
                )

        except Exception as e:
            _logger.error(f"[FILE ACCESS] Violation error: {e}", exc_info=True)
            return request.make_response(
                json.dumps({"success": False, "message": str(e)}),
                headers=[('Content-Type', 'application/json')]
            )

    # ========================================================================
    # EXTRA FILES API ENDPOINT
    # ========================================================================

    @http.route('/api/asset/extra_files', type='http', auth='public', methods=['POST'], csrf=False)
    def extra_files_sync(self, **kwargs):
        """
        Receive scanned file list from the agent.
        """
        try:
            payload = json.loads(request.httprequest.data or "{}")
            serial_number = (payload.get("serial_number") or "").strip()
            files_data    = payload.get("files", [])
            scanned_at    = payload.get("scanned_at")

            if not serial_number:
                return request.make_response(
                    json.dumps({"success": False, "message": "serial_number is required"}),
                    headers=[('Content-Type', 'application/json')]
                )

            Asset = request.env["asset.asset"].sudo()
            asset = Asset.search([("serial_number", "=", serial_number)], limit=1)

            if not asset:
                _logger.warning(f"[ExtraFiles] Asset not found: {serial_number}")
                return request.make_response(
                    json.dumps({"success": False, "message": "Asset not found"}),
                    headers=[('Content-Type', 'application/json')]
                )

            ExtraFile = request.env["asset.extra.file"].sudo()

            # Replace all existing files for this asset with the fresh scan
            ExtraFile.search([("asset_id", "=", asset.id)]).unlink()

            valid_folders = {'Desktop', 'Documents', 'Downloads', 'Pictures', 'Trash'}
            created = 0

            for f in files_data:
                file_name = (f.get("file_name") or "").strip()
                if not file_name:
                    continue

                folder = (f.get("folder") or "other").lower().strip()
                if folder not in valid_folders:
                    folder = "other"

                last_mod = None
                raw_mod = f.get("last_modified")
                if raw_mod:
                    last_mod = self._parse_datetime(raw_mod)

                ExtraFile.create({
                    "asset_id":      asset.id,
                    "file_name":     file_name,
                    "folder":        folder,
                    "full_path":     (f.get("full_path") or "").strip(),
                    "file_size_kb":  float(f.get("file_size_kb") or 0.0),
                    "last_modified": last_mod,
                })
                created += 1

            scan_time = self._parse_datetime(scanned_at) if scanned_at else fields.Datetime.now()
            asset.write({"last_file_scan": scan_time})

            _logger.info(f"[ExtraFiles] Synced {created} files for asset {serial_number}")
            return request.make_response(
                json.dumps({"success": True, "files_synced": created}),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            _logger.error(f"[ExtraFiles] Error: {e}", exc_info=True)
            return request.make_response(
                json.dumps({"success": False, "message": str(e)}),
                headers=[('Content-Type', 'application/json')]
            )

    # ========================================================================
    # FILE BROWSER — DB-BACKED (agent pushes, UI reads from DB only)
    # Architecture: Odoo NEVER calls agent IPs. Agent pushes via /scan or /delta.
    # ========================================================================

    @http.route(
        '/asset/file/browse_db',
        type='jsonrpc',
        auth='user',
        methods=['POST'],
    )
    def browse_files_from_db(self, **kwargs):
        """
        Serve file records stored in asset.file.access.record for a given asset.

        The agent pushes its file list via POST /api/asset/file_access/scan (full)
        or POST /api/asset/file_access/delta (incremental).  The UI reads
        ONLY from the database — no HTTP calls to laptop IPs are ever made.

        Params (JSON body):
            asset_id     (int)  required — Odoo asset record ID
            parent_folder (str) optional — 'Desktop' | 'Documents' | 'Downloads'
                           If omitted, returns all folders.
        """
        try:
            asset_id = kwargs.get('asset_id')
            parent_folder = (kwargs.get('parent_folder') or '').strip()

            if not asset_id:
                return {"status": "error", "message": "Missing asset_id"}

            asset = request.env['asset.asset'].sudo().browse(int(asset_id))
            if not asset.exists():
                return {"status": "error", "message": "Asset not found"}

            # Build domain
            domain = [('asset_id', '=', asset.id)]
            valid_folders = ('Desktop', 'Documents', 'Downloads')

            parent_path = (kwargs.get('parent_path') or '').strip()

            if parent_path:
                # Subfolder navigation: return children of this specific folder
                domain.append(('parent_path', '=', parent_path))
            elif parent_folder and parent_folder in valid_folders:
                # Top-level folder tab clicked: return root items whose parent_path
                # points to the Desktop/Documents/Downloads root itself.
                # For root items, parent_path equals the root folder path
                # (e.g. C:\Users\X\Desktop). We filter by parent_folder only
                # when no parent_path is provided so the widget can boot without
                # knowing the exact root path.
                domain.append(('parent_folder', '=', parent_folder))
                # Only return root-level items (those whose parent_path is the
                # top-level folder, not a subfolder inside it).
                # We achieve this by excluding records whose parent_path contains
                # a separator beyond the top-level path.
                # Simpler approximation: return only direct children of root.
                # We store parent_path on every record so here we return all
                # records that belong to this top-level folder at the root level
                # (parent_path ends with Desktop / Documents / Downloads)
                domain.append(('parent_path', 'like', '%' + parent_folder + '%'))
                # Refine: only the DIRECT children (parent_path endswith a root path)
                # This is done on Python side after fetch for accuracy.
            else:
                # Fallback: return everything for this asset (used for stats)
                pass

            FileAccessRecord = request.env['asset.file.access.record'].sudo()
            records = FileAccessRecord.search(domain, order='record_type desc, name asc')

            # For root-level tab loads (no parent_path given), filter to only
            # direct children: records whose parent_path is the topmost path
            # for that folder (i.e., not nested deeper).
            if not parent_path and parent_folder:
                # Find the shallowest parent_path values (fewest separators)
                # to identify root-level items
                if records:
                    sep = '\\' if '\\' in (records[0].parent_path or '/') else '/'
                    min_depth = min(
                        (r.parent_path or '').count(sep) for r in records
                    ) if records else 0
                    records = records.filtered(
                        lambda r: (r.parent_path or '').count(sep) == min_depth
                    )

            FileAccessRecord = request.env['asset.file.access.record'].sudo()
            records = FileAccessRecord.search(domain, order='record_type desc, name asc')

            files = []
            for rec in records:
                files.append({
                    'id': rec.id,
                    'name': rec.name,
                    'path': rec.path,
                    'parent_path': rec.parent_path or False,
                    'type': rec.record_type,
                    'record_type': rec.record_type,
                    'parent_folder': rec.parent_folder,
                    'size_kb': rec.size_kb or 0.0,
                    'last_modified': fields.Datetime.to_string(rec.last_modified) if rec.last_modified else False,
                    'scanned_at': fields.Datetime.to_string(rec.scanned_at) if rec.scanned_at else False,
                    'is_blocked': rec.is_blocked,
                })

            _logger.debug(
                f"[FILE BROWSE DB] Asset {asset.id} ({asset.asset_name}): "
                f"returning {len(files)} records for folder='{parent_folder or 'ALL'}'"
            )
            return {
                "status": "success",
                "data": {
                    "asset_id": asset.id,
                    "asset_name": asset.asset_name,
                    "parent_folder": parent_folder or 'all',
                    "last_scan": fields.Datetime.to_string(asset.last_file_access_scan) if asset.last_file_access_scan else False,
                    "files": files,
                    "total": len(files),
                },
            }

        except Exception as e:
            _logger.exception("[FILE BROWSE DB] Error")
            return {"status": "error", "message": str(e)}

    # Keep the old /asset/file/browse route as a redirect for any legacy calls,
    # so they fail gracefully with an informative message instead of a 404.
    @http.route(
        '/asset/file/browse',
        type='jsonrpc',
        auth='user',
        methods=['POST'],
    )
    def browse_agent_files_deprecated(self, **kwargs):
        """Deprecated: live agent browse was removed. Use /asset/file/browse_db instead."""
        return {
            "status": "error",
            "message": (
                "Live agent browsing has been removed. "
                "File data is now served from the Odoo database after agent sync. "
                "Use /asset/file/browse_db instead."
            ),
        }

    # ========================================================================
    # CCTV AGENT CONTROLLER - TASK 1, 2, 3
    # ========================================================================

    @http.route(
        '/api/agent/cctv/config',
        type='jsonrpc',
        auth='public',
        methods=['POST'],
        csrf=False
    )
    def get_cctv_config(self, **kwargs):
        """
        Get CCTV camera configuration from Odoo.

        TASK 1: Make Odoo the source of truth for CCTV configuration.
        This endpoint:
        - Returns ONLY active cameras (is_active = True)
        - Is READ-ONLY (agent cannot modify data)
        - Returns configuration needed by the CCTV agent
        """
        try:
            payload = {}
            try:
                payload = json.loads(request.httprequest.data or "{}")
            except (json.JSONDecodeError, TypeError):
                payload = {}

            provided_agent_id = payload.get('agent_id')
            agent_id = provided_agent_id or str(uuid.uuid4())

            if provided_agent_id:
                self._update_agent_heartbeat_from_config(provided_agent_id)

            Camera = request.env['asset.camera'].sudo()
            active_cameras = Camera.search([('is_active', '=', True)])

            cameras_config = []
            for camera in active_cameras:
                ip_address = camera.camera_ip or camera.ip_address or ''
                port = camera.camera_port or camera.port or 554
                brand = camera.camera_brand or 'generic'
                protocol = camera.protocol or 'rtsp'

                rtsp_url = camera.rtsp_url or ''
                if not rtsp_url and ip_address:
                    rtsp_url = self._generate_default_rtsp_url(
                        ip_address, port, brand,
                        camera.username, camera.password
                    )

                camera_config = {
                    'camera_code': camera.camera_code or '',
                    'ip': ip_address,
                    'port': port,
                    'brand': brand,
                    'protocol': protocol,
                    'rtsp_url': rtsp_url,
                    'username': camera.username or '',
                    'password': camera.password or ''
                }
                cameras_config.append(camera_config)

            _logger.info(
                f"CCTV config requested - Agent: {agent_id}, "
                f"Cameras returned: {len(cameras_config)}"
            )

            return {
                'agent_id': agent_id,
                'sync_interval': 30,
                'cameras': cameras_config
            }

        except Exception as e:
            _logger.error(f"Error in get_cctv_config: {str(e)}", exc_info=True)
            return {
                'agent_id': str(uuid.uuid4()),
                'sync_interval': 30,
                'cameras': [],
                'error': str(e)
            }

    def _generate_default_rtsp_url(self, ip, port, brand, username=None, password=None):
        """Generate default RTSP URL based on camera brand."""
        auth_part = ''
        if username and password:
            auth_part = f"{username}:{password}@"

        rtsp_paths = {
            'hikvision': '/Streaming/Channels/101',
            'dahua': '/cam/realmonitor?channel=1&subtype=0',
            'axis': '/axis-media/media.amp',
            'cp_plus': '/cam/realmonitor?channel=1&subtype=0',
            'generic': '/stream1'
        }

        path = rtsp_paths.get(brand, rtsp_paths['generic'])
        return f"rtsp://{auth_part}{ip}:{port}{path}"

    def _update_agent_heartbeat_from_config(self, agent_id):
        """Update agent heartbeat when config is requested."""
        try:
            Agent = request.env['asset.agent'].sudo()
            agent = Agent.search([('agent_id', '=', agent_id)], limit=1)
            if agent:
                agent.write({
                    'last_seen': fields.Datetime.now(),
                    'status': 'online'
                })
        except Exception as e:
            _logger.warning(f"Failed to update agent heartbeat: {e}")

    @http.route(
        '/api/agent/register',
        type='jsonrpc',
        auth='public',
        methods=['POST'],
        csrf=False
    )
    def register_agent(self, **kwargs):
        """
        Register a new monitoring agent or update existing one.

        TASK 2: Enterprise Agent Identity Registration.
        """
        try:
            payload = {}
            try:
                payload = json.loads(request.httprequest.data or "{}")
            except (json.JSONDecodeError, TypeError):
                payload = {}

            hostname = payload.get('hostname')
            platform = payload.get('platform', 'cctv')
            agent_version = payload.get('agent_version')

            ip_address = payload.get('ip_address') or request.httprequest.environ.get(
                'REMOTE_ADDR', ''
            )

            valid_platforms = ['windows', 'linux', 'macos', 'cctv']
            if platform not in valid_platforms:
                platform = 'cctv'

            Agent = request.env['asset.agent'].sudo()
            result = Agent.register_agent(
                hostname=hostname,
                platform=platform,
                agent_version=agent_version,
                ip_address=ip_address
            )

            _logger.info(
                f"Agent registration - Hostname: {hostname}, "
                f"Platform: {platform}, Success: {result.get('success')}"
            )

            return result

        except Exception as e:
            _logger.error(f"Error in register_agent: {str(e)}", exc_info=True)
            return {
                'success': False,
                'agent_id': None,
                'token': None,
                'message': f'Registration failed: {str(e)}'
            }

    @http.route(
        '/api/agent/heartbeat',
        type='jsonrpc',
        auth='public',
        methods=['POST'],
        csrf=False
    )
    def agent_heartbeat(self, **kwargs):
        """
        Receive agent heartbeat to update online status.

        TASK 3: Agent heartbeat support.
        """
        try:
            payload = {}
            try:
                payload = json.loads(request.httprequest.data or "{}")
            except (json.JSONDecodeError, TypeError):
                payload = {}

            agent_id = payload.get('agent_id')

            if not agent_id:
                return {
                    'success': False,
                    'message': 'agent_id is required'
                }

            hostname = payload.get('hostname')
            agent_version = payload.get('agent_version')
            ip_address = payload.get('ip_address') or request.httprequest.environ.get(
                'REMOTE_ADDR', ''
            )

            Agent = request.env['asset.agent'].sudo()
            success = Agent.update_agent_heartbeat(
                agent_id=agent_id,
                hostname=hostname,
                agent_version=agent_version,
                ip_address=ip_address
            )

            if success:
                return {
                    'success': True,
                    'message': 'Heartbeat received'
                }
            else:
                return {
                    'success': False,
                    'message': 'Agent not found. Please register first.'
                }

        except Exception as e:
            _logger.error(f"Error in agent_heartbeat: {str(e)}", exc_info=True)
            return {
                'success': False,
                'message': f'Heartbeat failed: {str(e)}'
            }

    # ========================================================================
    # ANTIVIRUS CONTROLLER
    # ========================================================================

    @http.route(
        '/api/antivirus/deploy',
        type='http',
        auth='user',
        methods=['POST'],
        csrf=False
    )
    def deploy_antivirus(self, **kwargs):
        """Deploy antivirus to specified assets."""
        try:
            data = json.loads(request.httprequest.data or "{}")
            asset_ids = data.get('asset_ids', [])
            config_id = data.get('config_id')

            if not asset_ids:
                return request.make_response(
                    json.dumps({
                        'success': False,
                        'message': 'asset_ids is required'
                    }),
                    headers=[('Content-Type', 'application/json')]
                )

            Deployment = request.env['antivirus.deployment'].sudo()
            result = Deployment.deploy_to_assets(asset_ids, config_id)

            return request.make_response(
                json.dumps({
                    'success': True,
                    'deployed': result['deployed_count'],
                    'skipped': result['skipped_count'],
                    'message': result['message']
                }),
                headers=[('Content-Type', 'application/json')]
            )

        except json.JSONDecodeError as e:
            _logger.error(f"Invalid JSON in deploy request: {e}")
            return request.make_response(
                json.dumps({
                    'success': False,
                    'message': 'Invalid JSON body'
                }),
                headers=[('Content-Type', 'application/json')]
            )
        except Exception as e:
            _logger.error(f"Error in deploy_antivirus: {e}", exc_info=True)
            return request.make_response(
                json.dumps({
                    'success': False,
                    'message': str(e)
                }),
                headers=[('Content-Type', 'application/json')]
            )

    @http.route(
        '/api/antivirus/command',
        type='http',
        auth='public',
        methods=['GET'],
        csrf=False
    )
    def get_antivirus_command(self, **params):
        """Agent polls for antivirus deployment command."""
        try:
            serial_number = params.get('serial_number', '').strip()

            if not serial_number:
                return request.make_response(
                    json.dumps({
                        'command': 'none',
                        'message': 'serial_number is required'
                    }),
                    headers=[('Content-Type', 'application/json')]
                )

            Asset = request.env['asset.asset'].sudo()
            asset = Asset.search([('serial_number', '=', serial_number)], limit=1)

            if not asset:
                return request.make_response(
                    json.dumps({
                        'command': 'none',
                        'message': 'Asset not found'
                    }),
                    headers=[('Content-Type', 'application/json')]
                )

            Deployment = request.env['antivirus.deployment'].sudo()
            deployment = Deployment.search([
                ('asset_id', '=', asset.id),
                ('status', '=', 'pending')
            ], order='create_date asc', limit=1)

            if not deployment:
                return request.make_response(
                    json.dumps({
                        'command': 'none',
                        'message': 'No pending deployment found'
                    }),
                    headers=[('Content-Type', 'application/json')]
                )

            config = deployment.config_id
            if not config:
                config = request.env['antivirus.config'].sudo().search([('is_default', '=', True)], limit=1)
                if not config:
                    config = request.env['antivirus.config'].sudo().search([], limit=1)

            if not config:
                return request.make_response(
                    json.dumps({
                        'command': 'none',
                        'message': 'No antivirus configuration available'
                    }),
                    headers=[('Content-Type', 'application/json')]
                )

            platform = asset.os_platform or 'unknown'
            installer_url = None

            if platform == 'windows':
                installer_url = config.installer_windows
            elif platform == 'linux':
                installer_url = config.installer_linux
            elif platform == 'macos':
                installer_url = config.installer_macos

            if not installer_url:
                return request.make_response(
                    json.dumps({
                        'command': 'none',
                        'message': f'No installer configured for platform: {platform}'
                    }),
                    headers=[('Content-Type', 'application/json')]
                )

            deployment.write({
                'status': 'downloading',
                'started_at': fields.Datetime.now(),
                'installer_url_used': installer_url
            })

            product = config.antivirus_product or 'unknown'
            if product == 'other' and config.custom_product_name:
                product = config.custom_product_name

            return request.make_response(
                json.dumps({
                    'command': 'install',
                    'deployment_id': deployment.id,
                    'installer_url': installer_url,
                    'silent_install': config.silent_install or True,
                    'platform': platform,
                    'product': product
                }),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            _logger.error(f"Error in get_antivirus_command: {e}", exc_info=True)
            return request.make_response(
                json.dumps({
                    'command': 'none',
                    'message': f'Error: {str(e)}'
                }),
                headers=[('Content-Type', 'application/json')]
            )

    @http.route(
        '/api/antivirus/status',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False
    )
    def report_antivirus_status(self, **kwargs):
        """Agent reports antivirus deployment status."""
        try:
            data = json.loads(request.httprequest.data or "{}")
            serial_number = data.get('serial_number', '').strip()
            deployment_id = data.get('deployment_id')
            status = data.get('status')
            av_version = data.get('av_version')
            error_message = data.get('error_message')
            agent_log = data.get('agent_log')

            if not serial_number:
                return request.make_response(
                    json.dumps({
                        'success': False,
                        'message': 'serial_number is required'
                    }),
                    headers=[('Content-Type', 'application/json')]
                )

            valid_statuses = ['downloading', 'installing', 'installed', 'failed']
            if status and status not in valid_statuses:
                return request.make_response(
                    json.dumps({
                        'success': False,
                        'message': f'Invalid status. Must be one of: {", ".join(valid_statuses)}'
                    }),
                    headers=[('Content-Type', 'application/json')]
                )

            Asset = request.env['asset.asset'].sudo()
            asset = Asset.search([('serial_number', '=', serial_number)], limit=1)

            if not asset:
                return request.make_response(
                    json.dumps({
                        'success': False,
                        'message': 'Asset not found'
                    }),
                    headers=[('Content-Type', 'application/json')]
                )

            Deployment = request.env['antivirus.deployment'].sudo()
            deployment = False

            if deployment_id:
                deployment = Deployment.search([
                    ('id', '=', deployment_id),
                    ('asset_id', '=', asset.id)
                ], limit=1)

            if not deployment:
                deployment = Deployment.search([
                    ('asset_id', '=', asset.id),
                    ('status', 'in', ['pending', 'downloading', 'installing'])
                ], order='create_date desc', limit=1)

            if not deployment:
                return request.make_response(
                    json.dumps({
                        'success': False,
                        'message': 'No active deployment found for this asset'
                    }),
                    headers=[('Content-Type', 'application/json')]
                )

            update_vals = {}
            if status:
                update_vals['status'] = status
            if av_version:
                update_vals['av_version_installed'] = av_version
            if error_message:
                update_vals['error_message'] = error_message
            if agent_log:
                update_vals['agent_log'] = agent_log

            if status in ['installed', 'failed']:
                update_vals['completed_at'] = fields.Datetime.now()

            if update_vals:
                deployment.write(update_vals)

            asset_status_map = {
                'installed': 'protected',
                'failed': 'unprotected',
                'downloading': 'installing',
                'installing': 'installing',
            }

            if status and status in asset_status_map:
                asset_status = asset_status_map[status]
                if asset_status == 'installing':
                    asset_status = 'pending'
                asset.write({'antivirus_status': asset_status})

            return request.make_response(
                json.dumps({
                    'success': True,
                    'message': 'Status updated successfully'
                }),
                headers=[('Content-Type', 'application/json')]
            )

        except json.JSONDecodeError as e:
            _logger.error(f"Invalid JSON in status report: {e}")
            return request.make_response(
                json.dumps({
                    'success': False,
                    'message': 'Invalid JSON body'
                }),
                headers=[('Content-Type', 'application/json')]
            )
        except Exception as e:
            _logger.error(f"Error in report_antivirus_status: {e}", exc_info=True)
            return request.make_response(
                json.dumps({
                    'success': False,
                    'message': str(e)
                }),
                headers=[('Content-Type', 'application/json')]
            )

    @http.route(
        '/api/antivirus/threat',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False
    )
    def report_antivirus_threat(self, **kwargs):
        """Agent reports a detected antivirus threat."""
        try:
            data = json.loads(request.httprequest.data or "{}")
            serial_number = data.get('serial_number', '').strip()

            if not serial_number:
                return request.make_response(
                    json.dumps({'success': False, 'message': 'serial_number is required'}),
                    headers=[('Content-Type', 'application/json')]
                )

            Asset = request.env['asset.asset'].sudo()
            asset = Asset.search([('serial_number', '=', serial_number)], limit=1)
            if not asset:
                return request.make_response(
                    json.dumps({'success': False, 'message': 'Asset not found'}),
                    headers=[('Content-Type', 'application/json')]
                )

            Threat = request.env['antivirus.threat'].sudo()
            threat_vals = {
                'asset_id': asset.id,
                'name': data.get('threat_name', 'Unknown Threat'),
                'threat_name': data.get('threat_name', 'Unknown Threat'),
                'threat_type': data.get('threat_type', 'virus'),
                'severity': data.get('severity', 'medium'),
                'status': data.get('status', 'active'),
                'summary': data.get('summary', ''),
            }

            if data.get('detected_date'):
                threat_vals['detected_date'] = data.get('detected_date')

            if data.get('status') == 'quarantined':
                threat_vals['quarantined_date'] = fields.Datetime.now()

            threat = Threat.create(threat_vals)

            return request.make_response(
                json.dumps({
                    'success': True,
                    'threat_id': threat.id,
                    'message': 'Threat reported successfully'
                }),
                headers=[('Content-Type', 'application/json')]
            )
        except Exception as e:
            _logger.error(f"Error in report_antivirus_threat: {e}", exc_info=True)
            return request.make_response(
                json.dumps({'success': False, 'message': str(e)}),
                headers=[('Content-Type', 'application/json')]
            )

    @http.route(
        '/api/antivirus/scan',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False
    )
    def report_antivirus_scan(self, **kwargs):
        """Agent reports an antivirus scan activity."""
        try:
            data = json.loads(request.httprequest.data or "{}")
            serial_number = data.get('serial_number', '').strip()

            if not serial_number:
                return request.make_response(
                    json.dumps({'success': False, 'message': 'serial_number is required'}),
                    headers=[('Content-Type', 'application/json')]
                )

            Asset = request.env['asset.asset'].sudo()
            asset = Asset.search([('serial_number', '=', serial_number)], limit=1)
            if not asset:
                return request.make_response(
                    json.dumps({'success': False, 'message': 'Asset not found'}),
                    headers=[('Content-Type', 'application/json')]
                )

            ScanLog = request.env['antivirus.scan.log'].sudo()
            scan_vals = {
                'asset_id': asset.id,
                'scan_type': data.get('scan_type', 'quick'),
                'result': data.get('result', 'clean'),
                'threats_detected': data.get('threats_detected', 0),
                'files_scanned': data.get('files_scanned', 0),
                'duration': data.get('duration', 0.0),
                'summary': data.get('summary', ''),
            }

            if data.get('scan_time'):
                scan_vals['scan_time'] = data.get('scan_time')

            scan_log = ScanLog.create(scan_vals)

            return request.make_response(
                json.dumps({
                    'success': True,
                    'scan_log_id': scan_log.id,
                    'message': 'Scan log reported successfully'
                }),
                headers=[('Content-Type', 'application/json')]
            )
        except Exception as e:
            _logger.error(f"Error in report_antivirus_scan: {e}", exc_info=True)
            return request.make_response(
                json.dumps({'success': False, 'message': str(e)}),
                headers=[('Content-Type', 'application/json')]
            )

    @http.route(
        '/api/antivirus/deployments',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False
    )
    def list_deployments(self, **params):
        """List all antivirus deployments with optional filtering."""
        try:
            status_filter = params.get('status')
            limit = int(params.get('limit', 100))

            Deployment = request.env['antivirus.deployment'].sudo()

            domain = []
            if status_filter:
                domain.append(('status', '=', status_filter))

            deployments = Deployment.search(domain, order='create_date desc', limit=limit)

            result = []
            for dep in deployments:
                result.append({
                    'id': dep.id,
                    'asset_code': dep.asset_code or '',
                    'device_name': dep.device_name or '',
                    'serial_number': dep.serial_number or '',
                    'platform': dep.os_platform or 'unknown',
                    'status': dep.status or 'pending',
                    'deployed_by': dep.deployed_by.name if dep.deployed_by else '',
                    'started_at': fields.Datetime.to_string(dep.started_at) if dep.started_at else '',
                    'completed_at': fields.Datetime.to_string(dep.completed_at) if dep.completed_at else '',
                    'duration_minutes': dep.duration_minutes or 0.0,
                    'av_version': dep.av_version_installed or '',
                    'error_message': dep.error_message or '',
                    'create_date': fields.Datetime.to_string(dep.create_date) if dep.create_date else ''
                })

            return request.make_response(
                json.dumps({
                    'success': True,
                    'count': len(result),
                    'deployments': result
                }),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            _logger.error(f"Error in list_deployments: {e}", exc_info=True)
            return request.make_response(
                json.dumps({
                    'success': False,
                    'message': str(e)
                }),
                headers=[('Content-Type', 'application/json')]
            )

    # ========================================================================
    # SOFTWARE DEPLOYMENT CONTROLLER (LEGACY)
    # ========================================================================

    @http.route(
        '/api/asset/software/poll',
        type='jsonrpc',
        auth='public',
        methods=['GET'],
        csrf=False
    )
    def poll_deployments_software(self, serial_number=None, **kwargs):
        """Agent polls for pending software deployments."""
        try:
            if not serial_number:
                return {'success': False, 'message': 'serial_number is required', 'deployments': []}
            device = request.env['asset.agent'].sudo().search([
                ('agent_id', '=', serial_number)
            ], limit=1)

            if not device:
                return {
                    'success': False,
                    'message': 'Device not found',
                    'deployments': []
                }

            deployments = request.env['asset.software.deployment'].sudo().search([
                ('device_id', '=', device.id),
                ('status', 'in', ['pending', 'downloading', 'installing'])
            ])

            deployment_list = []
            for dep in deployments:
                deployment_list.append({
                    'deployment_id': dep.id,
                    'software_name': dep.software_id.name,
                    'software_version': dep.software_id.version,
                    'installer_url': dep.software_id.installer_url,
                    'installer_filename': dep.software_id.installer_filename,
                    'silent_flags': dep.software_id.silent_flags,
                    'current_status': dep.status
                })

            _logger.info(
                f"[SOFTWARE] Device {serial_number} polled - "
                f"{len(deployment_list)} pending deployment(s)"
            )

            return {
                'success': True,
                'deployments': deployment_list
            }

        except Exception as e:
            _logger.error(
                f"[SOFTWARE] Error polling deployments: {e}",
                exc_info=True
            )
            return {
                'success': False,
                'message': str(e),
                'deployments': []
            }

    @http.route(
        '/api/asset/software/report',
        type='jsonrpc',
        auth='public',
        methods=['POST'],
        csrf=False
    )
    def report_deployment_status_software(
        self,
        serial_number,
        deployment_id,
        status,
        error_message=None,
        agent_log=None,
        **kwargs
    ):
        """Agent reports installation progress/results."""
        try:
            deployment = request.env['asset.software.deployment'].sudo().browse(
                deployment_id
            )

            if not deployment.exists():
                return {
                    'success': False,
                    'message': 'Deployment not found'
                }

            update_vals = {'status': status}

            if status == 'downloading':
                update_vals['started_date'] = fields.Datetime.now()

            if status in ['installed', 'failed']:
                update_vals['completed_date'] = fields.Datetime.now()

            if error_message:
                update_vals['error_message'] = error_message

            if agent_log:
                update_vals['agent_log'] = agent_log

            deployment.write(update_vals)

            _logger.info(
                f"[SOFTWARE] Deployment {deployment_id} status updated to: {status}"
            )

            return {
                'success': True,
                'message': 'Status updated'
            }

        except Exception as e:
            _logger.error(
                f"[SOFTWARE] Error reporting status: {e}",
                exc_info=True
            )
            return {
                'success': False,
                'message': str(e)
            }

    # ========================================================================
    # APP DEPLOYMENT CONTROLLER (NEW PACKAGE-MANAGER-BASED)
    # ========================================================================

    @http.route(
        '/asset_management/api/agent/poll',
        type='http',
        auth='public',
        methods=['GET'],
        csrf=False
    )
    def poll_deployments_app(self, serial_number=None, **kwargs):
        """Agent polls for pending app deployments (GET ?serial_number=<sn>)."""
        try:
            if not serial_number:
                return request.make_response(
                    json.dumps({'success': False, 'message': 'serial_number required', 'deployments': []}),
                    headers=[('Content-Type', 'application/json')]
                )

            # Look up the asset.asset record by serial number (device_id FK target)
            device = request.env['asset.asset'].sudo().search([
                ('serial_number', '=', serial_number)
            ], limit=1)

            if not device:
                _logger.warning(f"[APP DEPLOYMENT] Poll: no asset found for serial {serial_number}")
                return request.make_response(
                    json.dumps({'success': False, 'message': 'Device not found', 'deployments': []}),
                    headers=[('Content-Type', 'application/json')]
                )

            deployments = request.env['asset_management.app_deployment'].sudo().search([
                ('device_id', '=', device.id),
                ('status', '=', 'pending')
            ], order='deployment_created asc')

            deployment_list = []
            for dep in deployments:
                deployment_list.append({
                    'deployment_id':      dep.id,
                    'application_name':   dep.application_name,
                    'application_source': dep.application_source or 'preset',
                    'action_type':        dep.action_type or 'install',
                    'package_manager':    dep.package_manager,
                    'install_command':    dep.install_command,
                    'installer_url':      dep.installer_url or '',
                    'installer_type':     dep.installer_type or '',
                    'installer_args':     dep.installer_args or '',
                })

            _logger.info(
                f"[APP DEPLOYMENT] Device {serial_number} polled - "
                f"{len(deployment_list)} pending deployment(s)"
            )

            return request.make_response(
                json.dumps({'success': True, 'deployments': deployment_list}),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            _logger.error(f"[APP DEPLOYMENT] Error polling deployments: {e}", exc_info=True)
            return request.make_response(
                json.dumps({'success': False, 'message': str(e), 'deployments': []}),
                headers=[('Content-Type', 'application/json')]
            )

    @http.route(
        '/asset_management/api/agent/deployment_status',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False
    )
    def update_deployment_status_app(self, **kwargs):
        """Agent reports deployment progress/results (POST with JSON body)."""
        try:
            data = json.loads(request.httprequest.data or '{}')
            deployment_id = data.get('deployment_id')
            status = data.get('status')
            error_message = data.get('error_message')
            completed = data.get('completed')

            if not deployment_id or not status:
                return request.make_response(
                    json.dumps({'success': False, 'message': 'deployment_id and status required'}),
                    headers=[('Content-Type', 'application/json')]
                )

            deployment = request.env['asset_management.app_deployment'].sudo().browse(
                int(deployment_id)
            )

            if not deployment.exists():
                return request.make_response(
                    json.dumps({'success': False, 'message': 'Deployment not found'}),
                    headers=[('Content-Type', 'application/json')]
                )

            update_vals = {'status': status}

            if completed:
                try:
                    update_vals['completed'] = fields.Datetime.from_string(completed)
                except Exception:
                    update_vals['completed'] = fields.Datetime.now()
            elif status in ['success', 'failed']:
                update_vals['completed'] = fields.Datetime.now()

            if error_message:
                update_vals['error_message'] = error_message

            deployment.write(update_vals)

            _logger.info(f"[APP DEPLOYMENT] Deployment {deployment_id} status updated to: {status}")

            return request.make_response(
                json.dumps({'success': True, 'message': 'Status updated'}),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            _logger.error(f"[APP DEPLOYMENT] Error reporting status: {e}", exc_info=True)
            return request.make_response(
                json.dumps({'success': False, 'message': str(e)}),
                headers=[('Content-Type', 'application/json')]
            )