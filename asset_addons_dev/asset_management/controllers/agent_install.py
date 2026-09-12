"""Bootstrap installers for the asset agents.

Serves a ready-to-run install script for Windows and Ubuntu, in the style of
Elastic Agent's one-liner: the admin runs a single command on the machine and
it downloads, configures and registers the agent itself.

The point of rendering these server-side rather than shipping static files is
that the server URL and database are substituted in from the request. Whoever
runs the one-liner has already typed the Odoo address into it, so there is
nothing left to configure on the endpoint - which is what makes it a one-liner
rather than "download this, then edit that".
"""
import os

from odoo import http
from odoo.http import request
from odoo.tools import config as odoo_config

_MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INSTALLER_DIR = os.path.join(_MODULE_DIR, 'agent_installers')

# Stable, unversioned names under the existing /downloads/ route, so the
# installers never need editing when a new agent build is published - staging
# a new file under the same name is the whole release step.
AGENT_EXE = 'AssetAgent_latest.exe'
AGENT_DEB = 'asset-agent_latest.deb'
NSSM_EXE = 'nssm.exe'


class AssetAgentInstaller(http.Controller):

    def _base_url(self, override=None):
        """The Odoo URL to bake into the script.

        Defaults to the host the request came in on - i.e. exactly what the
        admin typed - because that is reachable from the endpoint by
        definition, which `web.base.url` is not always. `?url=` overrides it
        for the case where agents must talk to a different address than the
        one the installer was fetched from.
        """
        if override:
            return override.rstrip('/')
        return request.httprequest.host_url.rstrip('/')

    def _database(self, override=None):
        # request.db is None when no database is bound to the request (a
        # dbfilter that does not resolve, for instance), so fall back to the
        # server's configured default before giving up.
        return (override or request.db or odoo_config.get('db_name') or '').strip()

    def _render(self, filename, content_type, **kwargs):
        base = self._base_url(kwargs.get('url'))
        database = self._database(kwargs.get('db'))

        # The elevated relaunch on Windows re-fetches this same URL, so it has
        # to carry the database explicitly - the second process is a fresh
        # request and would otherwise have to resolve it again.
        bootstrap = '%s/agent/%s?db=%s' % (base, filename, database)

        with open(os.path.join(_INSTALLER_DIR, filename), encoding='utf-8') as handle:
            body = handle.read()

        for placeholder, value in (
            ('__SERVER_URL__', base),
            ('__DATABASE__', database),
            ('__BOOTSTRAP_URL__', bootstrap),
            ('__AGENT_URL__', '%s/downloads/%s' % (base, AGENT_EXE)),
            ('__DEB_URL__', '%s/downloads/%s' % (base, AGENT_DEB)),
            ('__NSSM_URL__', '%s/downloads/%s' % (base, NSSM_EXE)),
        ):
            body = body.replace(placeholder, value)

        return request.make_response(body, headers=[
            ('Content-Type', content_type),
            # These are generated per-request; a cached copy pointing at a
            # stale host is worse than a second round trip.
            ('Cache-Control', 'no-store'),
        ])

    @http.route('/agent/install.ps1', type='http', auth='public',
                methods=['GET'], csrf=False)
    def install_windows(self, **kwargs):
        """powershell -ExecutionPolicy Bypass -Command "irm <odoo>/agent/install.ps1 | iex" """
        return self._render('install.ps1', 'text/plain; charset=utf-8', **kwargs)

    @http.route('/agent/install.sh', type='http', auth='public',
                methods=['GET'], csrf=False)
    def install_ubuntu(self, **kwargs):
        """curl -fsSL <odoo>/agent/install.sh | sudo bash"""
        return self._render('install.sh', 'text/plain; charset=utf-8', **kwargs)
