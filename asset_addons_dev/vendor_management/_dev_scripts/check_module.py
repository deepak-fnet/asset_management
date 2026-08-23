#!/usr/bin/env python3
"""
Diagnostic script to verify vendor_management module structure
for category-based expiry configuration.
"""

import os
import sys

def check_file_exists(path, description):
    """Check if a file exists."""
    exists = os.path.exists(path)
    status = "✅" if exists else "❌"
    print(f"{status} {description}: {path}")
    return exists

def check_file_content(path, search_string, description):
    """Check if file contains specific string."""
    try:
        with open(path, 'r') as f:
            content = f.read()
            found = search_string in content
            status = "✅" if found else "❌"
            print(f"{status} {description}")
            return found
    except:
        print(f"❌ Could not read {path}")
        return False

print("=" * 60)
print("VENDOR MANAGEMENT MODULE DIAGNOSTIC")
print("=" * 60)
print()

base_path = "/home/prasanna/dev/odoo/custom_addons/vendor_management"

print("1. MODEL FILES")
print("-" * 60)
check_file_exists(
    f"{base_path}/models/vendor_category_document_line.py",
    "Intermediate model file"
)
check_file_content(
    f"{base_path}/models/vendor_category_document_line.py",
    "_name = 'vendor.category.document.line'",
    "Model _name is correct"
)
check_file_content(
    f"{base_path}/models/__init__.py",
    "from . import vendor_category_document_line",
    "Model imported in __init__.py"
)
print()

print("2. VENDOR CATEGORY MODEL")
print("-" * 60)
check_file_content(
    f"{base_path}/models/vendor_category.py",
    "mandatory_document_line_ids = fields.One2many",
    "One2many field exists"
)
check_file_content(
    f"{base_path}/models/vendor_category.py",
    "comodel_name='vendor.category.document.line'",
    "Comodel name is correct"
)
check_file_content(
    f"{base_path}/models/vendor_category.py",
    "inverse_name='category_id'",
    "Inverse name is correct"
)
print()

print("3. SECURITY ACCESS")
print("-" * 60)
check_file_content(
    f"{base_path}/security/ir.model.access.csv",
    "model_vendor_category_document_line",
    "Access rights exist for intermediate model"
)
check_file_content(
    f"{base_path}/security/ir.model.access.csv",
    "model_vendor_category_document_line,base.group_user",
    "base.group_user access exists"
)
print()

print("4. VIEW XML")
print("-" * 60)
check_file_content(
    f"{base_path}/views/vendor_category_views.xml",
    'name="mandatory_document_line_ids"',
    "One2many field in view"
)
check_file_content(
    f"{base_path}/views/vendor_category_views.xml",
    'name="document_type_id"',
    "document_type_id field in tree"
)
check_file_content(
    f"{base_path}/views/vendor_category_views.xml",
    'name="requires_expiry"',
    "requires_expiry field in tree"
)
print()

print("5. MODULE MANIFEST")
print("-" * 60)
check_file_content(
    f"{base_path}/__manifest__.py",
    "security/ir.model.access.csv",
    "Security file in data list"
)
check_file_content(
    f"{base_path}/__manifest__.py",
    "views/vendor_category_views.xml",
    "View file in data list"
)
print()

print("=" * 60)
print("DIAGNOSTIC COMPLETE")
print("=" * 60)
print()
print("If all checks pass, restart Odoo and upgrade:")
print("1. Stop Odoo completely")
print("2. Run: ./odoo-bin -c odoo.conf -u vendor_management -d <your_db> --stop-after-init")
print("3. Start Odoo normally")
