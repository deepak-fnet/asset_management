"""Ten complete business cycles of sample data, every flow and field exercised.

Run inside an Odoo shell (needs the role users: employee, dept_head, it_user,
engineer, purchase_user, purchase_stores, vendor_manager, vendor_md, ...):

    python3.12 odoo-bin shell -c config/<conf> -d <db> --no-http < scripts/demo_10_cycles.py

Each cycle, every step as the user whose job it is:
  1. vendor onboarding   - a new vendor registers, uploads GST/PAN/cheque,
                           vendor manager reviews, MD approves (vendor code)
  2. asset request       - employee asks, department head approves, store sets
                           the products and checks availability
  3. RFQ                 - purchase user sends it to the new vendor + an
                           existing one, both bid (price, delivery, payment
                           terms, notes, quotation copy)
  4. comparison          - lowest price awarded, comparison confirmed,
                           approval chain, Confirm Order -> one PO per vendor
  5. purchase orders     - Bill Policy, Terms & Conditions, confirm
  6. receipt             - stores receive; fixed assets created
  7. fixed assets        - serial, warranty, location, depreciation, submit,
                           assigned to the requester
  8. IT asset mapping    - laptops/desktops: the asset agent reports in with
                           the same serial (hardware, OS, installed apps), so
                           the fixed asset shows up under IT Asset
  9. bills + payment     - upload bill, Dept Head -> Stores -> Audit ->
                           Finance, post, pay
 10. helpdesk            - the requester raises a ticket (Raise Ticket ->
                           Submit, or an incoming email), the IT incharge
                           creates the ticket, assigns team/engineer, Create
                           Task -> repair: internal fix, internal with spares
                           from the store, external vendor (dispatch ->
                           receive back -> service PO -> confirm -> Done), or
                           not repairable -> replacement. Ticket closes.

A step that fails is printed as  BUG|cycle|step|error  and the cycle goes on,
so one run doubles as a full regression check. Re-running skips finished
cycles (RFQ reference DEMO-10-<n>).
"""
import base64
import json
import random
import traceback
from datetime import date, timedelta


random.seed(10)
TODAY = date.today()
PDF = base64.b64encode(b'%PDF-1.4\n% demo document\n').decode()
SU = env['res.users'].browse(1).sudo().env  # noqa: F821
BUGS = []


def log(*a):
    print('*', *a, flush=True)


def as_(login):
    user = SU['res.users'].search([('login', '=', login)], limit=1)
    return SU(user=user.id, su=False) if user else SU


def ref(xmlid):
    return SU.ref(xmlid, raise_if_not_found=False)


def get_or(model, domain, vals):
    rec = SU[model].with_context(active_test=False).search(domain, limit=1)
    return rec or SU[model].create(vals)


class Step:
    """with Step(n, 'name'): ...  - savepoint; a failure is logged as a bug."""

    def __init__(self, cycle, name):
        self.cycle, self.name = cycle, name

    def __enter__(self):
        self.sp = env.cr.savepoint()  # noqa: F821
        self.sp.__enter__()
        return self

    def __exit__(self, et, ev, tb):
        if et is None:
            self.sp.__exit__(None, None, None)
            return False
        self.sp.__exit__(et, ev, tb)
        msg = '%s: %s' % (et.__name__, str(ev).strip().replace('\n', ' ')[:300])
        BUGS.append((self.cycle, self.name, msg))
        print('BUG|%s|%s|%s' % (self.cycle, self.name, msg), flush=True)
        if et.__name__ not in ('UserError', 'ValidationError', 'AccessError'):
            traceback.print_exception(et, ev, tb, limit=-4)
        return True   # swallow, carry on


# ====================================================================== masters
company = SU.company
india = ref('base.in')
STATES = {c: SU['res.country.state'].search([('code', '=', c), ('country_id.code', '=', 'IN')], limit=1)
          for c in ('TN', 'KA', 'MH', 'DL', 'TS', 'KL')}
gst_p = SU['account.tax'].search([('name', '=', '18% GST P')], limit=1) or \
    SU['account.tax'].search([('type_tax_use', '=', 'purchase'), ('amount', '=', 18)], limit=1)
TERMS_PAY = {n: get_or('account.payment.term', [('name', '=', n)], {'name': n})
             for n in ('Immediate Payment', '15 Days', '30 Days', '45 Days')}

# product categories (fixed-asset settings)
CATS = {}
for name, parent, it, general, code, life in [
        ('Laptop', 'Office Assets', True, True, 'LAP', 4), ('Desktop', 'Office Assets', True, True, 'DSK', 5),
        ('Monitor', 'Goods', True, False, 'MON', 5), ('Printer', 'Office Assets', True, True, 'PRN', 5),
        ('UPS', 'Office Assets', False, True, 'UPS', 5), ('Air Conditioner', 'Office Assets', False, True, 'AC', 8),
        ('Chair', 'Office Assets', False, True, 'CHR', 7), ('Switch', 'Goods', True, False, 'NSW', 6),
        ('Spare Parts', None, False, False, 'SPR', 1)]:
    par = SU['product.category'].search([('name', '=', parent), ('parent_id', '=', False)], limit=1) if parent else False
    if parent and not par:
        par = SU['product.category'].create({'name': parent})
    cat = SU['product.category'].search([('name', '=', name), ('parent_id', '=', par.id if par else False)], limit=1) \
        or SU['product.category'].create({'name': name, 'parent_id': par.id if par else False})
    if name != 'Spare Parts':
        cat.write({'is_it_asset': it, 'is_general': general, 'code': cat.code or code,
                   'depreciation_method': 'slm', 'useful_life_years': life, 'salvage_value_percent': 5.0})
    CATS[name] = cat


def product(code, name, cat, price, desc, asset=True, storable=True):
    vals = {'name': name, 'default_code': code, 'type': 'consu', 'is_storable': storable, 'purchase_ok': True,
            'sale_ok': False, 'standard_price': price, 'categ_id': CATS[cat].id,
            'supplier_taxes_id': [(6, 0, gst_p.ids)], 'description_purchase': desc}
    if asset:
        vals.update({'is_fixed_asset': True, 'asset_category_id': CATS[cat].id, 'is_asset': True})
    vals = {k: v for k, v in vals.items() if k in SU['product.template']._fields}
    t = SU['product.template'].search([('default_code', '=', code)], limit=1)
    if t:
        t.write(vals)
    else:
        t = SU['product.template'].create(vals)
    return t.product_variant_id


W = {m: get_or('asset.warranty.template', [('name', '=', n)], {'name': n, 'period_months': m, 'note': note})
     for m, n, note in [(12, 'Standard 12 Months', 'Carry-in, parts and labour.'),
                        (24, 'Comprehensive 24 Months', 'Onsite, parts and labour.'),
                        (36, 'Onsite 36 Months', 'Next business day onsite support.')]}

# vendor onboarding masters
DT = SU['vendor.document.type']
gst_t = DT.search([('code', '=', 'GST')], limit=1) or DT.create({'name': 'GST Certificate', 'code': 'GST'})
pan_t = DT.search([('code', '=', 'PAN')], limit=1) or DT.create({'name': 'PAN Card', 'code': 'PAN'})
chq_t = DT.search([('name', '=', 'Cancelled Cheque')], limit=1) or DT.create({'name': 'Cancelled Cheque', 'code': 'CHQ'})
chq_t.requires_document_number = False
VCAT = {}
for name, icon in [('IT Hardware Suppliers', 'fa-laptop'), ('Electrical & HVAC', 'fa-bolt'),
                   ('Furniture', 'fa-bed'), ('Repair & Service', 'fa-wrench')]:
    VCAT[name] = get_or('vendor.category', [('name', '=', name)], {
        'name': name, 'icon': icon,
        'mandatory_document_line_ids': [(0, 0, {'document_type_id': t.id, 'is_mandatory': True,
                                                'requires_expiry': False}) for t in (gst_t, pan_t, chq_t)]})

# terms & conditions
TERMS = [('Delivery Period', 'Within 7 days from the date of PO'),
         ('Payment Terms', '30 days from receipt of goods and invoice'),
         ('Warranty', 'Manufacturer warranty from the date of delivery'),
         ('Freight & Insurance', 'Free delivery to our office, insured by the vendor'),
         ('Taxes', 'GST extra as applicable; GSTIN must be on the invoice'),
         ('Late Delivery Penalty', '0.5% of order value per week, maximum 5%'),
         ('Price Validity', 'Prices firm till the order is fully delivered'),
         ('Installation', 'Installation and demo at site by the vendor')]
TC, TV = SU['purchase.terms.condition'], SU['purchase.terms.condition.value']
term_value = {}
for tname, tval in TERMS:
    term = TC.search([('name', '=', tname)], limit=1) or TC.create({'name': tname})
    term_value[term] = TV.search([('term_id', '=', term.id), ('value', '=', tval)], limit=1) or \
        TV.create({'term_id': term.id, 'value': tval})
tc_template = SU['purchase.terms.condition.template'].search([('name', '=', 'Standard Purchase Terms')], limit=1) \
    or SU['purchase.terms.condition.template'].create({'name': 'Standard Purchase Terms'})
tc_template.term_ids = [(6, 0, [t.id for t in term_value])]


def apply_terms(order):
    if not order.terms_condition_line_ids:
        order.sudo().write({'terms_template_id': tc_template.id, 'terms_condition_line_ids': [
            (0, 0, {'term_id': t.id, 'value_id': v.id}) for t, v in term_value.items()]})


# helpdesk masters
it_team = SU['asset.helpdesk.team'].search([('name', '=', 'IT Support Team')], limit=1) or \
    SU['asset.helpdesk.team'].create({'name': 'IT Support Team'})
fac_team = SU['asset.helpdesk.team'].search([('name', '=', 'Facilities Team')], limit=1) or \
    SU['asset.helpdesk.team'].create({'name': 'Facilities Team', 'description': 'Electrical, HVAC, furniture'})
eng = SU['res.users'].search([('login', '=', 'engineer')], limit=1)
itu = SU['res.users'].search([('login', '=', 'it_user')], limit=1)
amg = SU['res.users'].search([('login', '=', 'asset_manager')], limit=1)
it_team.write({'leader_id': itu.id, 'member_ids': [(4, u.id) for u in (itu | eng)]})
fac_team.write({'leader_id': amg.id, 'member_ids': [(4, u.id) for u in (amg | eng)]})
HCAT = {}
for name, repair, general, team in [('Hardware', True, False, it_team), ('Software', True, False, it_team),
                                    ('General Maintenance', False, True, fac_team)]:
    c = SU['asset.helpdesk.category'].search([('name', '=', name)], limit=1) or \
        SU['asset.helpdesk.category'].create({'name': name})
    c.write({'is_repair_flow': repair, 'is_general': general, 'default_team_id': team.id})
    HCAT[name] = c
HTYPE = {n: get_or('asset.helpdesk.type', [('name', '=', n)], {'name': n, 'is_repair_flow': True})
         for n in ('Computer Maintenance', 'Electrical Maintenance', 'Printer Maintenance',
                   'HVAC Maintenance', 'Furniture Repair', 'Network Maintenance')}
RTYPE = {}
if 'repair.issue.type' in SU:
    for name, beh in [('Hardware Fault', 'hardware'), ('Software Fault', 'software'), ('General Fault', 'general')]:
        RTYPE[beh] = get_or('repair.issue.type', [('name', '=', name)], {'name': name, 'behaviour': beh})

LOC = {l.name: l for l in SU['asset.location'].search([])}
log('masters ready')

# ====================================================================== cycle definitions
NEW_VENDORS = [
    dict(name='Sakthi Computer Systems', city='Coimbatore', state='TN', street='112, Avinashi Road', zip='641018',
         vat='33AAGCS4521M1ZQ', pan='AAGCS4521M', cat='IT Hardware Suppliers', bank='Indian Bank',
         ifsc='IDIB000C012', acc='6543210987', contact='Karthikeyan S', phone='+91 422 425 6677'),
    dict(name='Dhanush Infotech Pvt Ltd', city='Bengaluru', state='KA', street='14, Old Airport Road', zip='560017',
         vat='29AACCD7781F1Z2', pan='AACCD7781F', cat='IT Hardware Suppliers', bank='Axis Bank',
         ifsc='UTIB0000009', acc='917020045612388', contact='Manjunath H', phone='+91 80 4911 2233'),
    dict(name='Shree Ganesh Enterprises', city='Mumbai', state='MH', street='7, Lamington Road', zip='400007',
         vat='27AAFFS3322R1ZK', pan='AAFFS3322R', cat='IT Hardware Suppliers', bank='Bank of Baroda',
         ifsc='BARB0LAMING', acc='04580200001234', contact='Rajesh Shah', phone='+91 22 2385 4411'),
    dict(name='PowerSafe UPS Solutions', city='Chennai', state='TN', street='22, Arcot Road, Vadapalani', zip='600026',
         vat='33AAJCP6612K1Z9', pan='AAJCP6612K', cat='Electrical & HVAC', bank='Canara Bank',
         ifsc='CNRB0001234', acc='1234101009876', contact='Senthil Murugan', phone='+91 44 2362 8899'),
    dict(name='CoolAir HVAC Services', city='Chennai', state='TN', street='5, GST Road, Guindy', zip='600032',
         vat='33AAKFC9087D1ZB', pan='AAKFC9087D', cat='Electrical & HVAC', bank='State Bank of India',
         ifsc='SBIN0001765', acc='38877766554', contact='Arul Doss', phone='+91 44 4209 7788'),
    dict(name='Viewtech Displays LLP', city='New Delhi', state='DL', street='B-42, Nehru Place', zip='110019',
         vat='07AAKFV1234N1ZS', pan='AAKFV1234N', cat='IT Hardware Suppliers', bank='Kotak Mahindra Bank',
         ifsc='KKBK0000182', acc='0612345987', contact='Amit Verma', phone='+91 11 4160 3322'),
    dict(name='PrintPro Office Systems', city='Hyderabad', state='TS', street='3-6-111, Himayatnagar', zip='500029',
         vat='36AAMCP5544E1Z7', pan='AAMCP5544E', cat='IT Hardware Suppliers', bank='HDFC Bank',
         ifsc='HDFC0001988', acc='50200098761234', contact='Srinivas Rao', phone='+91 40 2763 5511'),
    dict(name='Kerala Office Furnishers', city='Kochi', state='KL', street='MG Road, Ernakulam', zip='682016',
         vat='32AAHFK7766C1ZD', pan='AAHFK7766C', cat='Furniture', bank='Federal Bank',
         ifsc='FDRL0001102', acc='11020100045678', contact='Joseph Mathew', phone='+91 484 236 7788'),
    dict(name='Netcore Networks Pvt Ltd', city='Bengaluru', state='KA', street='45, Outer Ring Road, Marathahalli',
         zip='560037', vat='29AAECN3456P1ZH', pan='AAECN3456P', cat='IT Hardware Suppliers', bank='ICICI Bank',
         ifsc='ICIC0001234', acc='123405001789', contact='Pradeep Gowda', phone='+91 80 4012 6655'),
    dict(name='QuickFix Device Care', city='Chennai', state='TN', street='18, Ritchie Street, Mount Road', zip='600002',
         vat='33AAQFQ2211L1ZM', pan='AAQFQ2211L', cat='Repair & Service', bank='Karur Vysya Bank',
         ifsc='KVBL0001150', acc='1150135000004567', contact='Imran Basha', phone='+91 98410 22334'),
]

# product, qty, bids per (new vendor, existing vendor), requester, dept, ticket scenario
CYCLES = [
    dict(n=1, req='vignesh', dept='Engineering', loc='Dev Bay', warranty=36, bill_policy='hold',
         items=[('LEN-E14G5', 'Lenovo ThinkPad E14 Gen 5 (i5-1335U / 16GB / 512GB SSD)', 'Laptop', 58000,
                 '14" FHD, i5-1335U, 16GB, 512GB NVMe, Win 11 Pro', 2)],
         note='Two developers join the platform team next week.',
         agent=dict(model='LENOVO 21JKS0F400', cpu='13th Gen Intel(R) Core(TM) i5-1335U', ram=16, disk=512,
                    os='Windows 11', ver='10.0.22631', code='23H2'),
         ticket=dict(mode='software', subject='Outlook keeps crashing on startup',
                     body='<p>Since this morning Outlook closes as soon as it opens. Safe mode also crashes. '
                          'I need my mail for a client call at 3 PM.</p>', priority='high')),
    dict(n=2, req='rahul', dept='Sales', loc='Sales Bay', warranty=36, bill_policy='received',
         items=[('DEL-L5440', 'Dell Latitude 5440 (i5-1345U / 16GB / 512GB SSD)', 'Laptop', 64500,
                 '14" FHD, i5-1345U vPro, 16GB, 512GB, Win 11 Pro, 3 yr ProSupport', 2)],
         note='Field sales laptops for the new South region reps.',
         agent=dict(model='Dell Inc. Latitude 5440', cpu='13th Gen Intel(R) Core(TM) i5-1345U', ram=16, disk=512,
                    os='Windows 11', ver='10.0.22631', code='23H2'),
         ticket=dict(mode='spares', subject='Keyboard keys not working (E, R, T)',
                     body='<p>Three keys on the laptop keyboard stopped responding after a coffee spill. '
                          'External keyboard works.</p>', priority='medium',
                     spare=('SP-KB-L5440', 'Dell Latitude 5440 keyboard (US, backlit)', 2850))),
    dict(n=3, req='lakshmi', dept='Finance', loc='Finance Bay', warranty=24, bill_policy='hold',
         items=[('HP-PD400G9', 'HP ProDesk 400 G9 SFF (i5-12500 / 8GB / 512GB SSD)', 'Desktop', 52000,
                 'i5-12500, 8GB DDR4, 512GB SSD, Win 11 Pro, keyboard + mouse', 2)],
         note='Accounts desktops to replace the 2016 machines.',
         agent=dict(model='HP ProDesk 400 G9 SFF', cpu='12th Gen Intel(R) Core(TM) i5-12500', ram=8, disk=512,
                    os='Windows 11', ver='10.0.22631', code='23H2', chassis='Desktop'),
         ticket=dict(mode='external', subject='Desktop does not power on - burning smell',
                     body='<p>The desktop shut down suddenly with a burning smell and now does not power on. '
                          'Power LED stays off.</p>', priority='critical', cost=6500,
                     vendor_note='SMPS and motherboard VRM replaced under warranty labour.')),
    dict(n=4, req='suresh', dept='Operations', loc='Operations Office', warranty=24, bill_policy='received',
         items=[('APC-SRV1K', 'APC Easy UPS SRV 1kVA Online UPS', 'UPS', 24500,
                 '1kVA / 800W online double conversion, LCD, 3 yr warranty', 2)],
         note='Online UPS for the operations workstations and the router rack.',
         ticket=dict(mode='external', subject='UPS beeping continuously, backup only 2 minutes',
                     body='<p>The UPS in the operations office beeps every few seconds and the battery backup '
                          'has dropped to about two minutes.</p>', priority='high', cost=7800, general=True,
                     vendor_note='Battery bank (2 x 12V 9Ah) replaced; load test 18 minutes.')),
    dict(n=5, req='anitha', dept='Administration', loc='Meeting Room', warranty=12, bill_policy='hold',
         items=[('VOL-183V', 'Voltas 1.5 Ton 3 Star Inverter Split AC (183V Vectra)', 'Air Conditioner', 38500,
                 '1.5 TR inverter split AC, copper condenser, installation included', 2)],
         note='Meeting room and reception air conditioning.',
         ticket=dict(mode='external', subject='Meeting room AC not cooling',
                     body='<p>The AC runs but only blows normal air. Room temperature 29 C even after an hour.</p>',
                     priority='medium', cost=3200, general=True,
                     vendor_note='Gas leak at flare joint fixed, R32 top-up, cooling 16 C at the vent.')),
    dict(n=6, req='meena', dept='Engineering', loc='Dev Bay', warranty=36, bill_policy='received',
         items=[('DEL-P2423H', 'Dell P2423H 24" IPS Monitor', 'Monitor', 15900,
                 '24" FHD IPS, HDMI / DP / USB hub, height adjustable', 4)],
         note='Dual monitors for the QA engineers.',
         ticket=dict(mode='software', subject='Second monitor flickering',
                     body='<p>The second monitor flickers every few seconds. Changing the HDMI port did not help.</p>',
                     priority='low', category='Hardware')),
    dict(n=7, req='priya', dept='Sales', loc='Sales Bay', warranty=12, bill_policy='hold',
         items=[('HP-M404DN', 'HP LaserJet Pro M404dn Printer', 'Printer', 23800,
                 'Mono laser, 38 ppm, duplex, network, 1 yr onsite', 2)],
         note='One printer for the sales bay and one spare.',
         ticket=dict(mode='not_repairable', subject='Printer paper jam - fuser making grinding noise',
                     body='<p>Every page jams at the back and the fuser makes a loud grinding noise.</p>',
                     priority='medium')),
    dict(n=8, req='divya', dept='Finance', loc='Finance Bay', warranty=36, bill_policy='received',
         items=[('HP-EB840G10', 'HP EliteBook 840 G10 (i7-1355U / 16GB / 512GB SSD)', 'Laptop', 98500,
                 '14" WUXGA, i7-1355U, 16GB, 512GB, Win 11 Pro, 3 yr onsite', 1)],
         note='Laptop for the new finance controller.',
         agent=dict(model='HP EliteBook 840 14 inch G10 Notebook PC', cpu='13th Gen Intel(R) Core(TM) i7-1355U',
                    ram=16, disk=512, os='Windows 11', ver='10.0.26100', code='24H2'),
         ticket=dict(mode='software', via='email', subject='Tally not opening after Windows update',
                     body='<p>After last night\'s Windows update Tally shows "Unable to load TallyGateway". '
                          'Month-end closing is pending.</p>', priority='high')),
    dict(n=9, req='arun', dept='Operations', loc='Operations Office', warranty=12, bill_policy='hold',
         items=[('FTH-ERGO', 'Featherlite Optima Ergonomic Mesh Chair', 'Chair', 9800,
                 'Mesh back, lumbar support, 2D arms, nylon base', 5)],
         note='Ergonomic chairs for the operations team.',
         ticket=dict(mode='software', subject='Chair hydraulic not holding height',
                     body='<p>The chair slowly sinks to the lowest height within a few minutes.</p>',
                     priority='low', category='General Maintenance', general=True)),
    dict(n=10, req='gokul', dept='Engineering', loc='IT Store', warranty=36, bill_policy='received',
         items=[('CIS-C1000-24', 'Cisco Catalyst C1000-24T-4G-L 24-port Switch', 'Switch', 61500,
                 '24 x GE, 4 x 1G SFP, managed L2, rack mount', 1)],
         note='Core switch for the new floor.',
         ticket=dict(mode='external', subject='Switch ports 13-24 down',
                     body='<p>Ports 13 to 24 on the new floor switch show no link since the power cut. '
                          'Ports 1-12 work.</p>', priority='critical', cost=9500,
                     vendor_note='Port ASIC replaced under RMA; all 24 ports tested.')),
]
EXISTING = ['Nova Tech Systems Pvt Ltd', 'Prime IT Distributors LLP', 'Sri Balaji Computers',
            'INFLOW TECHNOLOGIES PRIVATE LIMITED', 'Futurenet Technology']


# ====================================================================== steps
def onboard_vendor(c, v):
    partner = SU['res.partner'].search([('name', '=', v['name'])], limit=1)
    with Step(c, 'vendor register'):
        if not partner:
            P = as_('vendor_user')['res.partner']
            partner = P.create({
                'name': v['name'], 'is_company': True, 'company_type': 'company', 'is_vendor': True,
                'email': 'sales@%s.example' % v['name'].split()[0].lower(),
                'phone': v['phone'], 'website': 'https://%s.example' % v['name'].split()[0].lower(),
                'street': v['street'], 'city': v['city'], 'zip': v['zip'],
                'state_id': STATES[v['state']].id, 'country_id': india.id, 'vat': v['vat'],
                'vendor_category_ids': [(6, 0, VCAT[v['cat']].ids)],
                'property_supplier_payment_term_id': TERMS_PAY['30 Days'].id,
                'child_ids': [(0, 0, {'name': v['contact'], 'function': 'Sales Manager', 'type': 'contact',
                                      'email': '%s@%s.example' % (v['contact'].split()[0].lower(),
                                                                  v['name'].split()[0].lower()),
                                      'phone': v['phone']})]})
            bank = get_or('res.bank', [('name', '=', v['bank'])], {'name': v['bank'], 'bic': v['ifsc'][:4]})
            SU['res.partner.bank'].create({'partner_id': partner.id, 'acc_number': v['acc'], 'bank_id': bank.id,
                                           'allow_out_payment': True})
    if not partner or partner.vendor_state == 'active':
        return partner
    with Step(c, 'vendor documents'):
        numbers = {gst_t.id: v['vat'], pan_t.id: v['pan'], chq_t.id: False}
        for doc in SU['vendor.document'].search([('vendor_id', '=', partner.id), ('state', '=', 'draft')]):
            d = as_('vendor_user')['vendor.document'].browse(doc.id)
            d.write({'document_number': numbers.get(doc.doc_type_id.id) or False, 'attachment': PDF,
                     'filename': '%s_%s.pdf' % (doc.doc_type_id.name.replace(' ', '_'), v['pan']),
                     'expiry_date': TODAY + timedelta(days=730) if doc.requires_expiry_for_vendor else False})
            d.action_submit()
        if partner.vendor_state == 'draft':
            as_('vendor_user')['res.partner'].browse(partner.id).action_submit_for_review()
    with Step(c, 'vendor approval'):
        for doc in SU['vendor.document'].search([('vendor_id', '=', partner.id)]):
            D = lambda login, d=doc: as_(login)['vendor.document'].browse(d.id)  # noqa: E731
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
    log('  vendor', partner.name, partner.vendor_code, partner.vendor_state, 'portal:', portal.login or '-')
    return partner


def approve_chain(c, order):
    for _i in range(8):
        pending = order.approval_line_ids.filtered(lambda l: l.status == 'pending')
        if not pending:
            break
        approver = pending.sorted('sequence')[0].approver_id
        SU(user=approver.id, su=False)['purchase.order'].browse(order.id).action_approve_order()


def agent_report(asset, spec, i, emp):
    """The asset agent installed on the delivered machine reports in."""
    a = spec['agent']
    host = 'FNT-%s-%s%02d' % ('DT' if a.get('chassis') == 'Desktop' else 'LT', spec['dept'][:3].upper(), spec['n'] * 10 + i)
    apps = [('Google Chrome', 'Google LLC', '130.0.6723.117'), ('Microsoft Edge', 'Microsoft Corporation', '130.0.2849.80'),
            ('Microsoft 365 Apps for enterprise - en-us', 'Microsoft Corporation', '16.0.18129.20116'),
            ('Microsoft Teams', 'Microsoft Corporation', '24295.605.3225.8804'),
            ('Zoom Workplace (64-bit)', 'Zoom Video Communications, Inc.', '6.2.5.50870'),
            ('7-Zip 24.08 (x64)', 'Igor Pavlov', '24.08'), ('Adobe Acrobat Reader (64-bit)', 'Adobe', '24.004.20219'),
            ('Quick Heal Total Security', 'Quick Heal Technologies Ltd.', '24.00')]
    if spec['dept'] == 'Finance':
        apps.append(('Tally.ERP 9', 'Tally Solutions Pvt. Ltd.', '6.6.3'))
    if spec['dept'] == 'Engineering':
        apps += [('Visual Studio Code', 'Microsoft Corporation', '1.95.1'), ('Git', 'The Git Development Community', '2.47.0'),
                 ('Python 3.12.7 (64-bit)', 'Python Software Foundation', '3.12.7150.0')]
    ip = '192.168.105.%d' % (160 + spec['n'] * 3 + i)
    payload = {
        'serial_number': asset.serial_number, 'hostname': host, 'device_name': a['model'],
        'platform': 'windows', 'os_type': 'Windows', 'os_name': a['os'], 'os_version': a['ver'],
        'os_codename': a['code'], 'processor': a['cpu'], 'ram_size': a['ram'], 'rom_size': a['disk'],
        'mac_address': ':'.join('%02X' % random.randint(0, 255) for _ in range(6)),
        'ip_address': ip, 'local_ip': ip, 'chassis_type': a.get('chassis', 'Notebook'), 'location_source': 'unavailable',
        'installed_apps': json.dumps([{'name': n, 'publisher': p, 'version': v, 'size': random.randint(5000, 900000),
                                       'installed_date': TODAY.strftime('%Y%m%d')} for n, p, v in apps])}
    # Same calls /api/laptop_monitor makes (asset first, then live metrics),
    # in-process so the run stays in this database and transaction.
    from odoo import fields as ofields
    payload.update({'public_ip': ip, 'ip_address': '%s:8000' % ip, 'last_sync_time': ofields.Datetime.now()})
    res = SU['asset.asset'].sync_from_agent(payload)
    if not res.get('success'):
        raise Exception('agent sync failed: %s' % res)
    SU['asset.live.monitoring'].update_live_metrics(asset.serial_number, {
        'cpu_usage': round(random.uniform(5, 40), 1), 'memory_usage': round(random.uniform(35, 80), 1),
        'storage_usage': round(random.uniform(20, 70), 1),
        'battery_level': 100 if a.get('chassis') == 'Desktop' else random.randint(40, 98),
        'ip_address': '%s:8000' % ip})
    return res


def run_cycle(spec):
    c = spec['n']
    tag = 'DEMO-10-%d' % c
    if SU['purchase.order'].search([('partner_ref', '=', tag)], limit=1).filtered(
            lambda o: o.state in ('purchase', 'done')):
        log('cycle', c, 'already done - skipped')
        return
    log('=== cycle %d: %s / %s' % (c, spec['dept'], spec['items'][0][1]))
    new_vendor = onboard_vendor(c, NEW_VENDORS[c - 1])
    old_vendor = SU['res.partner'].search([('name', '=', EXISTING[(c - 1) % len(EXISTING)]),
                                           ('vendor_state', '=', 'active')], limit=1)
    vendors = (new_vendor | old_vendor).filtered(lambda p: p.vendor_state == 'active')
    env.cr.commit()  # noqa: F821

    prods = [(product(code, name, cat, price, desc), qty, price, cat) for code, name, cat, price, desc, qty in spec['items']]
    requester = SU['res.users'].search([('login', '=', spec['req'])], limit=1)
    emp = requester.employee_id
    with Step(c, 'requester department'):
        dept = SU['hr.department'].search([('name', '=', spec['dept'])], limit=1)
        if emp and dept and emp.department_id != dept:
            emp.department_id = dept
    loc = LOC.get(spec['loc'])

    # ------------------------------------------------ asset request
    request = None
    with Step(c, 'asset request'):
        R = as_(spec['req'])['asset.request']
        request = R.create({'notes': spec['note'], 'line_ids': [
            (0, 0, {'description': p.display_name, 'quantity': q}) for p, q, _pr, _c in prods]})
        request.action_submit()
        mgr = request._requester_manager_user()
        as_(mgr.login if mgr else 'asset_manager')['asset.request'].browse(request.id).action_approve()
        store = as_('asset_user')['asset.request'].browse(request.id)
        for line, (p, _q, _pr, _c) in zip(store.line_ids.sorted('id'), prods):
            line.product_id = p
        store.action_check_availability()
        log('  request', request.name, request.state)
    env.cr.commit()  # noqa: F821

    # ------------------------------------------------ RFQ + bids
    rfq = SU['purchase.order']
    pu = as_('purchase_user')
    with Step(c, 'rfq'):
        vals = {}
        if request:
            act = pu['asset.request'].browse(request.id).action_create_rfq()
            vals = {k[8:]: v for k, v in (act.get('context') or {}).items()
                    if k.startswith('default_') and k != 'default_order_line'}
        vals.update({'partner_ref': tag, 'vendor_ids': [(6, 0, vendors.ids)], 'expiry_date': TODAY + timedelta(days=7),
                     'bill_policy': spec['bill_policy'],
                     'order_line': [(0, 0, {'product_id': p.id, 'product_qty': q, 'name': p.display_name,
                                            'date_planned': TODAY + timedelta(days=10)}) for p, q, _pr, _c in prods]})
        rfq = pu['purchase.order'].create(vals)
        rfq.action_send_to_vendors()
    with Step(c, 'bids'):
        for k, quote in enumerate(rfq.quote_ids.sorted(lambda q: q.vendor_id.id)):
            q = pu['vendor.quote'].browse(quote.id)
            factor = 1.0 + (0.03 if k else -0.02) * (1 if c % 2 else -1)
            for line in q.line_ids:
                base = next(pr for p, _q, pr, _c in prods if p == line.product_id)
                line.write({'vendor_price': round(base * factor, -1), 'delivery_date': TODAY + timedelta(days=5 + 3 * k),
                            'vendor_notes': 'Ex-stock' if k == 0 else 'Ships in a week from the distributor'})
            qref = '%s/Q/%d' % (quote.vendor_id.name.split()[0].upper()[:4], 2600 + c * 10 + k)
            att = SU['ir.attachment'].create({'name': '%s.pdf' % qref.replace('/', '_'), 'datas': PDF,
                                              'res_model': 'vendor.quote', 'res_id': quote.id})
            q.write({'payment_term_id': TERMS_PAY['30 Days' if k == 0 else '15 Days'].id,
                     'note': 'Prices exclusive of GST. Free delivery. Quote valid 15 days. Ref %s' % qref,
                     'quotation_attachment_ids': [(4, att.id)]})
            q.action_submit_bid()
    with Step(c, 'comparison + approval'):
        picks = SU['vendor.quote.line']
        for po_line in rfq.order_line:
            cands = rfq.comparison_line_ids.filtered(lambda q, l=po_line: q.po_line_id == l and q.vendor_price > 0)
            picks |= cands.sorted('vendor_price')[:1]
        P = pu['purchase.order'].browse(rfq.id)
        P.action_award_quote_lines(picks.ids)
        if rfq.comparison_state != 'confirmed' and hasattr(P, 'action_confirm_comparison'):
            P.action_confirm_comparison()
        P.action_submit_for_approval()
        approve_chain(c, rfq)
        if rfq.approval_status not in ('approved', False):
            raise Exception('RFQ approval stuck: %s %s' % (rfq.approval_status, rfq.approval_line_ids.mapped(
                lambda l: (l.approver_id.login, l.status))))
    with Step(c, 'confirm order -> vendor POs'):
        pu['purchase.order'].browse(rfq.id).button_confirm()
    env.cr.commit()  # noqa: F821

    children = SU['purchase.order'].search([('rfq_id', '=', rfq.id)]) if rfq else SU['purchase.order']
    for po in children:
        with Step(c, 'po %s terms + confirm' % po.name):
            if po.bill_policy != spec['bill_policy']:
                raise Exception('Bill Policy not carried to the vendor PO (%s vs %s)' % (po.bill_policy, spec['bill_policy']))
            apply_terms(po)
            po.write({'partner_ref': '%s/PO-ACK/%d' % (po.partner_id.name.split()[0].upper()[:4], 7000 + c)})
            if po.state in ('draft', 'sent'):
                as_('purchase_user')['purchase.order'].browse(po.id).button_confirm()
            if po.state not in ('purchase', 'done'):
                raise Exception('PO not confirmed: state %s' % po.state)
    with Step(c, 'bill policy locked after confirm'):
        po = children.filtered(lambda o: o.state == 'purchase')[:1]
        if po:
            other = 'received' if po.bill_policy == 'hold' else 'hold'
            try:
                with env.cr.savepoint():  # noqa: F821
                    as_('purchase_head')['purchase.order'].browse(po.id).write({'bill_policy': other})
                raise Exception('Bill Policy could still be changed on confirmed %s' % po.name)
            except Exception as e:  # noqa: BLE001
                if 'fixed once the order is confirmed' not in str(e) and 'Only a Purchase Manager' not in str(e):
                    raise
    env.cr.commit()  # noqa: F821

    # ------------------------------------------------ receipt -> assets
    for po in children:
        with Step(c, 'receive %s' % po.name):
            for picking in po.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel')):
                pk = as_('purchase_stores')['stock.picking'].browse(picking.id)
                for move in pk.move_ids:
                    move.quantity = move.product_uom_qty
                    move.picked = True
                pk.with_context(skip_backorder=True).button_validate()
    env.cr.commit()  # noqa: F821
    assets = SU['asset.asset'].search([('purchase_order_id', 'in', children.ids)], order='id')
    if not assets:
        BUGS.append((c, 'assets', 'no fixed asset created on receipt'))
        print('BUG|%s|assets|no fixed asset created on receipt' % c)
    pfx = spec['items'][0][0].split('-')[0][:3].upper()
    for i, asset in enumerate(assets):
        with Step(c, 'asset details %s' % asset.asset_code):
            po = asset.purchase_order_id
            sn = '%s%s%02d%03d' % (pfx, TODAY.strftime('%y'), c, 100 + i)
            asset.write({
                'serial_number': asset.serial_number if asset.serial_number and not asset.serial_number.startswith('NOSN')
                else sn,
                'purchase_date': po.date_approve.date() if po.date_approve else TODAY,
                'purchase_cost': po.order_line.filtered(lambda l: l.product_id == asset.product_id)[:1].price_unit
                or asset.purchase_cost,
                'warranty_template_id': W[spec['warranty']].id, 'warranty_provider_id': po.partner_id.id,
                'warranty_type': 'manufacturer', 'warranty_contract_no': 'WC-%s' % sn,
                'warranty_support_contact': '%s, %s' % (po.partner_id.name, po.partner_id.phone or ''),
                'warranty_coverage_notes': W[spec['warranty']].note,
                'location_id': loc.id if loc else False})
            if asset.state == 'draft':
                as_('asset_user')['asset.asset'].browse(asset.id).action_submit()
            asset.action_calculate_depreciation()
    # assign: one per requester (the rest stay in stock as spares)
    assigned = SU['asset.asset']
    for i, asset in enumerate(assets.filtered(lambda a: a.state == 'submit')):
        if i >= max(1, len(assets) - 1):
            break
        with Step(c, 'assign %s' % asset.asset_code):
            others = SU['hr.employee'].search([('id', '!=', emp.id), ('user_id.login', 'in', [
                'vignesh', 'meena', 'lakshmi', 'priya', 'rahul', 'suresh', 'divya', 'gokul', 'arun', 'anitha'])],
                order='id')
            pool = others.filtered(lambda e: e.department_id.name == spec['dept']) | others
            target = emp if i == 0 else pool[(i - 1) % len(pool)]
            wiz = as_('it_user')['asset.direct.assign.wizard'].create({
                'asset_id': asset.id, 'assign_type': 'employee', 'employee_id': target.id,
                'department_id': target.sudo().department_id.id, 'location_id': loc.id if loc else False})
            wiz.action_assign()
            assigned |= asset
    env.cr.commit()  # noqa: F821
    log('  assets', [(a.asset_code, a.serial_number, a.state, a.assigned_employee_id.name or '-') for a in assets])

    # ------------------------------------------------ IT asset mapping (agent)
    if spec.get('agent'):
        for i, asset in enumerate(assets):
            with Step(c, 'agent map %s' % asset.asset_code):
                agent_report(asset, spec, i, emp)
                asset.invalidate_recordset()
                dup = SU['asset.asset'].search([('serial_number', '=', asset.serial_number), ('id', '!=', asset.id)])
                if dup:
                    raise Exception('agent created a duplicate asset %s instead of updating %s'
                                    % (dup.mapped('asset_code'), asset.asset_code))
                if asset.os_platform != 'windows':
                    raise Exception('asset not mapped to the IT side: os_platform=%s' % asset.os_platform)
        log('  IT mapped', [(a.asset_code, a.os_platform, a.agent_status, a.installed_application_count) for a in assets])
    env.cr.commit()  # noqa: F821

    # ------------------------------------------------ bills -> payment
    for k, po in enumerate(children):
        with Step(c, 'bill %s' % po.name):
            if SU['account.move'].search([('invoice_origin', 'ilike', po.name), ('move_type', '=', 'in_invoice')], limit=1):
                continue
            inv = '%s/INV/26-27/%04d' % (po.partner_id.name.split()[0].upper()[:4], 300 + c * 10 + k)
            bill_id = as_('purchase_user')['purchase.order'].browse(po.id).action_upload_bill()['res_id']
            bill = as_('purchase_user')['account.move'].browse(bill_id)
            SU['ir.attachment'].create({'name': '%s.pdf' % inv.replace('/', '_'), 'datas': PDF,
                                        'res_model': 'account.move', 'res_id': bill.id})
            bill.write({'ref': inv, 'invoice_date': TODAY, 'payment_reference': inv})
            bill.action_bill_submit()
            as_(bill.bill_dept_approver_id.login or 'dept_head')['account.move'].browse(bill.id).action_dept_approve()
            as_('purchase_stores')['account.move'].browse(bill.id).action_stores_approve()
            as_('purchase_audit')['account.move'].browse(bill.id).action_audit_verify()
            as_('purchase_finance')['account.move'].browse(bill.id).action_post()
            if bill.state != 'posted':
                raise Exception('bill not posted: %s' % bill.state)
            bank = SU['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', company.id)], limit=1)
            wiz = as_('purchase_finance')['account.payment.register'].with_context(
                active_model='account.move', active_ids=bill.ids).create({'journal_id': bank.id, 'payment_date': TODAY})
            wiz.action_create_payments()
            log('  bill', bill.name, inv, bill.amount_total, bill.payment_state)
    env.cr.commit()  # noqa: F821

    # ------------------------------------------------ helpdesk
    t = spec['ticket']
    target_asset = assigned[:1] or assets[:1]
    if not target_asset:
        return
    ticket = SU['asset.helpdesk']
    with Step(c, 'raise ticket'):
        if t.get('via') == 'email':
            mail = SU['asset.helpdesk.mail'].with_context(mail_create_nolog=True).message_new({
                'subject': t['subject'], 'body': t['body'], 'email_from': '%s <%s>' % (requester.name, requester.email),
                'author_id': requester.partner_id.id})
        else:
            M = as_(spec['req'])['asset.helpdesk.mail']
            mail = M.create({'subject': t['subject'], 'body': t['body']})
            att = SU['ir.attachment'].create({'name': 'photo_issue_%d.pdf' % c, 'datas': PDF,
                                              'res_model': 'asset.helpdesk.mail', 'res_id': mail.id})
            mail.write({'attachment_ids': [(4, att.id)]})
            mail.action_submit()
        lead = 'it_user' if not t.get('general') else 'asset_manager'
        act = as_(lead)['asset.helpdesk.mail'].browse(mail.id).action_create_ticket()
        ticket = SU['asset.helpdesk'].browse(act['res_id'])
    with Step(c, 'assign ticket'):
        cat = HCAT[t.get('category') or ('General Maintenance' if t.get('general') else
                                          ('Software' if t['mode'] == 'software' else 'Hardware'))]
        team = cat.default_team_id
        ttype = {'Laptop': 'Computer Maintenance', 'Desktop': 'Computer Maintenance', 'Monitor': 'Computer Maintenance',
                 'UPS': 'Electrical Maintenance', 'Air Conditioner': 'HVAC Maintenance', 'Printer': 'Printer Maintenance',
                 'Chair': 'Furniture Repair', 'Switch': 'Network Maintenance'}[prods[0][3]]
        lead = team.leader_id.login or 'it_user'
        T = as_(lead)['asset.helpdesk'].browse(ticket.id)
        T.write({'asset_id': target_asset.id, 'location_id': target_asset.location_id.id, 'category_id': cat.id,
                 'ticket_type_id': HTYPE[ttype].id, 'priority': t['priority'], 'team_id': team.id,
                 'assigned_to_id': eng.id, 'customer_id': requester.id, 'phone': '+91 98400 %05d' % (12000 + c),
                 'issue_category': 'software' if t['mode'] == 'software' and not t.get('general') else
                 ('maintenance' if t.get('general') else 'hardware'),
                 'notes': 'Checked by phone with the user; %s.' % ('needs onsite visit' if t['mode'] != 'software'
                                                                     else 'remote session planned')})
    with Step(c, 'create task'):
        # Create Task opens a new repair form prefilled from the ticket; the
        # engineer completes it and saves.
        act = as_(lead)['asset.helpdesk'].browse(ticket.id).action_create_repair_management()
        ctx = dict(act.get('context') or {})
        vals = {'engineer_id': eng.employee_id.id, 'team_id': ticket.team_id.id,
                'request_date': TODAY, 'standby_needed': 'no'}
        if not ctx.get('default_asset_id'):
            vals['asset_id'] = target_asset.id
        as_('engineer')['repair.management'].with_context(**ctx).create(
            {k: v for k, v in vals.items() if k in SU['repair.management']._fields})
        ticket.invalidate_recordset()
        if not ticket.repair_id:
            raise Exception('Create Task made no repair')
    repair = ticket.repair_id
    if not repair:
        return
    R = as_('engineer')['repair.management'].browse(repair.id)
    with Step(c, 'repair start'):
        beh = 'general' if t.get('general') else ('software' if t['mode'] == 'software' else 'hardware')
        vals = {'description': html_text(t['body']), 'issue_type': beh}
        if RTYPE.get(beh):
            vals['issue_type_id'] = RTYPE[beh].id
        if t['mode'] in ('external',):
            repairer = SU['res.partner'].search([('name', 'in', ['QuickFix Device Care', 'Sri Balaji Computers']),
                                                 ('vendor_state', '=', 'active')], order='name', limit=1) \
                if not t.get('general') else SU['res.partner'].search(
                    [('name', 'in', ['PowerSafe UPS Solutions', 'CoolAir HVAC Services']),
                     ('vendor_state', '=', 'active')], limit=1)
            if c == 4:
                repairer = SU['res.partner'].search([('name', '=', 'PowerSafe UPS Solutions')], limit=1)
            if c == 5:
                repairer = SU['res.partner'].search([('name', '=', 'CoolAir HVAC Services')], limit=1)
            if c == 10:
                repairer = SU['res.partner'].search([('name', '=', 'Netcore Networks Pvt Ltd')], limit=1)
            vals.update({'repair_mode': 'vendor', 'vendor_id': repairer.id, 'repair_cost': t['cost']})
        R.write(vals)
        R.action_start()
    if t['mode'] == 'spares':
        with Step(c, 'spares from store'):
            sp_code, sp_name, sp_price = t['spare']
            spare = product(sp_code, sp_name, 'Spare Parts', sp_price, sp_name, asset=False)
            wh = SU['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
            if spare.with_context(location=wh.lot_stock_id.id).qty_available < 1:
                SU['stock.quant'].with_context(inventory_mode=True).create({
                    'product_id': spare.id, 'location_id': wh.lot_stock_id.id, 'inventory_quantity': 3,
                }).action_apply_inventory()
            R.write({'spare_part_ids': [(0, 0, {'product_id': spare.id, 'quantity': 1, 'description': sp_name,
                                                'cost': sp_price})]})
            act = R.action_request_spare_issue()
            as_('purchase_stores')['spare.issue'].browse(act['res_id']).action_issue()
    if t['mode'] == 'external':
        with Step(c, 'dispatch to vendor'):
            if repair.asset_id.product_id:
                R.action_dispatch_to_vendor()
        with Step(c, 'receive from vendor'):
            if repair.dispatch_picking_id:
                R.action_receive_from_vendor()
        with Step(c, 'service PO'):
            act = R.action_create_service_po()
            ctx = {k: v for k, v in act['context'].items()}
            po = as_('purchase_user')['purchase.order'].with_context(**ctx).create({'partner_ref': 'SVC-%s' % repair.name})
            po.order_line.write({'price_unit': t['cost']})
            apply_terms(po)
            P = as_('purchase_user')['purchase.order'].browse(po.id)
            # a direct order goes through the approval flow before Confirm
            if getattr(po, 'approval_status', False) in ('draft', 'not_submitted', False) \
                    and hasattr(P, 'action_submit_for_approval'):
                P.action_submit_for_approval()
            approve_chain(c, po)
            P.button_confirm()
            if po.state not in ('purchase', 'done'):
                raise Exception('service PO %s not confirmed (state %s, approval %s)' % (
                    po.name, po.state, getattr(po, 'approval_status', '-')))
            log('  service PO', po.name, po.amount_total, po.state)
    with Step(c, 'repair close'):
        R.write({'resolution_notes': t.get('vendor_note') or {
            'software': 'Repaired the Office/Tally installation, cleared the corrupt profile cache; user verified.',
            'spares': 'Keyboard replaced with the store spare; all keys tested.'}.get(t['mode'], 'Fixed onsite.')})
        if t['mode'] == 'not_repairable':
            R.action_not_repairable()
        else:
            R.action_done()
    if t['mode'] == 'not_repairable':
        with Step(c, 'replace decision'):
            spare_asset = assets.filtered(lambda a: a.state == 'submit' and a != target_asset)[:1]
            act = as_(spec['req'])['asset.helpdesk'].browse(ticket.id).action_replace_yes()
            wiz = as_(spec['req'])['asset.helpdesk.replace.wizard'].with_context(**act['context']).create(
                {'new_asset_id': spare_asset.id})
            wiz.action_replace()
    ticket.invalidate_recordset()
    repair.invalidate_recordset()
    with Step(c, 'ticket closed'):
        if ticket.state not in ('solved', 'replaced'):
            raise Exception('ticket %s still %s after repair %s' % (ticket.ticket_number, ticket.state, repair.state))
    log('  ticket', ticket.ticket_number, ticket.state, '| repair', repair.name, repair.repair_mode, repair.state,
        repair.repair_cost, '| asset', repair.asset_id.state)
    env.cr.commit()  # noqa: F821


def html_text(h):
    from odoo.tools import html2plaintext
    return html2plaintext(h or '')


for spec in CYCLES:
    try:
        run_cycle(spec)
    except Exception as e:  # noqa: BLE001
        env.cr.rollback()  # noqa: F821
        BUGS.append((spec['n'], 'cycle', repr(e)))
        print('BUG|%s|cycle|%r' % (spec['n'], e), flush=True)
        traceback.print_exc()
    env.cr.commit()  # noqa: F821

print('\n==== SUMMARY: %d issue(s)' % len(BUGS))
for b in BUGS:
    print('BUG|%s|%s|%s' % b)
