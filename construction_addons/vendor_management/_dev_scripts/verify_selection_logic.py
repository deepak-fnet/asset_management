import sys
import os

base_path = '/home/prasanna/dev/odoo/custom_addons/vendor_management'

def check_selection_logic():
    print("Checking vendor_quote.py logic...")
    with open(os.path.join(base_path, 'models/vendor_quote.py'), 'r') as f:
        content = f.read()
        if "others.write({'state': 'rejected'})" in content and "rec.write({'state': 'selected'})" in content:
            print("✅ action_select enforces single selection (rejects others)")
        else:
            print("❌ action_select logic MISSING OR INCOMPLETE")
        
        if "po.write({" in content and "'partner_id': False" in content and "'vendor_finalized': False" in content:
            print("✅ action_unselect resets PO fields correctly")
        else:
            print("❌ action_unselect logic MISSING OR INCOMPLETE")
            
        if "any_quote_selected = fields.Boolean(compute='_compute_any_quote_selected'" in content:
            print("✅ any_quote_selected computed field present")
        else:
            print("❌ any_quote_selected field MISSING")

    print("\nChecking purchase_order_views.xml...")
    with open(os.path.join(base_path, 'views/purchase_order_views.xml'), 'r') as f:
        content = f.read()
        if 'name="action_select"' in content and 'name="action_unselect"' in content:
            print("✅ PO view uses action_select/action_unselect buttons")
        else:
            print("❌ PO view logic NOT updated")

    print("\nChecking vendor_portal_templates.xml...")
    with open(os.path.join(base_path, 'views/portal/vendor_portal_templates.xml'), 'r') as f:
        content = f.read()
        if 'quote.state == \'rejected\'' in content and 'alert-danger' in content:
            print("✅ Portal detail alert handles rejected state")
        else:
            print("❌ Portal detail alert logic MISSING")
            
        if 'bg-danger' in content and 'quote.state == \'rejected\'' in content:
            print("✅ Portal list badge handles rejected state")
        else:
            print("❌ Portal list badge logic MISSING")

if __name__ == "__main__":
    check_selection_logic()
