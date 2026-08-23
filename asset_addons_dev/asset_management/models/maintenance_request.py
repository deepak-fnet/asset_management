from odoo import models, fields, api

class MaintenanceRequest(models.Model):
    _inherit = 'maintenance.request'

    asset_id = fields.Many2one(
        'asset.asset',
        string='Asset',
        index=True
    )

    def write(self, vals):
        res = super().write(vals)
        if 'stage_id' in vals:
            for request in self:
                if request.stage_id.name == 'Repaired' and request.asset_id:
                    request.asset_id.write({
                        'last_maintenance_date': fields.Date.today(),
                        'state': 'assigned'
                    })
        return res
