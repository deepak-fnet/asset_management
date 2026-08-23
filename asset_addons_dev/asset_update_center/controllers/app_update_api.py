# -*- coding: utf-8 -*-
"""
Agent API for third-party application updates.

Mirrors the existing OS-update loop so the agent code looks familiar:

    POST /api/asset/app_updates/report        agent -> server  (what's outdated)
    GET  /api/asset/app_updates/instructions  agent <- server  (what to upgrade)
    POST /api/asset/app_updates/result        agent -> server  (what happened)

Also adds a platform-correct OS update endpoint, because the existing
/api/asset/updates/report writes everything to asset.windows.update regardless
of the reporting machine's platform. Linux agents should post here instead.
"""

import json
import logging

from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)

OS_MODEL_BY_PLATFORM = {
    'windows': ('asset.windows.update', 'kb_number'),
    'linux': ('asset.linux.update', 'package_name'),
    'macos': ('asset.macos.update', 'package_name'),
}


def _json(data):
    return request.make_response(
        json.dumps(data), headers=[('Content-Type', 'application/json')]
    )


def _find_asset(serial_number):
    if not serial_number:
        return None
    return request.env['asset.asset'].sudo().search(
        [('serial_number', '=', serial_number)], limit=1
    )


class AppUpdateAPI(http.Controller):

    # ══════════════════════════════════════════════════════════════════════
    # App updates
    # ══════════════════════════════════════════════════════════════════════
    @http.route('/api/asset/app_updates/report', type='http', auth='public',
                methods=['POST'], csrf=False)
    def app_updates_report(self, **kwargs):
        """Agent reports outdated applications.

        Body:
            {
              "serial_number": "PF2Q10DW",
              "source": "winget",
              "updates": [
                {"app_name": "Google Chrome", "package_id": "Google.Chrome",
                 "installed_version": "141.0.7390.55",
                 "available_version": "142.0.7444.20"},
                ...
              ]
            }
        """
        try:
            payload = json.loads(request.httprequest.data or '{}')
            asset = _find_asset((payload.get('serial_number') or '').strip())
            if not asset:
                return _json({'success': False, 'message': 'Asset not found'})

            source = payload.get('source') or 'winget'
            reported = payload.get('updates') or []

            AppUpdate = request.env['asset.app.update'].sudo()
            today = fields.Date.today()
            created = refreshed = 0
            seen_names = []

            for item in reported:
                app_name = (item.get('app_name') or '').strip()
                if not app_name:
                    continue
                seen_names.append(app_name)

                existing = AppUpdate.search([
                    ('asset_id', '=', asset.id),
                    ('app_name', '=', app_name),
                ], limit=1)

                vals = {
                    'package_id': (item.get('package_id') or '')[:128] or False,
                    'source': source,
                    'installed_version': (item.get('installed_version') or '')[:64],
                    'available_version': (item.get('available_version') or '')[:64],
                    'detected_date': today,
                }

                if existing:
                    # Never clobber a queued/updating row — the agent is mid-job.
                    if existing.status in ('queued', 'updating', 'blocked'):
                        existing.write({'detected_date': today})
                    else:
                        vals['status'] = 'available'
                        existing.write(vals)
                    refreshed += 1
                else:
                    vals.update({
                        'asset_id': asset.id,
                        'app_name': app_name[:128],
                        'status': 'available',
                        'first_detected_date': today,
                    })
                    AppUpdate.create(vals)
                    created += 1

            # Anything previously flagged but no longer reported has been
            # updated by some other means — close it out rather than leaving
            # a stale row on the dashboard.
            if seen_names:
                stale = AppUpdate.search([
                    ('asset_id', '=', asset.id),
                    ('status', '=', 'available'),
                    ('app_name', 'not in', seen_names),
                ])
                if stale:
                    stale.write({'status': 'updated'})

            _logger.info('[AppUpdate] %s reported %s outdated app(s)',
                         asset.display_name, len(reported))
            return _json({'success': True, 'created': created,
                          'refreshed': refreshed})

        except Exception as exc:
            _logger.error('[AppUpdate] report error: %s', exc, exc_info=True)
            return _json({'success': False, 'message': str(exc)})

    @http.route('/api/asset/app_updates/instructions', type='http',
                auth='public', methods=['GET'], csrf=False)
    def app_updates_instructions(self, serial_number=None, **kwargs):
        """Agent asks which applications to upgrade."""
        try:
            asset = _find_asset((serial_number or '').strip())
            if not asset:
                return _json({'success': False, 'message': 'Asset not found'})

            AppUpdate = request.env['asset.app.update'].sudo()
            queued = AppUpdate.search([
                ('asset_id', '=', asset.id), ('status', '=', 'queued'),
            ])
            blocked = AppUpdate.search([
                ('asset_id', '=', asset.id), ('status', '=', 'blocked'),
            ])

            # Mark as in-flight so a second poll does not double-trigger.
            if queued:
                queued.write({'status': 'updating'})

            return _json({
                'success': True,
                'upgrade_list': [{
                    'app_name': u.app_name,
                    'package_id': u.package_id or '',
                    'source': u.source or 'winget',
                } for u in queued],
                'blocklist': [u.app_name for u in blocked],
            })

        except Exception as exc:
            _logger.error('[AppUpdate] instructions error: %s', exc, exc_info=True)
            return _json({'success': False, 'message': str(exc)})

    @http.route('/api/asset/app_updates/result', type='http', auth='public',
                methods=['POST'], csrf=False)
    def app_updates_result(self, **kwargs):
        """Agent reports the outcome of an upgrade attempt.

        Body:
            {"serial_number": "...", "app_name": "Google Chrome",
             "success": true, "new_version": "142.0.7444.20", "error": ""}
        """
        try:
            payload = json.loads(request.httprequest.data or '{}')
            asset = _find_asset((payload.get('serial_number') or '').strip())
            if not asset:
                return _json({'success': False, 'message': 'Asset not found'})

            app_name = (payload.get('app_name') or '').strip()
            rec = request.env['asset.app.update'].sudo().search([
                ('asset_id', '=', asset.id), ('app_name', '=', app_name),
            ], limit=1)
            if not rec:
                return _json({'success': False, 'message': 'Update row not found'})

            ok = bool(payload.get('success'))
            vals = {'status': 'updated' if ok else 'failed'}
            if ok and payload.get('new_version'):
                vals['installed_version'] = payload['new_version'][:64]
                vals['error_message'] = False
            if not ok:
                vals['error_message'] = (payload.get('error') or 'Unknown error')[:500]

            rec.write(vals)
            _logger.info('[AppUpdate] %s on %s -> %s',
                         app_name, asset.display_name, vals['status'])
            return _json({'success': True})

        except Exception as exc:
            _logger.error('[AppUpdate] result error: %s', exc, exc_info=True)
            return _json({'success': False, 'message': str(exc)})

    # ══════════════════════════════════════════════════════════════════════
    # Platform-correct OS update reporting
    # ══════════════════════════════════════════════════════════════════════
    @http.route('/api/asset/os_updates/report', type='http', auth='public',
                methods=['POST'], csrf=False)
    def os_updates_report(self, **kwargs):
        """Platform-aware replacement for /api/asset/updates/report.

        The original endpoint writes to asset.windows.update unconditionally,
        so Linux agents posting to it end up with Ubuntu package names stored
        as Windows KB numbers. This routes to the correct model based on the
        asset's platform.

        Body:
            {
              "serial_number": "...",
              "updates":      [{"identifier": "...", "title": "...",
                                "severity": "security", "size": "...",
                                "version": "..."}],
              "installed":    ["identifier", ...]
            }
        """
        try:
            payload = json.loads(request.httprequest.data or '{}')
            asset = _find_asset((payload.get('serial_number') or '').strip())
            if not asset:
                return _json({'success': False, 'message': 'Asset not found'})

            spec = OS_MODEL_BY_PLATFORM.get(asset.platform)
            if not spec:
                return _json({'success': False,
                              'message': f'No update model for platform '
                                         f'"{asset.platform or "unknown"}"'})

            model_name, id_field = spec
            if model_name not in request.env:
                return _json({'success': False,
                              'message': f'Model {model_name} not installed'})

            Model = request.env[model_name].sudo()
            today = fields.Date.today()
            created = skipped = installed_marked = 0

            for upd in payload.get('updates') or []:
                ident = (upd.get('identifier') or '').strip()
                if not ident:
                    continue

                existing = Model.search([
                    ('asset_id', '=', asset.id), (id_field, '=', ident),
                ], limit=1)

                if existing:
                    if existing.status == 'pending':
                        existing.write({'detected_date': today})
                    skipped += 1
                else:
                    vals = {
                        'asset_id': asset.id,
                        id_field: ident,
                        'title': (upd.get('title') or ident)[:255],
                        'severity': upd.get('severity') or 'optional',
                        'size': upd.get('size') or '',
                        'detected_date': today,
                        'status': 'pending',
                    }
                    # first_detected_date exists only if the earlier patch is
                    # installed; set it defensively.
                    if 'first_detected_date' in Model._fields:
                        vals['first_detected_date'] = today
                    if 'description' in Model._fields:
                        vals['description'] = (upd.get('description') or '')[:500]
                    Model.create(vals)
                    created += 1

            for ident in payload.get('installed') or []:
                ident = (ident or '').strip()
                if not ident:
                    continue
                rec = Model.search([
                    ('asset_id', '=', asset.id), (id_field, '=', ident),
                ], limit=1)
                if rec and rec.status not in ('installed', 'uninstalled'):
                    rec.write({'status': 'installed'})
                    installed_marked += 1

            _logger.info('[OSUpdate] %s (%s) — %s new, %s known, %s installed',
                         asset.display_name, asset.platform,
                         created, skipped, installed_marked)
            return _json({'success': True, 'platform': asset.platform,
                          'created': created, 'skipped': skipped,
                          'installed_marked': installed_marked})

        except Exception as exc:
            _logger.error('[OSUpdate] report error: %s', exc, exc_info=True)
            return _json({'success': False, 'message': str(exc)})
