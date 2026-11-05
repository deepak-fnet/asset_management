# -*- coding: utf-8 -*-
from odoo import api, fields, models
from datetime import date, timedelta
import logging

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    # ── 180-Day fields ─────────────────────────────────────────────────────
    is_compliance_alert = fields.Boolean(
        string='180-Day Compliance Alert', default=False, copy=False,
        help='Set True automatically by cron when vendor bill is pending beyond 180 days.',
    )
    compliance_days_overdue = fields.Integer(
        string='Days Since Invoice', compute='_compute_compliance_days_overdue', store=False,
    )
    compliance_days_overdue_str = fields.Char(
        string='Days Pending', compute='_compute_compliance_days_overdue_str', store=False,
    )
    compliance_acknowledged      = fields.Boolean(string='180-Day Alert Acknowledged', default=False, copy=False)
    compliance_acknowledged_by   = fields.Many2one('res.users', string='180-Day Ack. By', readonly=True, copy=False)
    compliance_acknowledged_date = fields.Datetime(string='180-Day Ack. On', readonly=True, copy=False)
    compliance_snooze_until      = fields.Date(string='180-Day Snoozed Until', copy=False)

    # ── 45-Day MSME fields ─────────────────────────────────────────────────
    is_msme_alert          = fields.Boolean(string='45-Day MSME Alert', default=False, copy=False)
    msme_acknowledged      = fields.Boolean(string='MSME Alert Acknowledged', default=False, copy=False)
    msme_acknowledged_by   = fields.Many2one('res.users', string='MSME Ack. By', readonly=True, copy=False)
    msme_acknowledged_date = fields.Datetime(string='MSME Ack. On', readonly=True, copy=False)
    msme_snooze_until      = fields.Date(string='MSME Snoozed Until', copy=False)

    # Derived from the vendor's udyog_adhaar (MSME registration) field.
    # Any non-empty value means the vendor is MSME-registered — no manual toggle needed.
    is_msme_partner = fields.Boolean(
        string='MSME Vendor',
        compute='_compute_is_msme_partner',
        store=True,
        help='True when the vendor has a Udyog Aadhaar / Udyam number (account_extended).',
    )

    # ── Computed ────────────────────────────────────────────────────────────
    @api.depends('invoice_date', 'state')
    def _compute_compliance_days_overdue(self):
        today = date.today()
        for move in self:
            if move.invoice_date and move.move_type == 'in_invoice':
                move.compliance_days_overdue = (today - move.invoice_date).days
            else:
                move.compliance_days_overdue = 0

    @api.depends('invoice_date', 'state')
    def _compute_compliance_days_overdue_str(self):
        today = date.today()
        for move in self:
            if move.invoice_date and move.move_type == 'in_invoice':
                days = (today - move.invoice_date).days
                move.compliance_days_overdue_str = f"{days} days ago"
            else:
                move.compliance_days_overdue_str = ""

    @api.depends('partner_id', 'partner_id.udyog_adhaar')
    def _compute_is_msme_partner(self):
        """
        Reads udyog_adhaar from res.partner (models/res_partner.py).
        Any non-empty value means the partner is MSME-registered.
        """
        for move in self:
            move.is_msme_partner = bool(
                move.partner_id and move.partner_id.udyog_adhaar
            )

    # ── Cron: daily scan ────────────────────────────────────────────────────
    @api.model
    def _check_180_day_compliance(self):
        """
        Called daily by ir.cron.
        1. Flags MSME vendor bills pending 45+ days  → is_msme_alert = True
        2. Flags all vendor bills pending 180+ days  → is_compliance_alert = True
        3. Clears both flags when bill is fully paid
        """
        today         = date.today()
        threshold_180 = today - timedelta(days=180)
        threshold_45  = today - timedelta(days=45)

        base_domain = [
            ('move_type', '=', 'in_invoice'),
            ('state', '=', 'posted'),
            ('payment_state', 'in', ('not_paid', 'partial')),
        ]

        # 1. Flag 45-day MSME (partner has is_msme_vendor = True)
        msme_overdue = self.search(base_domain + [
            ('invoice_date', '<=', threshold_45),
            ('is_msme_partner', '=', True),
            ('is_msme_alert', '=', False),
        ])
        if msme_overdue:
            msme_overdue.write({'is_msme_alert': True})
            _logger.info('payment_compliance_alert: Flagged %d MSME bill(s) 45+ days.', len(msme_overdue))

        # 2. Flag 180-day general (all vendors)
        overdue_180 = self.search(base_domain + [
            ('invoice_date', '<=', threshold_180),
            ('is_compliance_alert', '=', False),
        ])
        if overdue_180:
            overdue_180.write({'is_compliance_alert': True})
            _logger.info('payment_compliance_alert: Flagged %d bill(s) 180+ days.', len(overdue_180))

        # 3. Clear paid bills
        cleared = self.search([
            ('move_type', '=', 'in_invoice'),
            ('payment_state', '=', 'paid'),
            '|',
            ('is_compliance_alert', '=', True),
            ('is_msme_alert', '=', True),
        ])
        if cleared:
            cleared.write({
                'is_compliance_alert': False, 'compliance_acknowledged': False,
                'is_msme_alert': False,       'msme_acknowledged': False,
            })
            _logger.info('payment_compliance_alert: Cleared flags on %d paid bill(s).', len(cleared))

        return True

    # ── RPC: systray count ──────────────────────────────────────────────────
    @api.model
    def get_compliance_alert_count(self):
        today = date.today()
        domain_180 = [
            ('move_type', '=', 'in_invoice'), ('state', '=', 'posted'),
            ('payment_state', 'in', ('not_paid', 'partial')),
            ('is_compliance_alert', '=', True), ('compliance_acknowledged', '=', False),
        ]
        domain_45 = [
            ('move_type', '=', 'in_invoice'), ('state', '=', 'posted'),
            ('payment_state', 'in', ('not_paid', 'partial')),
            ('is_msme_alert', '=', True), ('msme_acknowledged', '=', False),
        ]

        def bill_dict(b, alert_type):
            return {
                'id': b.id,
                'name': b.name,
                'partner_name': b.partner_id.name or '',
                'amount_residual': b.amount_residual,
                'currency_symbol': b.currency_id.symbol or '₹',
                'days_overdue': (today - b.invoice_date).days if b.invoice_date else 0,
                'alert_type': alert_type,
                'udyog_adhaar': b.partner_id.udyog_adhaar or '',
            }

        bills_45  = self.search(domain_45,  limit=10, order='invoice_date asc')
        bills_180 = self.search(domain_180, limit=10, order='invoice_date asc')
        count_45  = self.search_count(domain_45)
        count_180 = self.search_count(domain_180)

        return {
            'count':     count_45 + count_180,
            'count_45':  count_45,
            'count_180': count_180,
            'bills': [bill_dict(b, '45') for b in bills_45] + [bill_dict(b, '180') for b in bills_180],
        }

    # ── RPC: acknowledge ────────────────────────────────────────────────────
    @api.model
    def acknowledge_compliance_alert(self, move_id, alert_type='180'):
        move = self.browse(move_id)
        if not move.exists() or move.move_type != 'in_invoice':
            return {'success': False, 'error': 'Invalid bill.'}

        user = self.env.user
        now  = fields.Datetime.now()
        days_overdue = (date.today() - move.invoice_date).days if move.invoice_date else 0

        if alert_type == '45':
            move.write({
                'msme_acknowledged': True,
                'msme_acknowledged_by': user.id,
                'msme_acknowledged_date': now,
            })
            statutory_law = 'msme'
            law_text = 'MSME Act Section 15 (45-day payment rule)'
        else:
            move.write({
                'compliance_acknowledged': True,
                'compliance_acknowledged_by': user.id,
                'compliance_acknowledged_date': now,
            })
            statutory_law = 'both'
            law_text = 'MSME Act Section 15 and Income Tax Act Section 43B(h)'

        self.env['compliance.alert.log'].sudo().create({
            'move_id': move.id,
            'user_id': user.id,
            'acknowledged_date': now,
            'days_overdue': days_overdue,
            'statutory_law': statutory_law,
            'alert_type': alert_type,
            'amount_residual': move.amount_residual,
            'currency_id': move.currency_id.id,
            'udyog_adhaar': move.partner_id.udyog_adhaar or '',
        })

        move.message_post(
            body=(
                f'<b>&#9888; COMPLIANCE ALERT ACKNOWLEDGED</b><br/>'
                f'Pending for <b>{days_overdue} days</b>. '
                f'Obligations under <b>{law_text}</b>.<br/>'
                f'Acknowledged by: <b>{user.name}</b> on {now.strftime("%d/%m/%Y %H:%M:%S")}'
            ),
            subtype_xmlid='mail.mt_note',
        )
        _logger.info(
            'payment_compliance_alert: Bill %s [%s] acknowledged by %s (%d days)',
            move.name, alert_type, user.name, days_overdue,
        )
        return {'success': True}

    # ── RPC: snooze ─────────────────────────────────────────────────────────
    @api.model
    def snooze_compliance_alert(self, move_id, alert_type='180'):
        move = self.browse(move_id)
        if move.exists():
            snooze = date.today() + timedelta(days=1)
            if alert_type == '45':
                move.write({'msme_snooze_until': snooze})
            else:
                move.write({'compliance_snooze_until': snooze})
        return {'success': True}

    # ── RPC: should dialog show ──────────────────────────────────────────────
    @api.model
    def should_show_compliance_dialog(self, move_id):
        move = self.browse(move_id)
        if not move.exists():
            return False
        today = date.today()
        if move.is_msme_alert and not move.msme_acknowledged:
            if not (move.msme_snooze_until and move.msme_snooze_until >= today):
                return '45'
        if move.is_compliance_alert and not move.compliance_acknowledged:
            if not (move.compliance_snooze_until and move.compliance_snooze_until >= today):
                return '180'
        return False

