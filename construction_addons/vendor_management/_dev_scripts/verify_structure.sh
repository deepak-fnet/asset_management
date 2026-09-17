#!/bin/bash
# Direct verification that all components match exactly

echo "================================================================"
echo "EXACT MODEL REGISTRATION VERIFICATION"
echo "================================================================"
echo ""

# 1. File exists
echo "1. Model file exists?"
if [ -f "models/vendor_category_document_line.py" ]; then
    echo "   ✅ YES: models/vendor_category_document_line.py"
    ls -lh models/vendor_category_document_line.py
else
    echo "   ❌ NO: File not found!"
    exit 1
fi
echo ""

# 2. Check _name
echo "2. Checking _name in vendor_category_document_line.py:"
grep "_name = " models/vendor_category_document_line.py | head -1
echo ""

# 3. Check import
echo "3. Checking models/__init__.py import order:"
head -3 models/__init__.py
echo ""

# 4. Check One2many field
echo "4. Checking One2many field in vendor_category.py:"
grep -A 3 "mandatory_document_line_ids = fields.One2many" models/vendor_category.py
echo ""

# 5. Check security
echo "5. Checking security access:"
grep "model_vendor_category_document_line,base.group_user" security/ir.model.access.csv
echo ""

echo "================================================================"
echo "If all above show ✅, then structure is correct."
echo "The issue may be:"
echo "1. Odoo is not restarted properly"
echo "2. Using wrong database"
echo "3. Module not upgraded with -u flag"
echo "================================================================"
