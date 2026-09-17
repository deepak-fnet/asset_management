#!/bin/bash
# Complete cleanup and upgrade script for vendor_management module

set -e

MODULE_PATH="/home/prasanna/dev/odoo/custom_addons/vendor_management"
DB_NAME="${1:-odoo18}"  # Default database name

echo "============================================================"
echo "VENDOR MANAGEMENT - CLEAN UPGRADE SCRIPT"
echo "============================================================"
echo ""

# Step 1: Clear Python cache
echo "1. Clearing Python cache..."
find "$MODULE_PATH" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$MODULE_PATH" -type f -name "*.pyc" -delete 2>/dev/null || true
find "$MODULE_PATH" -type f -name "*.pyo" -delete 2>/dev/null || true
echo "✅ Python cache cleared"
echo ""

# Step 2: Verify model structure
echo "2. Verifying model structure..."
if [ -f "$MODULE_PATH/models/vendor_category_document_line.py" ]; then
    echo "✅ vendor_category_document_line.py exists"
else
    echo "❌ vendor_category_document_line.py NOT FOUND"
    exit 1
fi

if grep -q "from . import vendor_category_document_line" "$MODULE_PATH/models/__init__.py"; then
    echo "✅ Model imported in __init__.py"
else
    echo "❌ Model NOT imported in __init__.py"
    exit 1
fi

if grep -q "mandatory_document_line_ids" "$MODULE_PATH/models/vendor_category.py"; then
    echo "✅ One2many field exists in vendor_category.py"
else
    echo "❌ One2many field NOT FOUND"
    exit 1
fi
echo ""

# Step 3: Check security
echo "3. Checking security access..."
if grep -q "model_vendor_category_document_line,base.group_user" "$MODULE_PATH/security/ir.model.access.csv"; then
    echo "✅ base.group_user access exists"
else
    echo "❌ base.group_user access MISSING"
    exit 1
fi
echo ""

# Step 4: Instructions
echo "============================================================"
echo "PRE-FLIGHT CHECK PASSED"
echo "============================================================"
echo ""
echo "NEXT STEPS:"
echo ""
echo "1. STOP ODOO COMPLETELY:"
echo "   sudo systemctl stop odoo"
echo "   # OR if running manually:"
echo "   # pkill -9 odoo"
echo ""
echo "2. RUN UPGRADE (from Odoo directory):"
echo "   cd /path/to/odoo"
echo "   ./odoo-bin -c odoo.conf -u vendor_management -d $DB_NAME --stop-after-init"
echo ""
echo "3. CHECK LOGS FOR:"
echo "   - ✅ 'vendor.category.document.line' model registered"
echo "   - ✅ Views loaded successfully"
echo "   - ❌ NO 'Field ... does not exist' errors"
echo ""
echo "4. START ODOO:"
echo "   sudo systemctl start odoo"
echo ""
echo "============================================================"
