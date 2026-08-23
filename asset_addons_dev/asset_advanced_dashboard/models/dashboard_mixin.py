from odoo import models, api
from datetime import date, timedelta


class AssetDashboardMixin(models.AbstractModel):
    """Mixin providing dashboard calculation methods for assets"""
    _name = 'asset.dashboard.mixin'
    _description = 'Asset Dashboard Mixin'

    @api.model
    def get_dashboard_summary(self):
        """Get summary data for dashboard"""
        Asset = self.env['asset.addition']
        today = date.today()
        
        return {
            'total_assets': Asset.search_count([]),
            'active_assets': Asset.search_count([('state', '=', 'done')]),
            'pending_assets': Asset.search_count([('state', 'in', ['submit', 'dept_approved', 'incharge_approved', 'cfo_approved'])]),
            'draft_assets': Asset.search_count([('state', '=', 'draft')]),
        }
