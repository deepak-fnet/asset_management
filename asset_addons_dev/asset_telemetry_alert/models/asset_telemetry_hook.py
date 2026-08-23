# -*- coding: utf-8 -*-
"""
Hook alert evaluation into telemetry ingest.

Deliberately wrapped in try/except: a broken alert rule must never cause the
telemetry POST to fail. Losing an alert is annoying; rejecting the payload
means losing the data entirely, and the agent has already moved on.
"""

import logging

from odoo import models, api

_logger = logging.getLogger(__name__)


class AssetAssetAlertHook(models.Model):
    _inherit = 'asset.asset'

    def record_telemetry(self, payload, keep_snapshot=True):
        result = super().record_telemetry(payload, keep_snapshot=keep_snapshot)

        try:
            engine = self.env['asset.telemetry.alert.engine']
            stats = engine.evaluate_for_asset(self, payload)
            if stats['triggered']:
                _logger.info(
                    '[TelemetryAlert] %s — %s rule(s) triggered, %s mail(s)',
                    self.display_name, stats['triggered'], stats['mailed'])
        except Exception as exc:
            _logger.error('[TelemetryAlert] Evaluation failed for %s: %s',
                          self.display_name, exc, exc_info=True)

        return result

    def action_evaluate_alerts_now(self):
        """Re-run rules against the stored payload, without waiting for the
        next report."""
        self.ensure_one()
        if not self.agent_telemetry:
            from odoo.exceptions import UserError
            raise UserError('This asset has not reported telemetry yet.')

        stats = self.env['asset.telemetry.alert.engine'].evaluate_for_asset(
            self, self.agent_telemetry)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Alerts Evaluated',
                'message': (
                    f"{stats['evaluated']} rule(s) checked, "
                    f"{stats['triggered']} triggered, "
                    f"{stats['mailed']} mail(s) sent."
                ),
                'type': 'warning' if stats['triggered'] else 'success',
            },
        }
