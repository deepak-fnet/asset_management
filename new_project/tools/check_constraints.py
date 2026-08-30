"""Step 6: prove every constraint fires (and that valid data still passes)."""
import sys
from datetime import timedelta
from odoo import fields
from odoo.exceptions import UserError, ValidationError

now = fields.Datetime.now()
today = fields.Date.context_today(env['res.company'])
past = now - timedelta(days=5)
past_d = today - timedelta(days=5)

comp = env['res.company'].search([('name', 'like', 'Branch 1%')])
e = env['base'].with_company(comp).with_context(
    allowed_company_ids=[comp.id], tracking_disable=True).env
vendor = e['res.partner'].search([('name', '=', 'Nova Suppliers IN 1')], limit=1)
customer = e['res.partner'].search([('name', '=', 'Orion Traders IN 1')], limit=1)
prods = e['product.product'].search([('company_id', '=', comp.id)], limit=2, order='id')
po_done = e['purchase.order'].search([('company_id', '=', comp.id), ('state', '=', 'purchase')], limit=1)
so_done = e['sale.order'].search([('company_id', '=', comp.id), ('state', '=', 'sale')], limit=1)
inv_posted = e['account.move'].search([('company_id', '=', comp.id), ('move_type', '=', 'out_invoice'),
                                       ('state', '=', 'posted')], limit=1)

results = []


def expect(label, fn, should_raise=True):
    sp = env.cr.savepoint()
    try:
        fn()
        ok = not should_raise
        detail = "no error"
    except (ValidationError, UserError) as ex:
        ok = should_raise
        detail = str(ex).strip().splitlines()[0][:90]
    except Exception as ex:                      # unexpected error type
        ok = False
        detail = "%s: %s" % (type(ex).__name__, str(ex)[:80])
    finally:
        sp.rollback()
    results.append((ok, label, detail))
    print("%-4s %-52s %s" % ("PASS" if ok else "FAIL", label, detail)); sys.stdout.flush()


def new_po(**kw):
    vals = {'partner_id': vendor.id, 'company_id': comp.id, 'date_order': now,
            'order_line': [(0, 0, {'product_id': prods[0].id, 'name': prods[0].name,
                                   'product_qty': 5, 'price_unit': 10,
                                   'date_planned': now + timedelta(days=2)})]}
    vals.update(kw)
    return e['purchase.order'].create(vals)


def new_so(**kw):
    vals = {'partner_id': customer.id, 'company_id': comp.id, 'date_order': now,
            'order_line': [(0, 0, {'product_id': prods[0].id, 'product_uom_qty': 1,
                                   'price_unit': 20})]}
    vals.update(kw)
    return e['sale.order'].create(vals)


print("\n--- PURCHASE ---")
expect("PO in the past is refused", lambda: new_po(date_order=past))
expect("PO with arrival before order date is refused",
       lambda: new_po(order_line=[(0, 0, {'product_id': prods[0].id, 'name': 'x',
                                          'product_qty': 5, 'price_unit': 10,
                                          'date_planned': now - timedelta(days=1)})]))
expect("PO with zero quantity is refused",
       lambda: new_po(order_line=[(0, 0, {'product_id': prods[0].id, 'name': 'x',
                                          'product_qty': 0, 'price_unit': 10,
                                          'date_planned': now + timedelta(days=2)})]))
expect("PO with negative price is refused",
       lambda: new_po(order_line=[(0, 0, {'product_id': prods[0].id, 'name': 'x',
                                          'product_qty': 1, 'price_unit': -10,
                                          'date_planned': now + timedelta(days=2)})]))
expect("Adding a line to a confirmed PO is refused",
       lambda: e['purchase.order.line'].with_company(comp).create({
           'order_id': po_done.id, 'product_id': prods[1].id, 'name': 'sneaky',
           'product_qty': 1, 'price_unit': 5, 'date_planned': now + timedelta(days=2)}))
expect("Editing the qty of a confirmed PO line is refused",
       lambda: po_done.order_line[0].write({'product_qty': 999}))
expect("Editing the price of a confirmed PO line is refused",
       lambda: po_done.order_line[0].write({'price_unit': 1}))
expect("Deleting a line of a confirmed PO is refused", lambda: po_done.order_line[0].unlink())
expect("Deleting a confirmed PO is refused", lambda: po_done.unlink())
expect("A valid PO is still accepted", lambda: new_po(), should_raise=False)

print("\n--- SALES ---")
expect("SO in the past is refused", lambda: new_so(date_order=past))
expect("SO delivery date before order date is refused",
       lambda: new_so(commitment_date=now - timedelta(days=1)))
expect("SO with zero quantity is refused",
       lambda: new_so(order_line=[(0, 0, {'product_id': prods[0].id,
                                          'product_uom_qty': 0, 'price_unit': 20})]))
expect("SO with a discount above 100 is refused",
       lambda: new_so(order_line=[(0, 0, {'product_id': prods[0].id, 'product_uom_qty': 1,
                                          'price_unit': 20, 'discount': 150})]))
expect("Adding a line to a confirmed SO is refused",
       lambda: e['sale.order.line'].with_company(comp).create({
           'order_id': so_done.id, 'product_id': prods[1].id,
           'product_uom_qty': 1, 'price_unit': 9}))
expect("Editing the qty of a confirmed SO line is refused",
       lambda: so_done.order_line[0].write({'product_uom_qty': 999}))
expect("Editing the price of a confirmed SO line is refused",
       lambda: so_done.order_line[0].write({'price_unit': 1}))
expect("Deleting a line of a confirmed SO is refused", lambda: so_done.order_line[0].unlink())
expect("Deleting a confirmed SO is refused", lambda: so_done.unlink())
expect("A valid SO is still accepted", lambda: new_so(), should_raise=False)

print("\n--- CRM ---")


def new_lead(**kw):
    vals = {'name': 'test opp', 'type': 'opportunity', 'partner_id': customer.id,
            'company_id': comp.id, 'email_from': customer.email,
            'expected_revenue': 100, 'date_deadline': today + timedelta(days=5)}
    vals.update(kw)
    return e['crm.lead'].create(vals)


expect("Opportunity closing in the past is refused", lambda: new_lead(date_deadline=past_d))
expect("Negative expected revenue is refused", lambda: new_lead(expected_revenue=-1))
expect("Opportunity without any contact detail is refused",
       lambda: new_lead(partner_id=False, email_from=False, phone=False))
expect("Deleting a won opportunity is refused",
       lambda: e['crm.lead'].search([('company_id', '=', comp.id),
                                     ('stage_id.is_won', '=', True)], limit=1).unlink())
expect("A valid opportunity is still accepted", lambda: new_lead(), should_raise=False)

print("\n--- INVOICING ---")


def new_inv(**kw):
    vals = {'move_type': 'out_invoice', 'partner_id': customer.id, 'company_id': comp.id,
            'invoice_date': today,
            'invoice_line_ids': [(0, 0, {'product_id': prods[0].id,
                                         'quantity': 1, 'price_unit': 30})]}
    vals.update(kw)
    return e['account.move'].create(vals)


expect("Invoice dated in the past is refused", lambda: new_inv(invoice_date=past_d))
expect("Due date before invoice date is refused",
       lambda: new_inv(invoice_date_due=past_d))
expect("Discount above 100 on an invoice line is refused",
       lambda: new_inv(invoice_line_ids=[(0, 0, {'product_id': prods[0].id, 'quantity': 1,
                                                 'price_unit': 30, 'discount': 120})]))
expect("Posting an empty invoice is refused",
       lambda: new_inv(invoice_line_ids=[]).action_post())
expect("A valid invoice posts", lambda: new_inv().action_post(), should_raise=False)

print("\n--- NO NEGATIVE STOCK ---")
quant = e['stock.quant'].search([('company_id', '=', comp.id), ('quantity', '>', 0),
                                 ('location_id.usage', '=', 'internal')], limit=1)
stock_loc = quant.location_id
prod = quant.product_id


def oversell():
    """Deliver more than what is on hand."""
    so = e['sale.order'].create({
        'partner_id': customer.id, 'company_id': comp.id, 'date_order': now,
        'order_line': [(0, 0, {'product_id': prod.id,
                               'product_uom_qty': quant.quantity + 500,
                               'price_unit': 20})]})
    so.action_confirm()
    pick = so.picking_ids[:1]
    for m in pick.move_ids:
        m.quantity = m.product_uom_qty
        m.picked = True
    pick.button_validate()


expect("Delivering more than the stock on hand is refused", oversell)
def negative_adjustment():
    quant.write({'inventory_quantity': -5, 'inventory_quantity_set': True})
    quant.action_apply_inventory()


expect("Inventory adjustment to a negative quantity is refused", negative_adjustment)
expect("Writing a negative quantity on a quant is refused",
       lambda: quant.write({'quantity': -1}))
expect("Selling within the stock on hand still works",
       lambda: e['sale.order'].create({
           'partner_id': customer.id, 'company_id': comp.id, 'date_order': now,
           'order_line': [(0, 0, {'product_id': prod.id, 'product_uom_qty': 1,
                                  'price_unit': 20})]}).action_confirm(),
       should_raise=False)

failed = [r for r in results if not r[0]]
print("\n%s/%s checks passed" % (len(results) - len(failed), len(results)))
for _, label, detail in failed:
    print("  FAILED:", label, "|", detail)
env.cr.rollback()
