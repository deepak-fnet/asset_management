from odoo import models
from odoo.tools import SQL


class PurchaseReport(models.Model):
    _inherit = 'purchase.report'

    def _where(self) -> SQL:
        # A split RFQ ('ordered') carries the same lines as the purchase
        # orders created from it - counting both would double every purchase.
        return SQL("%s AND po.state != 'ordered'", super()._where())
