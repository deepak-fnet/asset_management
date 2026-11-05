import hashlib
import json
import os

from odoo import http
from odoo.http import request

AGENT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'agent')
LINUX_DIR = os.path.join(AGENT_DIR, 'linux')
LINUX_FILES = ('agent.py', 'agent_extensions.py', 'asset_remote_worker.py', 'os_release.py', 'security_audit.py',
               'asset-agent.service', 'asset-terminal.service', 'focus_extension.js', 'focus_metadata.json')


def _json(data, status=200):
    return request.make_response(json.dumps(data, default=str), status=status,
                                 headers=[('Content-Type', 'application/json')])


def agent_version():
    """Fingerprint of the agent files this Odoo serves - changes whenever any
    of them changes, which is what makes installed agents update themselves."""
    h = hashlib.sha256()
    for name in LINUX_FILES + ('../install_linux.sh',):
        with open(os.path.join(LINUX_DIR, name), 'rb') as fh:
            h.update(fh.read())
    return h.hexdigest()[:16]


class AgentInstallAPI(http.Controller):

    @http.route('/asset/agent/install.sh', type='http', auth='public', methods=['GET'], csrf=False)
    def install_sh(self, token=None, **kw):
        tok = request.env['asset.fleet.enrollment.token']._get_valid(token)
        if not tok:
            return request.make_response('echo "Invalid or disabled install token"; exit 1\n', status=403,
                                         headers=[('Content-Type', 'text/x-shellscript')])
        with open(os.path.join(AGENT_DIR, 'install_linux.sh'), encoding='utf-8') as fh:
            script = fh.read()
        script = (script.replace('__SERVER__', (tok.server_url or '').rstrip('/'))
                  .replace('__DB__', request.env.cr.dbname).replace('__TOKEN__', tok.token)
                  .replace('__FILES__', ' '.join(LINUX_FILES)).replace('__VERSION__', agent_version()))
        return request.make_response(script, headers=[('Content-Type', 'text/x-shellscript')])

    @http.route('/asset/agent/file/<string:name>', type='http', auth='public', methods=['GET'], csrf=False)
    def agent_file(self, name, token=None, **kw):
        if name not in LINUX_FILES or not request.env['asset.fleet.enrollment.token']._get_valid(token):
            return request.make_response('not found', status=404)
        with open(os.path.join(LINUX_DIR, name), 'rb') as fh:
            return request.make_response(fh.read(), headers=[('Content-Type', 'text/plain')])

    @http.route('/api/asset/agent/version', type='http', auth='public', methods=['GET'], csrf=False)
    def version(self, **kw):
        return _json({'success': True, 'version': agent_version()})

    # ---- Chrome location extension -------------------------------------------
    @http.route('/asset/agent/chrome/update.xml', type='http', auth='public', methods=['GET'], csrf=False)
    def chrome_update_xml(self, **kw):
        from ..models.fleet import LOCATION_EXTENSION_ID, LOCATION_EXTENSION_VERSION
        base = request.httprequest.host_url.rstrip('/')
        xml = ("<?xml version='1.0' encoding='UTF-8'?>\n"
               "<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>\n"
               "  <app appid='%s'>\n"
               "    <updatecheck codebase='%s/asset/agent/chrome/asset-location.crx?db=%s' version='%s' />\n"
               "  </app>\n</gupdate>\n") % (LOCATION_EXTENSION_ID, base, request.env.cr.dbname,
                                              LOCATION_EXTENSION_VERSION)
        return request.make_response(xml, headers=[('Content-Type', 'application/xml')])

    @http.route('/asset/agent/chrome/asset-location.crx', type='http', auth='public', methods=['GET'], csrf=False)
    def chrome_crx(self, **kw):
        with open(os.path.join(AGENT_DIR, 'chrome', 'asset-location.crx'), 'rb') as fh:
            return request.make_response(fh.read(), headers=[('Content-Type', 'application/x-chrome-extension')])

    @http.route('/api/asset/location/browser', type='http', auth='public', methods=['POST'], csrf=False)
    def browser_location(self, **kw):
        """Position from the Chrome extension (Chrome's Wi-Fi location service)."""
        try:
            data = json.loads(request.httprequest.data or b'{}')
        except ValueError:
            return _json({'success': False}, 400)
        if not request.env['asset.fleet.enrollment.token']._get_valid(data.get('token')):
            return _json({'success': False, 'message': 'invalid token'}, 403)
        serial = (data.get('serial_number') or '').strip()
        asset = request.env['asset.asset'].sudo().search([('serial_number', '=', serial)], limit=1) if serial else None
        if not asset:
            return _json({'success': False, 'message': 'unknown serial'}, 404)
        if data.get('error') or data.get('latitude') in (None, ''):
            return _json({'success': True, 'stored': False, 'message': data.get('error')})
        lat, lon = float(data['latitude']), float(data['longitude'])
        acc = float(data.get('accuracy') or 0)
        moved = abs((asset.latitude or 0) - lat) > 0.0005 or abs((asset.longitude or 0) - lon) > 0.0005
        vals = {'latitude': lat, 'longitude': lon, 'location_source': 'browser', 'location_accuracy': acc,
                'last_location_update': request.env.cr.now()}
        if moved or asset.location_source != 'browser':
            info = {}
            try:
                from odoo.addons.asset_management.controllers.asset_agent_api import reverse_geocode
                info = reverse_geocode(lat, lon) or {}
            except Exception:  # noqa: BLE001 - Nominatim down / rate limited: the daily cron fills it
                info = {}
            vals.update({'location_address': info.get('address', ''), 'location_city': info.get('city', ''),
                         'location_state': info.get('state', ''), 'location_country': info.get('country', '')})
            request.env['asset.location.history'].sudo().create({
                'asset_id': asset.id, 'latitude': lat, 'longitude': lon, 'source': 'Browser (Wi-Fi) +/-%dm' % acc,
                'city': info.get('city', ''), 'region': info.get('state', ''), 'country': info.get('country', ''),
                'full_address': info.get('address', '')})
        asset.write(vals)
        return _json({'success': True, 'stored': True})

    @http.route('/api/asset/screentime', type='http', auth='public', methods=['POST'], csrf=False)
    def screentime(self, **kw):
        try:
            data = json.loads(request.httprequest.data or b'{}')
        except ValueError:
            return _json({'success': False, 'message': 'bad json'}, 400)
        serial = (data.get('serial_number') or '').strip()
        asset = request.env['asset.asset'].sudo().search([('serial_number', '=', serial)], limit=1) if serial else None
        if not asset:
            return _json({'success': False, 'message': 'unknown serial'}, 404)
        request.env['asset.screen.time'].sudo()._ingest(asset, data)
        return _json({'success': True})

    @http.route('/api/asset/telemetry/policy', type='http', auth='public', methods=['GET'], csrf=False)
    def telemetry_policy(self, serial_number=None, **kw):
        serial = (serial_number or '').strip()
        asset = request.env['asset.asset'].sudo().search([('serial_number', '=', serial)], limit=1) if serial else None
        if not asset:
            return _json({'success': False, 'message': 'unknown serial'})
        policy = request.env['asset.fleet.policy'].sudo()._for_asset(asset)
        if not policy:
            return _json({'success': False, 'message': 'no policy'})
        return _json({'success': True, 'policy': policy._payload()})
