import os
import sys

# Setup Odoo environment
# Note: This is a placeholder for the actual Odoo shell script logic
# In a real Odoo environment, this would be run via odoo-bin shell

def test_sync():
    print("Starting Sync Verification...")
    
    # Simulate Odoo Shell logic
    partner = env['res.partner'].search([('is_vendor', '=', True)], limit=1)
    if not partner:
        print("FAIL: No vendor found for testing.")
        return

    doc_type_gst = env['vendor.document.type'].search([('code', '=', 'GST')], limit=1)
    doc_type_pan = env['vendor.document.type'].search([('code', '=', 'PAN')], limit=1)

    if not doc_type_gst or not doc_type_pan:
        print("FAIL: GST/PAN Document Types not found.")
        return

    print(f"Testing with Partner: {partner.name} (ID: {partner.id})")

    # 1. Create GST Document
    gst_doc = env['vendor.document'].create({
        'vendor_id': partner.id,
        'doc_type_id': doc_type_gst.id,
        'document_number': '22AAAAA0000A1Z5',
        'attachment': b'test',
        'filename': 'gst.pdf'
    })
    print(f"Created GST Doc: {gst_doc.id}")

    # 2. Approve GST Document using action_approve (which now uses write())
    gst_doc.action_approve()
    env.cr.commit() # Ensure trigger executes

    if partner.vat == '22AAAAA0000A1Z5':
        print("SUCCESS: GST Sync verified.")
    else:
        print(f"FAIL: GST Sync failed. Partner VAT is {partner.vat}")

    # 3. Create PAN Document
    pan_doc = env['vendor.document'].create({
        'vendor_id': partner.id,
        'doc_type_id': doc_type_pan.id,
        'document_number': 'ABCDE1234F',
        'attachment': b'test',
        'filename': 'pan.pdf'
    })
    print(f"Created PAN Doc: {pan_doc.id}")

    # 4. Approve PAN Document
    pan_doc.action_approve()
    env.cr.commit()

    if partner.l10n_in_pan == 'ABCDE1234F':
        print("SUCCESS: PAN Sync verified.")
    else:
        print(f"FAIL: PAN Sync failed. Partner PAN is {partner.l10n_in_pan}")

    # 5. Cleanup (optional - verification usually better with real data check)
    # gst_doc.unlink()
    # pan_doc.unlink()
    # print("SUCCESS: Cleanup verified.")

if __name__ == "__main__":
    # This script is intended to be run inside Odoo shell:
    # python3 odoo-bin shell -d <db> --content-prefix 'from verify_sync_v2 import test_sync; test_sync()'
    pass
