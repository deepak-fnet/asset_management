"""Step 9: advanced base flows -- backorders, vendor bills, taxes, credit notes, returns."""
import os, sys
from datetime import timedelta
from odoo import fields

now = fields.Datetime.now()
today = fields.Date.context_today(env['res.company'])
ONLY = os.environ.get('NP_ONLY')

companies = env['res.company'].search([('name', 'like', 'Branch %')], order='id')
if ONLY:
    companies = companies.filtered(lambda c: ONLY in c.name)

results = []


def check(label, condition, detail=''):
    results.append((bool(condition), label, detail))
    print("%-4s %-58s %s" % ("PASS" if condition else "FAIL", label, detail))
    sys.stdout.flush()


def set_qty(picking, qty_by_product=None):
    """Fill the done quantities, optionally only a part of them."""
    picking.action_assign()
    for move in picking.move_ids:
        wanted = move.product_uom_qty
        if qty_by_product is not None:
            wanted = qty_by_product.get(move.product_id.id, 0)
        move.quantity = wanted
        move.picked = bool(wanted)


def validate(picking, create_backorder=None):
    """Validate; answer the backorder wizard when Odoo raises one."""
    res = picking.button_validate()
    if isinstance(res, dict) and res.get('res_model') == 'stock.backorder.confirmation':
        wiz = env['stock.backorder.confirmation'].with_context(
            **res['context']).create({})
        if create_backorder is False:
            wiz.process_cancel_backorder()
        else:
            wiz.process()
        return 'backorder'
    return picking.state


for comp in companies:
    print("\n############ %s (%s) ############" % (comp.name, comp.currency_id.name))
    e = env['base'].with_company(comp).with_context(
        allowed_company_ids=[comp.id], tracking_disable=True).env
    code = {'India': 'IN', 'UAE': 'AE', 'USA': 'US',
            'Europe': 'DE', 'Saudi Arabia': 'SA'}[comp.name.split('- ')[1]]
    vendor = e['res.partner'].search([('name', '=', 'Nova Suppliers %s 1' % code)], limit=1)
    customer = e['res.partner'].search([('name', '=', 'Orion Traders %s 1' % code)], limit=1)
    prods = e['product.product'].search([('company_id', '=', comp.id)], limit=2, order='id')
    p1, p2 = prods[0], prods[1]

    # a real second tax, so the journal entry has to split across two tax accounts
    # (the generic chart only ships a 15% and a 0% sale tax)
    sale_tax = e['account.tax'].search(
        [('company_id', '=', comp.id), ('type_tax_use', '=', 'sale'),
         ('amount', '>', 0)], limit=1)
    extra_tax = e['account.tax'].search(
        [('company_id', '=', comp.id), ('name', '=', 'NP Test 5%')], limit=1)
    if not extra_tax:
        extra_tax = e['account.tax'].create({
            'name': 'NP Test 5%', 'amount': 5.0, 'amount_type': 'percent',
            'type_tax_use': 'sale', 'company_id': comp.id,
            'tax_group_id': sale_tax.tax_group_id.id,
        })
    buy_tax = e['account.tax'].search(
        [('company_id', '=', comp.id), ('type_tax_use', '=', 'purchase')], limit=1)

    # ================= A. PURCHASE BACKORDER =================
    print("\n--- A. purchase receipt backorder ---")
    po = e['purchase.order'].create({
        'partner_id': vendor.id, 'company_id': comp.id, 'date_order': now,
        'order_line': [(0, 0, {'product_id': p.id, 'name': p.name, 'product_qty': 100,
                               'price_unit': 20, 'tax_ids': [(6, 0, buy_tax.ids)],
                               'date_planned': now + timedelta(days=2)}) for p in prods],
    })
    po.button_confirm()
    receipt = po.picking_ids[:1]
    set_qty(receipt, {p1.id: 40, p2.id: 0})
    state = validate(receipt)
    check("Partial receipt raises the backorder wizard", state == 'backorder', state)
    check("First receipt is done", receipt.state == 'done', receipt.state)

    backorder = e['stock.picking'].search([('backorder_id', '=', receipt.id)])
    check("A backorder was created", len(backorder) == 1, backorder.mapped('name'))
    bo_qty = {m.product_id.id: m.product_uom_qty for m in backorder.move_ids}
    check("Backorder carries the remaining 60 + 100",
          bo_qty.get(p1.id) == 60 and bo_qty.get(p2.id) == 100, str(bo_qty))
    check("PO received quantity is 40 / 0",
          po.order_line[0].qty_received == 40 and po.order_line[1].qty_received == 0,
          str(po.order_line.mapped('qty_received')))

    # ================= B. VENDOR BILL ON RECEIVED QTY =================
    print("\n--- B. vendor bill concepts ---")
    check("Purchase method is 'on received quantities'",
          p1.purchase_method == 'receive', p1.purchase_method)
    check("Only the received qty is billable",
          po.order_line[0].qty_to_invoice == 40 and po.order_line[1].qty_to_invoice == 0,
          str(po.order_line.mapped('qty_to_invoice')))
    check("PO invoice status is 'to invoice'", po.invoice_status == 'to invoice',
          po.invoice_status)

    po.action_create_invoice()
    env.invalidate_all()
    bill1 = e['account.move'].search([('invoice_origin', '=', po.name),
                                      ('move_type', '=', 'in_invoice')], order='id desc', limit=1)
    bill1.invoice_date = today
    bill1.action_post()
    # Odoo puts every PO line on the draft bill, the not-yet-received one at qty 0.
    billed = {l.product_id.id: l.quantity for l in bill1.invoice_line_ids}
    check("First bill charges the 40 received and nothing for the rest",
          billed.get(p1.id) == 40 and billed.get(p2.id, 0) == 0, str(billed))
    check("First bill untaxed total is 40 x 20",
          comp.currency_id.compare_amounts(bill1.amount_untaxed, 800) == 0,
          bill1.amount_untaxed)
    check("Bill uses the Purchase journal", bill1.journal_id.type == 'purchase',
          bill1.journal_id.name)

    # tax split on the bill's journal entry
    tax_lines = bill1.line_ids.filtered(lambda l: l.tax_line_id)
    base_lines = bill1.line_ids.filtered(lambda l: l.display_type == 'product')
    payable = bill1.line_ids.filtered(lambda l: l.account_id.account_type == 'liability_payable')
    check("Bill journal entry splits base / tax / payable",
          base_lines and payable and (tax_lines or not buy_tax),
          "base=%s tax=%s payable=%s" % (len(base_lines), len(tax_lines), len(payable)))
    check("Bill journal entry is balanced",
          comp.currency_id.is_zero(sum(bill1.line_ids.mapped('balance'))),
          sum(bill1.line_ids.mapped('balance')))

    # receive the backorder, then bill the rest
    set_qty(backorder)
    validate(backorder)
    check("Backorder receipt is done", backorder.state == 'done', backorder.state)
    check("PO fully received",
          po.order_line[0].qty_received == 100 and po.order_line[1].qty_received == 100,
          str(po.order_line.mapped('qty_received')))

    po.action_create_invoice()
    env.invalidate_all()
    bill2 = e['account.move'].search([('invoice_origin', '=', po.name),
                                      ('move_type', '=', 'in_invoice')], order='id desc', limit=1)
    bill2.invoice_date = today
    bill2.action_post()
    check("A second bill covers the remainder", bill2.id != bill1.id, bill2.name)
    check("PO is fully invoiced after two bills", po.invoice_status == 'invoiced',
          po.invoice_status)
    check("PO shows 2 vendor bills", len(po.invoice_ids) == 2,
          str(po.invoice_ids.mapped('name')))

    # ================= C. TAXES / JOURNAL SPLIT ON A CUSTOMER INVOICE =================
    print("\n--- C. taxes and journal split ---")
    t1, t2 = sale_tax, extra_tax
    inv = e['account.move'].create({
        'move_type': 'out_invoice', 'partner_id': customer.id, 'company_id': comp.id,
        'invoice_date': today,
        'invoice_line_ids': [
            (0, 0, {'product_id': p1.id, 'quantity': 5, 'price_unit': 100,
                    'tax_ids': [(6, 0, t1.ids)]}),
            (0, 0, {'product_id': p2.id, 'quantity': 3, 'price_unit': 200,
                    'tax_ids': [(6, 0, t2.ids)]}),
        ],
    })
    inv.action_post()
    check("Customer invoice uses the Sales journal", inv.journal_id.type == 'sale',
          inv.journal_id.name)
    tax_lines = inv.line_ids.filtered(lambda l: l.tax_line_id)
    check("One journal item per distinct tax",
          len(tax_lines) == len(t1 | t2) and set(tax_lines.mapped('tax_line_id.id'))
          == set((t1 | t2).ids),
          "%s tax line(s) %s" % (len(tax_lines), tax_lines.mapped('tax_line_id.name')))
    # tax_base_amount is signed like the move, hence the abs()
    bases = sorted(abs(l.tax_base_amount) for l in tax_lines)
    check("Each tax line carries its own base (500 and 600)",
          bases == [500.0, 600.0], str(bases))
    expected_tax = sum(tax_lines.mapped(lambda l: abs(l.balance)))
    check("Tax amount matches the invoice total",
          comp.currency_id.compare_amounts(inv.amount_tax, expected_tax) == 0,
          "amount_tax=%s journal tax=%s" % (inv.amount_tax, expected_tax))
    check("Invoice journal entry is balanced",
          comp.currency_id.is_zero(sum(inv.line_ids.mapped('balance'))),
          sum(inv.line_ids.mapped('balance')))
    check("Untaxed + tax = total",
          comp.currency_id.compare_amounts(
              inv.amount_untaxed + inv.amount_tax, inv.amount_total) == 0,
          "%s + %s = %s" % (inv.amount_untaxed, inv.amount_tax, inv.amount_total))
    receivable = inv.line_ids.filtered(
        lambda l: l.account_id.account_type == 'asset_receivable')
    check("Receivable line equals the invoice total",
          comp.currency_id.compare_amounts(sum(receivable.mapped('balance')),
                                           inv.amount_total) == 0,
          sum(receivable.mapped('balance')))

    # ================= D. CREDIT NOTES =================
    print("\n--- D. credit notes ---")
    rev = e['account.move.reversal'].with_context(
        active_model='account.move', active_ids=inv.ids).create({
            'journal_id': inv.journal_id.id, 'date': today,
            'reason': 'Goods returned by the customer'})
    rev.reverse_moves()
    env.invalidate_all()
    cn = e['account.move'].search([('reversed_entry_id', '=', inv.id)], limit=1)
    check("Credit note created for the invoice", bool(cn), cn.name)
    check("Credit note type is out_refund", cn.move_type == 'out_refund', cn.move_type)
    if cn.state == 'draft':
        cn.action_post()
    check("Credit note posts", cn.state == 'posted', cn.state)
    check("Credit note mirrors the invoice amount",
          comp.currency_id.compare_amounts(cn.amount_total, inv.amount_total) == 0,
          "%s vs %s" % (cn.amount_total, inv.amount_total))
    check("Credit note carries the same taxes",
          len(cn.line_ids.filtered(lambda l: l.tax_line_id)) == len(tax_lines),
          len(cn.line_ids.filtered(lambda l: l.tax_line_id)))

    # reconcile the credit note against the invoice
    to_rec = (inv.line_ids + cn.line_ids).filtered(
        lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled)
    to_rec.reconcile()
    env.invalidate_all()
    check("Invoice is settled by the credit note",
          inv.payment_state in ('reversed', 'paid', 'in_payment'), inv.payment_state)
    check("Nothing left to pay on the invoice",
          comp.currency_id.is_zero(inv.amount_residual), inv.amount_residual)

    # vendor credit note (refund of bill 1)
    rev2 = e['account.move.reversal'].with_context(
        active_model='account.move', active_ids=bill1.ids).create({
            'journal_id': bill1.journal_id.id, 'date': today,
            'reason': 'Damaged goods returned to the vendor'})
    rev2.reverse_moves()
    env.invalidate_all()
    vcn = e['account.move'].search([('reversed_entry_id', '=', bill1.id)], limit=1)
    if vcn.state == 'draft':
        vcn.action_post()
    check("Vendor credit note created and posted",
          vcn.move_type == 'in_refund' and vcn.state == 'posted',
          "%s %s" % (vcn.name, vcn.state))

    # ================= E. STOCK RETURN =================
    print("\n--- E. stock return ---")
    qty_before = p1.with_context(allowed_company_ids=[comp.id]).qty_available
    wiz = e['stock.return.picking'].with_context(
        active_id=receipt.id, active_model='stock.picking').create({})
    for ln in wiz.product_return_moves:
        ln.quantity = 10 if ln.product_id == p1 else 0
    act = wiz.action_create_returns()
    ret = e['stock.picking'].browse(act['res_id'])
    set_qty(ret)
    validate(ret)
    env.invalidate_all()
    qty_after = p1.with_context(allowed_company_ids=[comp.id]).qty_available
    check("Return to the vendor is validated", ret.state == 'done', ret.state)
    check("Return reduced the stock by 10", qty_before - qty_after == 10,
          "%s -> %s" % (qty_before, qty_after))

    # ================= F. SALES BACKORDER, INVOICE & CUSTOMER RETURN =================
    print("\n--- F. delivery backorder and customer return ---")
    so = e['sale.order'].create({
        'partner_id': customer.id, 'company_id': comp.id, 'date_order': now,
        'order_line': [(0, 0, {'product_id': p1.id, 'product_uom_qty': 30,
                               'price_unit': 50, 'tax_ids': [(6, 0, t1.ids)]})],
    })
    so.action_confirm()
    delivery = so.picking_ids[:1]
    set_qty(delivery, {p1.id: 12})
    state = validate(delivery)
    check("Partial delivery raises the backorder wizard", state == 'backorder', state)
    out_bo = e['stock.picking'].search([('backorder_id', '=', delivery.id)])
    check("Delivery backorder holds the remaining 18",
          len(out_bo) == 1 and out_bo.move_ids[0].product_uom_qty == 18,
          str(out_bo.move_ids.mapped('product_uom_qty')))
    check("Only 12 are delivered on the order", so.order_line[0].qty_delivered == 12,
          so.order_line[0].qty_delivered)

    # invoicing policy: the generic products are 'ordered quantities'
    inv2 = so._create_invoices()
    inv2.invoice_date = today
    inv2.action_post()
    check("Invoice from a partially delivered order posts",
          inv2.state == 'posted', "%s %s" % (inv2.name, inv2.amount_total))

    set_qty(out_bo)
    validate(out_bo)
    check("Delivery backorder is done", out_bo.state == 'done', out_bo.state)
    check("Order is fully delivered", so.order_line[0].qty_delivered == 30,
          so.order_line[0].qty_delivered)

    qty_before_ret = p1.with_context(allowed_company_ids=[comp.id]).qty_available
    wiz2 = e['stock.return.picking'].with_context(
        active_id=delivery.id, active_model='stock.picking').create({})
    for ln in wiz2.product_return_moves:
        ln.quantity = 5
    ret2 = e['stock.picking'].browse(wiz2.action_create_returns()['res_id'])
    set_qty(ret2)
    validate(ret2)
    env.invalidate_all()
    qty_after_ret = p1.with_context(allowed_company_ids=[comp.id]).qty_available
    check("Customer return is validated", ret2.state == 'done', ret2.state)
    check("Customer return put 5 back in stock", qty_after_ret - qty_before_ret == 5,
          "%s -> %s" % (qty_before_ret, qty_after_ret))

    rev3 = e['account.move.reversal'].with_context(
        active_model='account.move', active_ids=inv2.ids).create({
            'journal_id': inv2.journal_id.id, 'date': today,
            'reason': 'Partial return, 5 units credited'})
    rev3.reverse_moves()
    env.invalidate_all()
    cn2 = e['account.move'].search([('reversed_entry_id', '=', inv2.id)], limit=1)
    if cn2.state == 'draft':
        cn2.action_post()
    check("Credit note issued for the returned goods",
          cn2.move_type == 'out_refund' and cn2.state == 'posted',
          "%s %s" % (cn2.name, cn2.amount_total))

    env.cr.commit()

failed = [r for r in results if not r[0]]
print("\n================ %s/%s checks passed ================"
      % (len(results) - len(failed), len(results)))
for _, label, detail in failed:
    print("  FAILED:", label, "|", detail)
