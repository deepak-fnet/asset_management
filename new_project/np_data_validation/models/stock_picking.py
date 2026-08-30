from odoo import api, models

from .np_mixin import check_not_past, check_order


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    @api.constrains('scheduled_date', 'date_deadline', 'state')
    def _np_check_dates(self):
        for picking in self:
            if picking.state in ('draft', 'waiting', 'confirmed', 'assigned'):
                check_not_past(picking, 'scheduled_date', "Scheduled Date")
            check_order(picking, 'scheduled_date', 'date_deadline',
                        "Scheduled Date", "Deadline")
