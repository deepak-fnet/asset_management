"""Daily alerts: warranty / AMC and licence expiry, repairs stuck too long.

Each item is told only on milestone days (30, 15, 7, 1 and 0 days left), so a
30-day window does not ring every morning for the same asset.
"""
from datetime import timedelta

from odoo import _, api, fields, models

from .notify_mixin import IT, MANAGER

MILESTONES = (30, 15, 7, 1, 0)
REPAIR_STUCK_DAYS = (7, 14, 30)


class AssetAssetAlerts(models.Model):
    _inherit = 'asset.asset'

    @api.model
    def _sn_cron_daily(self):
        today = fields.Date.today()
        mgr = self._sn_users(MANAGER, IT)

        # warranty / AMC
        assets = self.sudo().search([('warranty_end_date', '>=', today),
                                     ('warranty_end_date', '<=', today + timedelta(days=max(MILESTONES))),
                                     ('state', '!=', 'scrapped')])
        for a in assets:
            days = (a.warranty_end_date - today).days
            if days not in MILESTONES:
                continue
            amc = bool(getattr(a, 'is_amc', False))
            what = _('AMC') if amc else _('Warranty')
            title = (_('%(w)s ends today: %(a)s') if days == 0 else _('%(w)s ends in %(d)s days: %(a)s')) % {
                'w': what, 'd': days, 'a': a.display_name}
            holder = a.assigned_employee_id.user_id if a.assigned_employee_id else self.env['res.users']
            a._sn(mgr, title, [(_('Asset'), a.display_name), (_('Ends on'), a._sn_date(a.warranty_end_date)),
                               (_('Provider'), a.warranty_provider_id.display_name if 'warranty_provider_id' in a._fields else ''),
                               (_('With'), a.assigned_employee_id.display_name)],
                  kind='warning' if days > 7 else 'danger', icon='fa-shield',
                  priority='high' if days <= 7 else 'normal', keep_actor=True)

        # licences
        if 'asset.license' in self.env:
            lics = self.env['asset.license'].sudo().search([
                ('expiry_date', '>=', today), ('expiry_date', '<=', today + timedelta(days=max(MILESTONES))),
                ('active', '=', True)])
            for lic in lics:
                days = (lic.expiry_date - today).days
                if days not in MILESTONES:
                    continue
                self.env['smart.notification'].sudo().notify(
                    lic, mgr, (_('Licence ends today: %(l)s') if days == 0 else _('Licence ends in %(d)s days: %(l)s'))
                    % {'d': days, 'l': lic.display_name},
                    body=a_body(self, [(_('Ends on'), self._sn_date(lic.expiry_date)),
                                       (_('Seats in use'), getattr(lic, 'assigned_seats', '')),
                                       (_('Auto-renew'), _('Yes') if getattr(lic, 'auto_renew', False) else _('No'))]),
                    kind='warning' if days > 7 else 'danger', icon='fa-key',
                    priority='high' if days <= 7 else 'normal')

        # repairs stuck in progress
        Repair = self.env['repair.management'].sudo()
        for r in Repair.search([('state', '=', 'in_progress')]):
            started = (r.write_date or r.create_date).date()
            days = (today - started).days
            if days not in REPAIR_STUCK_DAYS:
                continue
            r._sn(mgr | r._sn_users(r.engineer_id), _('Repair stuck for %(d)s days: %(a)s') % {
                'd': days, 'a': r.asset_id.display_name}, r._sn_rows(), kind='alert', icon='fa-hourglass-half',
                priority='high', keep_actor=True)
        return True


def a_body(rec, rows):
    return rec.env['asset.sn.mixin']._sn_body(rows)
