#!/usr/bin/env python3
"""
Model registration test - Simulates Odoo's model loading process
"""

import sys
import os

# Simulate Odoo environment
class FakeModels:
    class Model:
        pass

class FakeFields:
    @staticmethod
    def Many2one(*args, **kwargs):
        return "Many2one field"
    
    @staticmethod
    def One2many(*args, **kwargs):
        return "One2many field"
    
    @staticmethod
    def Many2many(*args, **kwargs):
        return "Many2many field"
        
    @staticmethod
    def Boolean(*args, **kwargs):
        return "Boolean field"
    
    @staticmethod
    def Char(*args, **kwargs):
        return "Char field"
    
    @staticmethod
    def Integer(*args, **kwargs):
        return "Integer field"
        
    @staticmethod
    def Text(*args, **kwargs):
        return "Text field"
        
    @staticmethod
    def Html(*args, **kwargs):
        return "Html field"

class FakeAPI:
    @staticmethod
    def depends(*args):
        def decorator(func):
            return func
        return decorator
    
    @staticmethod
    def model(func):
        return func

# Mock odoo
sys.modules['odoo'] = type('odoo', (), {})()
sys.modules['odoo'].models = FakeModels()
sys.modules['odoo'].fields = FakeFields()
sys.modules['odoo'].api = FakeAPI()
sys.modules['odoo.models'] = FakeModels
sys.modules['odoo.fields'] = FakeFields
sys.modules['odoo.api'] = FakeAPI

print("=" * 70)
print("MODEL REGISTRATION TEST")
print("=" * 70)
print()

# Test 1: Import intermediate model
print("1. Testing intermediate model import...")
try:
    sys.path.insert(0, '/home/prasanna/dev/odoo/custom_addons/vendor_management/models')
    import vendor_category_document_line
    
    # Check _name
    if hasattr(vendor_category_document_line.VendorCategoryDocumentLine, '_name'):
        model_name = vendor_category_document_line.VendorCategoryDocumentLine._name
        print(f"✅ Model _name: {model_name}")
        
        if model_name == 'vendor.category.document.line':
            print("✅ Model name is CORRECT")
        else:
            print(f"❌ Model name is WRONG: {model_name}")
    else:
        print("❌ _name attribute missing")
        
    # Check fields
    if hasattr(vendor_category_document_line.VendorCategoryDocumentLine, 'category_id'):
        print("✅ category_id field exists")
    else:
        print("❌ category_id field missing")
        
    if hasattr(vendor_category_document_line.VendorCategoryDocumentLine, 'document_type_id'):
        print("✅ document_type_id field exists")
    else:
        print("❌ document_type_id field missing")
        
    if hasattr(vendor_category_document_line.VendorCategoryDocumentLine, 'requires_expiry'):
        print("✅ requires_expiry field exists")
    else:
        print("❌ requires_expiry field missing")
        
except Exception as e:
    print(f"❌ Failed to import: {e}")
    import traceback
    traceback.print_exc()

print()

# Test 2: Import vendor_category
print("2. Testing vendor_category model import...")
try:
    import vendor_category
    
    # Check One2many field
    if hasattr(vendor_category.VendorCategory, 'mandatory_document_line_ids'):
        print("✅ mandatory_document_line_ids field exists")
        
        field = vendor_category.VendorCategory.mandatory_document_line_ids
        print(f"   Field type: {type(field)}")
    else:
        print("❌ mandatory_document_line_ids field missing")
        
except Exception as e:
    print(f"❌ Failed to import vendor_category: {e}")
    import traceback
    traceback.print_exc()

print()
print("=" * 70)
print("TEST COMPLETE")
print("=" * 70)
