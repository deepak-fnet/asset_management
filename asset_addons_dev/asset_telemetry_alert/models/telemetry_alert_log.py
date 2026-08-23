# -*- coding: utf-8 -*-
"""
Alert log and the engine that fires rules when telemetry arrives.

State model
-----------
An alert is a *condition on an asset*, not an event. It has a lifecycle:

    active     — condition is currently true
    recovered  — condition cleared; a recovery mail was sent if configured
    muted      — someone decided not to hear about this one again

This matters because telemetry arrives every 15 minutes. Treating each
arrival as a fresh event produces 96 identical mails a day. Treating it as a
state means one mail when it starts, one when it stops, and a cooldown in
between if it persists.
"""

import logging
from datetime import timedelta

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AssetTelemetryAlertLog(models.Model):
    _name = 'asset.telemetry.alert.log'
    _description = 'Telemetry Alert'
    _order = 'first_seen desc, id desc'
    _rec_name = 'display_name'

    rule_id = fields.Many2one(
        'asset.telemetry.alert.rule', required=True,
        ondelete='cascade', index=True,
    )
    asset_id = fields.Many2one(
        'asset.asset', required=True, ondelete='cascade', index=True,
    )
    display_name = fields.Char(compute='_compute_display_name', store=True)

    severity = fields.Selection(related='rule_id.severity', store=True)
    json_path = fields.Char(related='rule_id.json_path', store=True)

    state = fields.Selection(
        [('active', 'Active'), ('recovered', 'Recovered'), ('muted', 'Muted')],
        default='active', required=True, index=True,
    )

    observed_value = fields.Char()
    threshold_value = fields.Char()

    first_seen = fields.Datetime(default=fields.Datetime.now, readonly=True)
    last_seen = fields.Datetime(default=fields.Datetime.now, readonly=True)
    recovered_at = fields.Datetime(readonly=True)

    occurrence_count = fields.Integer(
        default=1,
        help='How many telemetry reports have seen this condition true.',
    )
    mail_sent_count = fields.Integer(default=0)
    last_mail_sent = fields.Datetime(readonly=True)

    notes = fields.Text()

    @api.depends('rule_id', 'asset_id')
    def _compute_display_name(self):
        for log in self:
            log.display_name = (
                f'{log.asset_id.display_name or "?"} — '
                f'{log.rule_id.name or "?"}'
            )

    def action_mute(self):
        self.write({'state': 'muted'})
        return True

    def action_unmute(self):
        self.write({'state': 'active'})
        return True

    def action_open_asset(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'asset.asset',
            'res_id': self.asset_id.id,
            'view_mode': 'form',
        }


class AssetTelemetryAlertEngine(models.AbstractModel):
    _name = 'asset.telemetry.alert.engine'
    _description = 'Telemetry Alert Engine'

    # ══════════════════════════════════════════════════════════════════════
    # Entry point
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def evaluate_for_asset(self, asset, telemetry):
        """Run every active rule against one asset's fresh telemetry.

        Called from record_telemetry(). Wrapped by the caller so a failure
        here can never reject the telemetry itself — losing an alert is
        annoying, losing the payload is worse.
        """
        Rule = self.env['asset.telemetry.alert.rule'].sudo()
        Log = self.env['asset.telemetry.alert.log'].sudo()

        rules = Rule.search([('active', '=', True)])
        if not rules:
            return {'evaluated': 0, 'triggered': 0, 'mailed': 0}

        now = fields.Datetime.now()
        triggered_count = mailed_count = 0

        for rule in rules:
            try:
                fired, value, note = rule.evaluate(asset, telemetry)
            except Exception as exc:
                _logger.error('[TelemetryAlert] Rule "%s" raised: %s',
                              rule.name, exc, exc_info=True)
                continue

            existing = Log.search([
                ('rule_id', '=', rule.id),
                ('asset_id', '=', asset.id),
                ('state', 'in', ('active', 'muted')),
            ], limit=1)

            # ── Condition true ────────────────────────────────────────────
            if fired:
                triggered_count += 1

                if existing:
                    existing.write({
                        'last_seen': now,
                        'occurrence_count': existing.occurrence_count + 1,
                        'observed_value': str(value)[:255],
                    })
                    if existing.state == 'muted':
                        continue
                    log = existing
                else:
                    log = Log.create({
                        'rule_id': rule.id,
                        'asset_id': asset.id,
                        'observed_value': str(value)[:255],
                        'threshold_value': rule.threshold or '',
                        'state': 'active',
                        'first_seen': now,
                        'last_seen': now,
                    })
                    rule.write({
                        'trigger_count': rule.trigger_count + 1,
                        'last_triggered': now,
                    })

                if self._cooldown_elapsed(log, rule, now):
                    if self._send_alert_mail(rule, asset, log, value,
                                             recovery=False):
                        mailed_count += 1
                        log.write({
                            'mail_sent_count': log.mail_sent_count + 1,
                            'last_mail_sent': now,
                        })

            # ── Condition false, but an alert was open ────────────────────
            elif existing and existing.state == 'active':
                existing.write({'state': 'recovered', 'recovered_at': now})
                if rule.send_recovery_mail:
                    if self._send_alert_mail(rule, asset, existing, value,
                                             recovery=True):
                        mailed_count += 1

        return {
            'evaluated': len(rules),
            'triggered': triggered_count,
            'mailed': mailed_count,
        }

    # ══════════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def _cooldown_elapsed(self, log, rule, now):
        if rule.cooldown_hours <= 0:
            return True
        if not log.last_mail_sent:
            return True
        return now >= log.last_mail_sent + timedelta(hours=rule.cooldown_hours)

    @api.model
    def _build_placeholders(self, rule, asset, log, value, recovery):
        base_url = self.env['ir.config_parameter'].sudo().get_param(
            'web.base.url', '')
        return {
            'asset_name': asset.display_name or '',
            'asset_serial': asset.serial_number or '',
            'asset_hostname': getattr(asset, 'hostname', '') or '',
            'rule_name': rule.name or '',
            'json_path': rule.json_path or (rule.expression or ''),
            'value': str(value) if value is not None else 'n/a',
            'threshold': rule.threshold or '',
            'severity': dict(rule._fields['severity'].selection).get(
                rule.severity, rule.severity),
            'record_url': f'{base_url}/web#id={asset.id}'
                          f'&model=asset.asset&view_type=form',
            'first_seen': log.first_seen.strftime('%d %b %Y %H:%M')
                          if log.first_seen else '',
            'occurrence_count': log.occurrence_count,
            'status': 'RECOVERED' if recovery else 'TRIGGERED',
        }

    @api.model
    def _send_alert_mail(self, rule, asset, log, value, recovery=False):
        """Render and queue the mail. Returns True on success."""
        if not rule.mail_to:
            return False

        placeholders = self._build_placeholders(rule, asset, log, value,
                                                recovery)

        subject_template = rule.email_subject or \
            'Asset Alert: {asset_name} — {rule_name}'
        if recovery:
            subject_template = 'RECOVERED: ' + subject_template

        try:
            subject = subject_template.format(**placeholders)
        except (KeyError, IndexError, ValueError) as exc:
            _logger.warning('[TelemetryAlert] Subject placeholder error on '
                            '"%s": %s', rule.name, exc)
            subject = f'Asset Alert: {asset.display_name} — {rule.name}'

        if recovery:
            body = (
                f'<p>The condition on <b>{placeholders["asset_name"]}</b> '
                f'has cleared.</p>'
                f'<table style="border-collapse:collapse;">'
                f'<tr><td style="padding:4px 12px 4px 0;"><b>Rule</b></td>'
                f'<td>{placeholders["rule_name"]}</td></tr>'
                f'<tr><td style="padding:4px 12px 4px 0;"><b>Path</b></td>'
                f'<td>{placeholders["json_path"]}</td></tr>'
                f'<tr><td style="padding:4px 12px 4px 0;"><b>Now</b></td>'
                f'<td>{placeholders["value"]}</td></tr>'
                f'<tr><td style="padding:4px 12px 4px 0;">'
                f'<b>First seen</b></td>'
                f'<td>{placeholders["first_seen"]}</td></tr>'
                f'<tr><td style="padding:4px 12px 4px 0;">'
                f'<b>Occurrences</b></td>'
                f'<td>{placeholders["occurrence_count"]}</td></tr>'
                f'</table>'
                f'<p><a href="{placeholders["record_url"]}">'
                f'Open the asset in Odoo</a></p>'
            )
        else:
            try:
                body = (rule.email_content or '').format(**placeholders)
            except (KeyError, IndexError, ValueError) as exc:
                _logger.warning('[TelemetryAlert] Body placeholder error on '
                                '"%s": %s — sending unformatted',
                                rule.name, exc)
                body = rule.email_content or ''

        try:
            mail = self.env['mail.mail'].sudo().create({
                'subject': subject,
                'body_html': body,
                'email_to': rule.mail_to,
                'auto_delete': False,
            })
            mail.send()
            if mail.state == 'exception':
                _logger.error('[TelemetryAlert] Mail failed for "%s": %s',
                              rule.name, mail.failure_reason)
                return False
            _logger.info('[TelemetryAlert] %s mail sent for "%s" on %s',
                         'Recovery' if recovery else 'Alert',
                         rule.name, asset.display_name)
            return True
        except Exception as exc:
            _logger.error('[TelemetryAlert] Could not send mail for "%s": %s',
                          rule.name, exc, exc_info=True)
            return False

    # ══════════════════════════════════════════════════════════════════════
    # Manual sweep
    # ══════════════════════════════════════════════════════════════════════
    @api.model
    def cron_evaluate_all(self):
        """Re-evaluate every asset's stored telemetry.

        Rules normally fire when telemetry arrives. This exists for the case
        where a rule is added or edited after the fact — otherwise it would
        not fire until the next report, up to 15 minutes later, and a newly
        written rule would appear broken.
        """
        assets = self.env['asset.asset'].sudo().search(
            [('agent_telemetry', '!=', False)])
        total = {'assets': 0, 'triggered': 0, 'mailed': 0}
        for asset in assets:
            try:
                res = self.evaluate_for_asset(asset, asset.agent_telemetry)
                total['assets'] += 1
                total['triggered'] += res['triggered']
                total['mailed'] += res['mailed']
            except Exception as exc:
                _logger.error('[TelemetryAlert] Sweep failed on %s: %s',
                              asset.display_name, exc)
        _logger.info('[TelemetryAlert] Sweep: %s asset(s), %s triggered, '
                     '%s mailed', total['assets'], total['triggered'],
                     total['mailed'])
        return True
