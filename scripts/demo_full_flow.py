"""One complete, fully filled sample cycle for the Asset Management demo.

Run inside an Odoo shell:
    python3.12 odoo-bin shell -c config/<conf> --no-http < scripts/demo_full_flow.py

Each step runs as the user whose job it is (vendor_user, dept_head, purchase_user,
purchase_stores, ...) when that login exists, otherwise as the superuser.
Vendor onboarding -> purchase request -> RFQ -> bids -> comparison -> approval ->
purchase orders -> receipt -> assets -> bills (approval chain) -> payments ->
depreciation -> warranty (+ claim) -> licenses. Safe to re-run: masters are
reused, and a cycle already done (same RFQ reference) is skipped.
"""
import base64
from datetime import date, timedelta

TODAY = date.today()
TAG = 'DEMO-FLOW-1'          # vendor reference on the RFQ: one full cycle per tag
PDF = base64.b64encode(b'%PDF-1.4\n% demo document\n').decode()
SU = env['res.users'].browse(1).sudo().env  # noqa: F821 (env comes from odoo shell)


def log(*a):
    print('*', *a, flush=True)


def as_(login):
    """Environment of that user when it exists, else superuser."""
    user = SU['res.users'].search([('login', '=', login)], limit=1)
    return SU(user=user.id, su=False) if user else SU


def get_or(model, domain, vals):
    rec = SU[model].with_context(active_test=False).search(domain, limit=1)
    return rec or SU[model].create(vals)


def ref(xmlid):
    return SU.ref(xmlid, raise_if_not_found=False)


# ---------------------------------------------------------------- menus (workspace layout)
if hasattr(SU['ir.module.module'], '_workspace_rewire_menus'):
    SU['ir.module.module']._workspace_rewire_menus()
    env.cr.commit()  # noqa: F821
    log('menus rewired')

# ---------------------------------------------------------------- masters
company = SU.company
tn = SU['res.country.state'].search([('code', '=', 'TN'), ('country_id.code', '=', 'IN')], limit=1)
ka = SU['res.country.state'].search([('code', '=', 'KA'), ('country_id.code', '=', 'IN')], limit=1)
india = ref('base.in')
if not company.state_id:
    company.write({'street': '12, Anna Salai', 'city': 'Chennai', 'zip': '600002',
                   'state_id': tn.id, 'country_id': india.id})

term30 = get_or('account.payment.term', [('name', '=', '30 Days')], {'name': '30 Days'})
term15 = get_or('account.payment.term', [('name', '=', '15 Days')], {'name': '15 Days'})
gst_p = SU['account.tax'].search([('name', '=', '18% GST P')], limit=1)

laptop_cat = SU['product.category'].search([('name', '=', 'Laptop')], limit=1) or SU['product.category'].create({'name': 'Laptop'})
laptop_cat.write({'is_it_asset': True, 'is_general': True, 'code': laptop_cat.code or 'LAP',
                  'depreciation_method': 'slm', 'useful_life_years': 4, 'salvage_value_percent': 5.0})
acc_cat = get_or('product.category', [('name', '=', 'IT Accessories')], {'name': 'IT Accessories'})

w36 = get_or('asset.warranty.template', [('name', '=', 'Onsite 36 Months')],
             {'name': 'Onsite 36 Months', 'period_months': 36, 'note': 'Next business day onsite support.'})

laptop_vals = {
    'name': 'Lenovo ThinkPad E14 Gen 5 (i5-1335U / 16GB / 512GB SSD)', 'default_code': 'LEN-E14G5',
    'type': 'consu', 'is_storable': True, 'purchase_ok': True, 'sale_ok': False, 'standard_price': 58000,
    'categ_id': laptop_cat.id, 'supplier_taxes_id': [(6, 0, gst_p.ids)],
    'description_purchase': '14" FHD, Intel i5-1335U, 16GB DDR4, 512GB NVMe, Win 11 Pro, 3 yr onsite warranty',
    'is_fixed_asset': True, 'asset_category_id': laptop_cat.id, 'is_asset': True,
}
laptop_t = SU['product.template'].search([('default_code', '=', 'LEN-E14G5')], limit=1)
laptop_t = laptop_t.write(laptop_vals) and laptop_t if laptop_t else SU['product.template'].create(laptop_vals)
laptop = laptop_t.product_variant_id

combo_vals = {
    'name': 'Logitech MK270 Wireless Keyboard & Mouse Combo', 'default_code': 'LOG-MK270',
    'type': 'consu', 'is_storable': True, 'purchase_ok': True, 'sale_ok': False, 'standard_price': 1500,
    'categ_id': acc_cat.id, 'supplier_taxes_id': [(6, 0, gst_p.ids)],
    'description_purchase': '2.4 GHz wireless keyboard and mouse, USB receiver',
}
combo_t = SU['product.template'].search([('default_code', '=', 'LOG-MK270')], limit=1)
combo_t = combo_t.write(combo_vals) and combo_t if combo_t else SU['product.template'].create(combo_vals)
combo = combo_t.product_variant_id
log('products', laptop.display_name, '|', combo.display_name)

# document types + vendor category (GST, PAN mandatory, cancelled cheque without a number)
DT = SU['vendor.document.type']
gst_t = DT.search([('code', '=', 'GST')], limit=1) or DT.create({'name': 'GST Certificate', 'code': 'GST'})
pan_t = DT.search([('code', '=', 'PAN')], limit=1) or DT.create({'name': 'PAN Card', 'code': 'PAN'})
chq_t = DT.search([('name', '=', 'Cancelled Cheque')], limit=1) or DT.create({'name': 'Cancelled Cheque', 'code': 'CHQ'})
chq_t.requires_document_number = False
vcat = get_or('vendor.category', [('name', '=', 'IT Hardware Suppliers')], {
    'name': 'IT Hardware Suppliers', 'icon': 'fa-laptop',
    'mandatory_document_line_ids': [(0, 0, {'document_type_id': t.id, 'is_mandatory': True, 'requires_expiry': False})
                                    for t in (gst_t, pan_t, chq_t)]})

# terms & conditions: term types with their values, one template
TERMS = [
    ('Delivery Period', 'Within 7 days from the date of PO'),
    ('Payment Terms', '30 days from receipt of goods and invoice'),
    ('Warranty', '3 years onsite warranty from the date of delivery'),
    ('Freight & Insurance', 'Free delivery to our office, insured by the vendor'),
    ('Taxes', 'GST extra as applicable; GSTIN must be on the invoice'),
    ('Late Delivery Penalty', '0.5% of order value per week, maximum 5%'),
    ('Price Validity', 'Prices firm till the order is fully delivered'),
    ('Installation', 'Unboxing, OS setup and asset tagging at site by the vendor'),
]
TC = SU['purchase.terms.condition']
TV = SU['purchase.terms.condition.value']
term_value = {}
for tname, tval in TERMS:
    term = TC.search([('name', '=', tname)], limit=1) or TC.create({'name': tname})
    value = TV.search([('term_id', '=', term.id), ('value', '=', tval)], limit=1) or \
        TV.create({'term_id': term.id, 'value': tval})
    term_value[term] = value
tc_template = SU['purchase.terms.condition.template'].search([('name', '=', 'IT Hardware Purchase Terms')], limit=1) \
    or SU['purchase.terms.condition.template'].create({'name': 'IT Hardware Purchase Terms'})
tc_template.term_ids = [(6, 0, [t.id for t in term_value])]


def apply_terms(order):
    """Terms template + one line per term with its value (if the order has none)."""
    if order.terms_condition_line_ids:
        return False
    order.sudo().write({'terms_template_id': tc_template.id, 'terms_condition_line_ids': [
        (0, 0, {'term_id': t.id, 'value_id': v.id}) for t, v in term_value.items()]})
    return True


log('terms template', tc_template.name, len(term_value), 'terms')

# ---------------------------------------------------------------- vendors
VENDORS = [
    dict(name='Nova Tech Systems Pvt Ltd', email='sales@novatech.example', phone='+91 44 4567 8901',
         website='https://novatech.example', street='45, Mount Road', street2='3rd Floor, Spencer Plaza',
         city='Chennai', zip='600002', state=tn, vat='33AABCN1234F1Z5', pan='AABCN1234F',
         cin='U72200TN2012PTC086421', term=term30, bank='HDFC Bank', ifsc='HDFC0000123', acc='50200012345678',
         contact=('Ramesh Kumar', 'Sales Manager', 'ramesh@novatech.example', '+91 98400 11223')),
    dict(name='Prime IT Distributors LLP', email='orders@primeit.example', phone='+91 80 4123 7788',
         website='https://primeit.example', street='88, Residency Road', street2='Ashok Nagar',
         city='Bengaluru', zip='560025', state=ka, vat='29AAJFP5678K1Z3', pan='AAJFP5678K',
         cin='U51909KA2015PTC079854', term=term15, bank='ICICI Bank', ifsc='ICIC0000456', acc='004505012345',
         contact=('Divya Rao', 'Key Accounts', 'divya@primeit.example', '+91 99000 44556')),
]
vendors = SU['res.partner']
for v in VENDORS:
    P = as_('vendor_user')['res.partner']
    partner = SU['res.partner'].search([('name', '=', v['name'])], limit=1)
    if not partner:
        partner = P.create({
            'name': v['name'], 'is_company': True, 'company_type': 'company', 'is_vendor': True,
            'email': v['email'], 'phone': v['phone'], 'website': v['website'],
            'street': v['street'], 'street2': v['street2'], 'city': v['city'], 'zip': v['zip'],
            'state_id': v['state'].id, 'country_id': india.id, 'vat': v['vat'], 'company_registry': v['cin'],
            'vendor_category_ids': [(6, 0, vcat.ids)],
            'property_supplier_payment_term_id': v['term'].id,
            'child_ids': [(0, 0, {'name': v['contact'][0], 'function': v['contact'][1], 'email': v['contact'][2],
                                  'phone': v['contact'][3], 'type': 'contact'})],
        })
        bank = get_or('res.bank', [('name', '=', v['bank'])], {'name': v['bank'], 'bic': v['ifsc'][:4]})
        SU['res.partner.bank'].create({'partner_id': partner.id, 'acc_number': v['acc'], 'bank_id': bank.id,
                                       'allow_out_payment': True})
    vendors |= partner
    if partner.vendor_state == 'active':
        continue
    numbers = {gst_t.id: v['vat'], pan_t.id: v['pan'], chq_t.id: False}
    for doc in SU['vendor.document'].search([('vendor_id', '=', partner.id)]):
        if doc.state == 'draft':
            as_('vendor_user')['vendor.document'].browse(doc.id).write({
                'document_number': numbers.get(doc.doc_type_id.id) or False,
                'attachment': PDF, 'filename': '%s_%s.pdf' % (doc.doc_type_id.name.replace(' ', '_'), v['pan']),
                'expiry_date': TODAY + timedelta(days=730) if doc.requires_expiry_for_vendor else False,
            })
        if doc.state == 'draft':
            as_('vendor_user')['vendor.document'].browse(doc.id).action_submit()
    if partner.vendor_state == 'draft':
        as_('vendor_user')['res.partner'].browse(partner.id).action_submit_for_review()
    for doc in SU['vendor.document'].search([('vendor_id', '=', partner.id)]):
        D = lambda login: as_(login)['vendor.document'].browse(doc.id)  # noqa: E731
        if doc.state == 'submitted':
            D('vendor_manager').action_manager_review()
        if doc.state == 'manager_review':
            D('vendor_manager').action_md_approval()
        if doc.state == 'md_approval':
            D('vendor_md').action_approve()
    if partner.vendor_state == 'submitted':
        as_('vendor_manager')['res.partner'].browse(partner.id).action_mark_reviewed()
    if partner.vendor_state == 'manager_review':
        as_('vendor_md')['res.partner'].browse(partner.id).action_approve_activate()
    portal = SU['res.users'].with_context(active_test=False).search([('partner_id', '=', partner.id)], limit=1)
    if portal:
        portal.write({'password': '1', 'active': True})
    log('vendor', partner.name, partner.vendor_code, partner.vendor_state, partner.compliance_status,
        'portal:', portal.login or '-')

nova, prime = vendors[0], vendors[1]

# ---------------------------------------------------------------- purchase request
PO = SU['purchase.order']
rfq = PO.search([('partner_ref', '=', TAG)], limit=1)
if not rfq:
    req_env = as_('employee')
    request = req_env['asset.request'].create({
        'notes': 'Three developers join the Engineering team on the 15th - laptops and keyboard/mouse sets needed.',
        'line_ids': [(0, 0, {'description': 'Laptop for developer (i5, 16GB RAM, 512GB SSD)', 'quantity': 3}),
                     (0, 0, {'description': 'Wireless keyboard and mouse set', 'quantity': 3})]})
    request.action_submit()
    approver = request._requester_manager_user().login if request._requester_manager_user() else 'asset_manager'
    as_(approver)['asset.request'].browse(request.id).action_approve()
    store = as_('asset_user')['asset.request'].browse(request.id)
    for line, product in zip(store.line_ids.sorted('id'), (laptop, combo)):
        line.product_id = product
    store.action_check_availability()
    log('request', request.name, request.state, [(l.product_id.default_code, l.asset_category_id.name, l.available_qty)
                                                  for l in request.line_ids])

    # ------------------------------------------------------------ RFQ
    pu = as_('purchase_user')
    act = pu['asset.request'].browse(request.id).action_create_rfq()
    vals = {k[8:]: val for k, val in (act.get('context') or {}).items()
            if k.startswith('default_') and k != 'default_order_line'}
    vals.update({
        'partner_ref': TAG, 'vendor_ids': [(6, 0, vendors.ids)], 'expiry_date': TODAY + timedelta(days=7),
        'order_line': [(0, 0, {'product_id': l.product_id.id, 'product_qty': l.quantity, 'name': l.description,
                               'date_planned': TODAY + timedelta(days=14)}) for l in request.line_ids],
    })
    rfq = pu['purchase.order'].create(vals)
    apply_terms(rfq)
    rfq.action_send_to_vendors()
    log('rfq', rfq.name, rfq.comparison_state, 'vendors', rfq.vendor_ids.mapped('name'))

    # ------------------------------------------------------------ bids (entered for the vendors)
    BIDS = {
        nova.id: ({laptop.id: (58500, 7, '3 yr onsite warranty, carry bag included'),
                   combo.id: (1450, 3, 'Stock available in Chennai')},
                  term30, 'Prices exclusive of GST. Free delivery to Chennai office. Quote valid 15 days.', 'NTS/Q/2026/118'),
        prime.id: ({laptop.id: (56900, 12, 'Ships from Bengaluru warehouse'),
                    combo.id: (1690, 5, False)},
                   term15, 'Prices exclusive of GST. Freight extra at actuals. Quote valid 10 days.', 'PID/QT/4471'),
    }
    for quote in rfq.quote_ids:
        prices, term, note, qref = BIDS[quote.vendor_id.id]
        q = pu['vendor.quote'].browse(quote.id)
        for line in q.line_ids:
            price, days, vnote = prices[line.product_id.id]
            line.write({'vendor_price': price, 'delivery_date': TODAY + timedelta(days=days), 'vendor_notes': vnote})
        att = SU['ir.attachment'].create({'name': '%s.pdf' % qref.replace('/', '_'), 'datas': PDF,
                                          'res_model': 'vendor.quote', 'res_id': quote.id})
        q.write({'payment_term_id': term.id, 'note': note, 'quotation_attachment_ids': [(4, att.id)]})
        q.action_submit_bid()
    log('bids', rfq.comparison_state, [(q.vendor_id.name, q.total_amount, q.payment_term_id.name, str(q.delivery_date))
                                       for q in rfq.quote_ids])

    # ------------------------------------------------------------ comparison: lowest price per product
    picks = SU['vendor.quote.line']
    for po_line in rfq.order_line:
        cands = rfq.comparison_line_ids.filtered(lambda q, l=po_line: q.po_line_id == l and q.vendor_price > 0)
        picks |= cands.sorted('vendor_price')[:1]
    pu['purchase.order'].browse(rfq.id).action_award_quote_lines(picks.ids)
    pu['purchase.order'].browse(rfq.id).action_confirm_comparison()
    pu['purchase.order'].browse(rfq.id).action_submit_for_approval()
    for _i in range(6):
        pending = rfq.approval_line_ids.filtered(lambda l: l.status == 'pending')
        if not pending:
            break
        approver = pending.sorted('sequence')[0].approver_id
        SU(user=approver.id, su=False)['purchase.order'].browse(rfq.id).action_approve_order()
    log('approval', rfq.approval_status, rfq.approval_line_ids.mapped(lambda l: (l.approver_id.login, l.status)))

    # order terms (payment terms from the bids) -> confirm
    for q in rfq.awarded_quote_ids.filtered(lambda q: not q.payment_term_id):
        q.payment_term_id = term30
    pu['purchase.order'].browse(rfq.id).button_confirm()

children = PO.search([('rfq_id', '=', rfq.id)])
for order in rfq | children:
    if apply_terms(order):
        log('terms added to', order.name)
QREF = {nova.id: 'NTS/Q/2026/118', prime.id: 'PID/QT/4471'}
for po in children:
    pm = po.partner_id.property_outbound_payment_method_line_id if 'property_outbound_payment_method_line_id' in po.partner_id._fields else False
    po.write({'partner_ref': QREF.get(po.partner_id.id, TAG)} | ({'vendor_payment_method_line_id': pm.id} if pm else {}))
log('purchase orders', [(p.name, p.partner_id.name, p.amount_total, p.payment_term_id.name) for p in children])

# ---------------------------------------------------------------- receive
serial_no = iter(['PF4XK2%03d' % i for i in range(101, 200)])
for po in children:
    for picking in po.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel')):
        pk = as_('purchase_stores')['stock.picking'].browse(picking.id)
        for move in pk.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        pk.with_context(skip_backorder=True).button_validate()
log('receipts', [(p.name, p.state) for p in children.picking_ids])

# ---------------------------------------------------------------- assets: details, warranty, depreciation
assets = SU['asset.asset'].search([('purchase_order_id', 'in', children.ids)])
loc = SU['asset.location'].search([('name', '=', 'Dev Bay')], limit=1) or SU['asset.location'].search([], limit=1)
for asset in assets.filtered(lambda a: a.product_id == laptop):
    po = asset.purchase_order_id
    asset.write({
        'serial_number': next(serial_no),
        'purchase_date': po.date_approve.date() if po.date_approve else TODAY,
        'purchase_cost': po.order_line.filtered(lambda l: l.product_id == laptop)[:1].price_unit or asset.purchase_cost,
        'warranty_template_id': w36.id,
        'warranty_provider_id': po.partner_id.id,
        'warranty_type': 'manufacturer',
        'warranty_contract_no': 'LNV-OS-%s' % asset.serial_number,
        'warranty_support_contact': '1800-419-4666 (Lenovo support)',
        'warranty_coverage_notes': 'Parts and labour, next business day onsite. Battery covered for 1 year.',
        'location_id': loc.id if loc else False,
    })
for asset in assets:
    if asset.state == 'draft':
        as_('asset_user')['asset.asset'].browse(asset.id).action_submit()
laptops = assets.filtered(lambda a: a.product_id == laptop)
laptops.action_calculate_depreciation()
log('assets', [(a.asset_code, a.serial_number, a.state, a.purchase_cost, a.warranty_end_date and str(a.warranty_end_date),
                len(a.depreciation_line_ids)) for a in laptops])

# assign the three laptops to developers
emps = SU['hr.employee'].search([('user_id.login', 'in', ['vignesh', 'meena', 'lakshmi'])], limit=3) \
    or SU['hr.employee'].search([], limit=3)
for asset, emp in zip(laptops.filtered(lambda a: a.state == 'submit'), emps):
    wiz = as_('it_user')['asset.direct.assign.wizard'].create({
        'asset_id': asset.id, 'assign_type': 'employee', 'employee_id': emp.id,
        'department_id': emp.sudo().department_id.id, 'location_id': emp.sudo().department_id.location_id.id or (loc.id if loc else False)})
    wiz.action_assign()
log('assigned', [(a.asset_code, a.assigned_employee_id.name) for a in laptops])

# ---------------------------------------------------------------- bills -> approval chain -> post
INV = {nova.id: 'NTS/INV/2026/0931', prime.id: 'PID/INV/26-27/0212'}
bills = SU['account.move']
for po in children:
    existing = SU['account.move'].search([('invoice_origin', 'ilike', po.name), ('move_type', '=', 'in_invoice')], limit=1)
    if existing:
        bills |= existing
        continue
    bill_id = as_('purchase_user')['purchase.order'].browse(po.id).action_upload_bill()['res_id']
    bill = as_('purchase_user')['account.move'].browse(bill_id)
    SU['ir.attachment'].create({'name': '%s.pdf' % INV[po.partner_id.id].replace('/', '_'), 'datas': PDF,
                                'res_model': 'account.move', 'res_id': bill.id})
    bill.write({'ref': INV[po.partner_id.id], 'invoice_date': TODAY, 'payment_reference': INV[po.partner_id.id]})
    bill.action_bill_submit()
    head = bill.bill_dept_approver_id.login or 'dept_head'
    as_(head)['account.move'].browse(bill.id).action_dept_approve()
    as_('purchase_stores')['account.move'].browse(bill.id).action_stores_approve()
    as_('purchase_audit')['account.move'].browse(bill.id).action_audit_verify()
    as_('purchase_finance')['account.move'].browse(bill.id).action_post()
    bills |= bill
log('bills', [(b.name, b.ref, b.partner_id.name, b.amount_total, b.state, b.payment_state) for b in bills])

# ---------------------------------------------------------------- payments
bank = SU['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', company.id)], limit=1)
for bill in bills.filtered(lambda b: b.state == 'posted' and b.payment_state == 'not_paid'):
    fin = as_('purchase_finance')
    wiz = fin['account.payment.register'].with_context(active_model='account.move', active_ids=bill.ids).create(
        {'journal_id': bank.id, 'payment_date': TODAY})
    wiz.action_create_payments()
log('payments', [(b.name, b.payment_state) for b in bills],
    [(p.name, p.amount, p.state) for p in SU['account.payment'].search([('partner_id', 'in', vendors.ids)])])

# ---------------------------------------------------------------- warranty claim on one laptop
if laptops and not SU['asset.warranty.claim'].search([('asset_id', 'in', laptops.ids)], limit=1):
    a = laptops[0]
    claim = as_('asset_manager')['asset.warranty.claim'].create({
        'asset_id': a.id, 'employee_id': a.assigned_employee_id.id, 'issue_type': 'battery',
        'description': 'Battery drains from 100% to 0 in about 40 minutes; swelling noticed near the touchpad.',
        'provider_id': a.warranty_provider_id.id, 'contract_no': a.warranty_contract_no,
        'provider_ticket_no': 'LNV-CASE-7781234', 'cost': 0.0,
        'attachment_file': PDF, 'attachment_filename': 'battery_report.pdf'})
    claim.action_submit()
    claim.action_start_progress()
    claim.action_approve()
    claim.write({'resolution': 'replaced_part', 'replaced_part': 'Battery 57Wh (FRU 5B10W51867)',
                 'resolution_notes': 'Technician replaced the battery onsite; health 100%.'})
    claim.action_complete()
    log('warranty claim', claim.name, claim.state, claim.turnaround_days)

# ---------------------------------------------------------------- licenses
L = SU['asset.license']
LICS = [
    dict(name='Microsoft 365 Business Standard', reference='MS365-BS-2026', software_name='Microsoft 365 Business Standard',
         version='2026', publisher='Microsoft', vendor_id=nova.id, license_type='subscription',
         license_key='M365-NOVA-7K2P-9QXW-4HRT', total_seats=10, purchase_date=TODAY, start_date=TODAY,
         expiry_date=TODAY + timedelta(days=365), cost=118800, renewal_cost=124800, auto_renew=True,
         po_reference=children[:1].name, notes='Email, Teams, Office apps. 10 seats, annual commitment.',
         attachment_file=PDF, attachment_filename='M365_invoice.pdf'),
    dict(name='Windows 11 Pro (OEM)', reference='WIN11-OEM-E14', software_name='Windows 11 Pro',
         version='23H2', publisher='Microsoft', vendor_id=nova.id, license_type='oem',
         license_key='OEM-DM-ON-BIOS', total_seats=3, purchase_date=TODAY, start_date=TODAY,
         cost=0, notes='Pre-activated OEM licence, tied to each laptop.',
         po_reference=children[:1].name),
]
for vals in LICS:
    lic = L.search([('reference', '=', vals['reference'])], limit=1)
    if not lic:
        lic = L.create({k: v for k, v in vals.items() if k in L._fields})
        for asset in laptops:
            SU['asset.license.assignment'].create({'license_id': lic.id, 'asset_id': asset.id,
                                                    'employee_id': asset.assigned_employee_id.id})
    log('license', lic.name, lic.state, '%s/%s seats' % (lic.assigned_seats, lic.total_seats))

env.cr.commit()  # noqa: F821
log('DONE')
