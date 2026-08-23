from odoo import api, fields, models, _
from odoo.tests.common import TransactionCase

def check_data(env):
    Vendor = env['res.partner']
    Category = env['res.partner.category']
    VendorCategory = env['vendor.category']
    
    print(f"Total Partners: {Vendor.search_count([])}")
    print(f"Total Vendors (is_vendor=True): {Vendor.search_count([('is_vendor', '=', True)])}")
    print(f"Total Vendors (supplier_rank > 0): {Vendor.search_count([('supplier_rank', '>', 0)])}")
    
    print(f"Total Res Partner Categories (Tags): {Category.search_count([])}")
    print(f"Total Custom Vendor Categories: {VendorCategory.search_count([])}")
    
    vendors_with_tags = Vendor.search_count([('is_vendor', '=', True), ('category_id', '!=', False)])
    vendors_with_custom_cats = Vendor.search_count([('is_vendor', '=', True), ('vendor_category_ids', '!=', False)])
    
    print(f"Vendors with Tags: {vendors_with_tags}")
    print(f"Vendors with Custom Categories: {vendors_with_custom_cats}")

# This script is meant to be run via odoo shell or similar context
# I'll use it to understand the data state.
