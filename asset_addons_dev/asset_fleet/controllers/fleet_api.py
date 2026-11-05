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
