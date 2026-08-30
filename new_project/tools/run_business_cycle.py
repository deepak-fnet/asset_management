"""Step 4: one complete Purchase -> Stock -> CRM -> Sale -> Invoice -> Payment cycle per branch."""
import os, sys
from odoo import fields
from datetime import timedelta

ONLY = os.environ.get('NP_ONLY')          # optional branch filter
today = fields.Date.context_today(env['res.company'])
now = fields.Datetime.now()

companies = env['res.company'].search([('name', 'like', 'Branch %')], order='id')
if ONLY:
    companies = companies.filtered(lambda c: ONLY in c.name)

def validate(picking):
    picking.action_assign()
    for move in picking.move_ids:
        if not move.move_line_ids:
            move._action_assign()
        for ml in move.move_line_ids:
            ml.quantity = ml.quantity or move.product_uom_qty
        if not move.move_line_ids:
            move.quantity = move.product_uom_qty
        move.picked = True
    res = picking.button_validate()
    if isinstance(res, dict) and res.get('res_model'):
        wiz = env[res['res_model']].with_context(**res.get('context', {})).create({})
        (getattr(wiz, 'process', None) or getattr(wiz, 'action_confirm', None) or wiz.process)()
    return picking.state

def pay(move):
    Reg = env['account.payment.register'].with_company(move.company_id).with_context(
        active_model='account.move', active_ids=move.ids,
        allowed_company_ids=[move.company_id.id])
    Reg.create({'payment_date': today})._create_payments()
    return move.payment_state

summary = []
for comp in companies:
    ctx = {'allowed_company_ids': [comp.id], 'mail_create_nolog': True, 'tracking_disable': True}
    e = env['base'].with_company(comp).with_context(**ctx).env
    cc = comp.name.split('- ')[1]
    code = {'India': 'IN', 'UAE': 'AE', 'USA': 'US', 'Europe': 'DE', 'Saudi Arabia': 'SA'}[cc]
    vendor = e['res.partner'].search([('name', '=', 'Nova Suppliers %s 1' % code)], limit=1)
    customer = e['res.partner'].search([('name', '=', 'Orion Traders %s 1' % code)], limit=1)
    products = e['product.product'].search([('company_id', '=', comp.id)], limit=3, order='id')
    assert vendor and customer and len(products) == 3, "master data missing for %s" % comp.name
    line = []
    def note(msg, _l=line):
        _l.append(msg); print("   [%s] %s" % (comp.name, msg)); sys.stdout.flush()

    # ---------- 1. PURCHASE ----------
    po = e['purchase.order'].create({
        'partner_id': vendor.id, 'company_id': comp.id,
        'date_order': now,
        'order_line': [(0, 0, {
            'product_id': p.id, 'name': p.name, 'product_qty': 100,
            'price_unit': p.standard_price or 25.0,
            'date_planned': now + timedelta(days=3),
        }) for p in products],
    })
    po.button_confirm()
    note('PO %s %s %.2f %s' % (po.name, po.state, po.amount_total, comp.currency_id.name))

    receipt = po.picking_ids[:1]
    validate(receipt)
    note('Receipt %s %s' % (receipt.name, receipt.state))

    po.action_create_invoice()
    e['base'].env.invalidate_all()
    bill = e['account.move'].search(
        [('invoice_origin', '=', po.name), ('company_id', '=', comp.id),
         ('move_type', '=', 'in_invoice')], order='id desc', limit=1)
    note('Bill draft lines=%s' % len(bill.invoice_line_ids))
    bill.invoice_date = today
    bill.action_post()
    note('Bill %s %s %.2f' % (bill.name, bill.state, bill.amount_total))
    note('Bill payment %s' % pay(bill))

    onhand = sum(e['stock.quant'].search([
        ('product_id', 'in', products.ids),
        ('location_id.usage', '=', 'internal')]).mapped('quantity'))
    note('On hand after receipt: %s' % onhand)

    # ---------- 2. CRM ----------
    lead = e['crm.lead'].create({
        'name': '%s - annual supply enquiry' % customer.name,
        'type': 'opportunity', 'partner_id': customer.id, 'company_id': comp.id,
        'email_from': customer.email, 'phone': customer.phone,
        'expected_revenue': 25000, 'probability': 60,
        'date_deadline': today + timedelta(days=20),
    })
    note('Lead %s stage=%s' % (lead.name, lead.stage_id.name))

    # ---------- 3. SALE ----------
    so = e['sale.order'].create({
        'partner_id': customer.id, 'company_id': comp.id,
        'opportunity_id': lead.id, 'date_order': now,
        'commitment_date': now + timedelta(days=5),
        'order_line': [(0, 0, {
            'product_id': p.id, 'product_uom_qty': 10,
            'price_unit': p.list_price or 50.0,
        }) for p in products],
    })
    so.action_confirm()
    note('SO %s %s %.2f %s' % (so.name, so.state, so.amount_total, comp.currency_id.name))

    won_stage = e['crm.stage'].search([('is_won', '=', True)], limit=1)
    if won_stage:
        lead.stage_id = won_stage
    note('Lead won stage=%s' % lead.stage_id.name)

    delivery = so.picking_ids[:1]
    validate(delivery)
    note('Delivery %s %s' % (delivery.name, delivery.state))

    inv = so._create_invoices()
    inv.invoice_date = today
    inv.action_post()
    note('Invoice %s %s %.2f' % (inv.name, inv.state, inv.amount_total))
    note('Invoice payment %s' % pay(inv))

    onhand2 = sum(e['stock.quant'].search([
        ('product_id', 'in', products.ids),
        ('location_id.usage', '=', 'internal')]).mapped('quantity'))
    note('On hand after delivery: %s' % onhand2)

    env.cr.commit()
    summary.append((comp.name, line))

for name, line in summary:
    print("\n=== %s ===" % name)
    for l in line:
        print("   " + l)
print("\nSTEP4 OK")
