"""Check the sale-order shortage warning: warns, but never blocks."""
from odoo import fields
from datetime import timedelta
now = fields.Datetime.now()
results = []


def check(label, cond, detail=''):
    results.append(bool(cond))
    print("%-4s %-52s %s" % ("PASS" if cond else "FAIL", label, detail))


comp = env['res.company'].search([('name', 'like', 'Branch 1%')])
e = env['base'].with_company(comp).with_context(
    allowed_company_ids=[comp.id], tracking_disable=True).env
cust = e['res.partner'].search([('name', '=', 'Orion Traders IN 1')], limit=1)
prod = e['product.product'].search([('company_id', '=', comp.id)], limit=1, order='id')
free = prod.with_company(comp).qty_available
print("on hand of %s = %s\n" % (prod.default_code, free))

# --- over the stock on hand: warn, do not block ---
so = e['sale.order'].create({
    'partner_id': cust.id, 'company_id': comp.id, 'date_order': now,
    'order_line': [(0, 0, {'product_id': prod.id, 'product_uom_qty': free + 500,
                           'price_unit': 10})]})
line = so.order_line[0]
check("Shortage is detected on the line", line.np_has_shortage, line.np_shortage_qty)
check("Missing quantity is exactly the overshoot", line.np_shortage_qty == 500,
      line.np_shortage_qty)
check("Order shows the warning banner text", bool(so.np_shortage_warning),
      (so.np_shortage_warning or '')[:70])

onchange = line._np_onchange_warn_availability()
check("Editing the qty pops a warning", bool(onchange and onchange.get('warning')),
      (onchange or {}).get('warning', {}).get('title'))

so.action_confirm()
check("The order still confirms (warning only)", so.state == 'sale', so.state)
note = so.message_ids.filtered(lambda m: 'insufficient stock' in (m.body or ''))
check("Chatter records the stock shortage", bool(note), len(note))

pick = so.picking_ids[:1]
for m in pick.move_ids:
    m.quantity = m.product_uom_qty
    m.picked = True
try:
    pick.button_validate()
    blocked = False
except Exception as ex:
    blocked = True
    msg = str(ex).splitlines()[0][:60]
check("The delivery is still blocked", blocked, msg if blocked else 'validated!')

# --- within the stock on hand: silent ---
env.cr.rollback()
stocked = e['stock.quant'].search(
    [('company_id', '=', comp.id), ('location_id.usage', '=', 'internal'),
     ('quantity', '>', 10)], limit=1)
prod2 = stocked.product_id
print("\non hand of %s = %s" % (prod2.default_code, prod2.qty_available))
so2 = e['sale.order'].create({
    'partner_id': cust.id, 'company_id': comp.id, 'date_order': now,
    'order_line': [(0, 0, {'product_id': prod2.id, 'product_uom_qty': 1,
                           'price_unit': 10})]})
check("No warning when the stock covers the order",
      not so2.order_line[0].np_has_shortage and not so2.np_shortage_warning, '')
check("No popup when the stock covers the order",
      not so2.order_line[0]._np_onchange_warn_availability(), '')
env.cr.rollback()
print("\n%s/%s checks passed" % (sum(results), len(results)))
