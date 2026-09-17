import sys
import os

# Base directory for Odoo
base_path = '/home/prasanna/dev/odoo/custom_addons/vendor_management'
sys.path.insert(0, base_path)

# Mocking Odoo because we don't have a live environment to run full Odoo tests easily without complex setup
# Instead, I will verify the logic by importing the models and checking the methods if possible, 
# or I will use the browser subagent for a real end-to-end test.

# Given the constraints, a manual verification with browser_subagent is more reliable for Odoo 18.
# But I can write a script to check if the fields are present in the files.

def check_fields():
    print("Checking fields in purchase_order.py...")
    with open(os.path.join(base_path, 'models/purchase_order.py'), 'r') as f:
        content = f.read()
        if 'rfq_revision = fields.Integer' in content and 'rfq_last_update = fields.Datetime' in content:
            print("✅ Fields present in purchase.order")
        else:
            print("❌ Fields MISSING in purchase.order")
        
        if 'vals[\'rfq_revision\'] = rec.rfq_revision + 1' in content:
            print("✅ Revision increment logic present in purchase.order write")
        else:
            print("❌ Revision increment logic MISSING in purchase.order write")

    print("\nChecking fields in vendor_quote.py...")
    with open(os.path.join(base_path, 'models/vendor_quote.py'), 'r') as f:
        content = f.read()
        if 'po_line_id = fields.Many2one' in content:
            print("✅ po_line_id present in vendor.quote.line")
        else:
            print("❌ po_line_id MISSING in vendor.quote.line")
        
        if "related='po_line_id.product_qty'" in content:
            print("✅ product_qty is now a related field")
        else:
            print("❌ product_qty is NOT a related field")

    print("\nChecking portal controller logic...")
    with open(os.path.join(base_path, 'controllers/vendor_portal.py'), 'r') as f:
        content = f.read()
        if 'for line in order.order_line:' in content and 'ql.po_line_id == line' in content:
            print("✅ Controller now loops over order.order_line and maps via po_line_id")
        else:
            print("❌ Controller logic for line mapping is WRONG")

    print("\nChecking portal template logic...")
    with open(os.path.join(base_path, 'views/portal/vendor_portal_templates.xml'), 'r') as f:
        content = f.read()
        if 't-foreach="order.order_line"' in content and 'ql.po_line_id == line' in content:
            print("✅ Template now loops over order.order_line and matches via po_line_id")
        else:
            print("❌ Template loop logic is WRONG")

if __name__ == "__main__":
    check_fields()
