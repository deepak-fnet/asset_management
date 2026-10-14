"""Vendors already approved get their vendor code now, in creation order."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    vendors = env['res.partner'].with_context(active_test=False).search(
        [('is_vendor', '=', True), ('vendor_state', 'in', ('active', 'blocked')), ('vendor_code', '=', False)],
        order='id')
    vendors._assign_vendor_code()
