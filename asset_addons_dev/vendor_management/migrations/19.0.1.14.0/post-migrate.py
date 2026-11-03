from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Ratings made from a bill had no PO Reference: fill it from the bill lines."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['vendor.performance.overview'].search(
        [('move_id', '!=', False), ('purchase_id', '=', False)])._fill_purchase_from_bill()
