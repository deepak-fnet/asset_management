"""Budget sample data on .115: FY budgets per department x category, settings,
and three requests run through the budget (spent / in process / over budget)."""
import base64
import traceback
from datetime import date, timedelta
from odoo import Command

SU = env['res.users'].browse(1).sudo().env  # noqa: F821
T = date.today()
FY_FROM = date(T.year if T.month >= 4 else T.year - 1, 4, 1)
FY_TO = date(FY_FROM.year + 1, 3, 31)
PDF = base64.b64encode(b'%PDF-1.4\n% demo\n').decode()


def log(*a):
    print('C|', *a, flush=True)


def as_(login):
    u = SU['res.users'].search([('login', '=', login)], limit=1)
    return SU(user=u.id, su=False) if u else SU


def cat(path):
    parent, name = path.split(' / ') if ' / ' in path else (None, path)
    dom = [('name', '=', name)] + ([('parent_id.name', '=', parent)] if parent else [])
    return SU['product.category'].search(dom, limit=1)


# settings
cfo = SU['res.users'].search([('login', '=', 'cfo')], limit=1)
SU['ir.config_parameter'].set_param('asset_budget.exceed_mode', 'approval')
SU['ir.config_parameter'].set_param('asset_budget.approver_id', cfo.id)
log('settings: approval mode, approver', cfo.name)

PLAN = {
    'Engineering': [('Office Assets / Laptop', 600000, 8), ('Office Assets / Desktop', 200000, 4),
                    ('Goods / Monitor', 150000, 10), ('Goods / Switch', 150000, 2)],
    'Sales': [('Office Assets / Laptop', 450000, 6), ('Office Assets / Printer', 60000, 2)],
    'Finance': [('Office Assets / Laptop', 250000, 3), ('Office Assets / Desktop', 80000, 2)],
    'Operations': [('Office Assets / UPS', 120000, 5), ('Office Assets / Chair', 75000, 10)],
    'Administration': [('Office Assets / Air Conditioner', 160000, 4), ('Office Assets / Chair', 40000, 5)],
}
Budget = SU['asset.budget']
for dname, rows in PLAN.items():
    dept = SU['hr.department'].search([('name', '=', dname)], limit=1)
    for path, amount, qty in rows:
        c = cat(path)
        if not (dept and c):
            log('skip', dname, path)
            continue
        b = Budget.search([('department_id', '=', dept.id), ('category_id', '=', c.id),
                           ('date_from', '=', FY_FROM)], limit=1)
        if not b:
            b = Budget.create({'department_id': dept.id, 'category_id': c.id, 'date_from': FY_FROM,
                               'date_to': FY_TO, 'budget_amount': amount, 'budget_qty': qty,
                               'ledger_code': 'CAPEX-%s-%s' % (dname[:3].upper(), c.name[:3].upper()),
                               'note': 'FY %s-%s capital budget' % (FY_FROM.year, str(FY_TO.year)[2:])})
log('budgets', Budget.search_count([]))
env.cr.commit()  # noqa: F821

# a transfer: Sales printers -> Sales laptops
s_lap = Budget.search([('department_id.name', '=', 'Sales'), ('category_id.name', '=', 'Laptop')], limit=1)
s_prn = Budget.search([('department_id.name', '=', 'Sales'), ('category_id.name', '=', 'Printer')], limit=1)
if s_lap and s_prn and not s_lap.transfer_ids:
    as_('admin')['asset.budget.transfer.wizard'].create({
        'operation': 'transfer', 'from_budget_id': s_prn.id, 'to_budget_id': s_lap.id, 'amount': 20000,
        'reason': 'Printer budget not needed this year; two extra field laptops'}).action_apply()
    as_('admin')['asset.budget.transfer.wizard'].create({
        'operation': 'add', 'from_budget_id': s_lap.id, 'amount': 50000,
        'reason': 'Board approved additional budget for the South region launch'}).action_apply()
env.cr.commit()  # noqa: F821

terms_tmpl = SU['purchase.terms.condition.template'].search([], limit=1)


def apply_terms(po):
    if not po.terms_condition_line_ids and terms_tmpl:
        po.sudo().write({'terms_template_id': terms_tmpl.id, 'terms_condition_line_ids': [
            Command.create({'term_id': t.id, 'value_id': t.value_ids[:1].id}) for t in terms_tmpl.term_ids]})


def approve_chain(order):
    for _i in range(8):
        pend = order.approval_line_ids.filtered(lambda l: l.status == 'pending')
        if not pend:
            break
        u = pend.sorted('sequence')[0].approver_id
        SU(user=u.id, su=False)['purchase.order'].browse(order.id).action_approve_order()


def run(tag, req_login, code, qty, prices, stop):
    if SU['purchase.order'].search([('partner_ref', '=', tag)], limit=1):
        log(tag, 'exists - skipped')
        return
    prod = SU['product.product'].search([('default_code', '=', code)], limit=1)
    vendors = SU['res.partner'].search([('vendor_state', '=', 'active'),
                                        ('name', 'in', ['Nova Tech Systems Pvt Ltd', 'Prime IT Distributors LLP',
                                                        'Sakthi Computer Systems', 'Shree Ganesh Enterprises'])], limit=2)
    R = as_(req_login)['asset.request']
    req = R.create({'notes': '%s - budget demo' % tag, 'line_ids': [Command.create({
        'description': prod.display_name, 'quantity': qty})]})
    req.action_submit()
    mgr = req._requester_manager_user()
    as_(mgr.login if mgr else 'asset_manager')['asset.request'].browse(req.id).action_approve()
    store = as_('asset_user')['asset.request'].browse(req.id)
    store.line_ids.product_id = prod
    store.action_check_availability()
    pu = as_('purchase_user')
    act = pu['asset.request'].browse(req.id).action_create_rfq()
    vals = {k[8:]: v for k, v in (act.get('context') or {}).items() if k.startswith('default_') and k != 'default_order_line'}
    vals.update({'partner_ref': tag, 'vendor_ids': [Command.set(vendors.ids)], 'expiry_date': T + timedelta(days=7),
                 'order_line': [Command.create({'product_id': prod.id, 'product_qty': qty, 'name': prod.display_name,
                                                'date_planned': T + timedelta(days=10)})]})
    rfq = pu['purchase.order'].create(vals)
    rfq.action_send_to_vendors()
    term = SU['account.payment.term'].search([('name', '=', '30 Days')], limit=1) or SU['account.payment.term'].search([], limit=1)
    for k, q in enumerate(rfq.quote_ids.sorted('id')):
        Q = pu['vendor.quote'].browse(q.id)
        Q.line_ids.write({'vendor_price': prices[k % len(prices)], 'delivery_date': T + timedelta(days=6 + k)})
        Q.write({'payment_term_id': term.id, 'note': 'Prices exclusive of GST.'})
        Q.action_submit_bid()
    best = rfq.comparison_line_ids.filtered(lambda l: l.vendor_price > 0).sorted('vendor_price')[:1]
    P = pu['purchase.order'].browse(rfq.id)
    P.action_award_quote_lines(best.ids)
    if rfq.comparison_state != 'confirmed':
        P.action_confirm_comparison()
    P.action_submit_for_approval()
    log(tag, req.name, req.department_id.name, rfq.name, 'over budget' if rfq.budget_exceeded else 'within budget',
        rfq.approval_line_ids.mapped(lambda l: (l.sequence, l.approver_id.login)))
    if stop == 'submitted':
        return
    approve_chain(rfq)
    P.button_confirm()
    kids = SU['purchase.order'].search([('rfq_id', '=', rfq.id)])
    for po in kids:
        apply_terms(po)
        as_('purchase_user')['purchase.order'].browse(po.id).button_confirm()
    if stop == 'confirmed':
        log(tag, 'vendor POs', kids.mapped('name'), kids.mapped('state'))
        return
    for po in kids:
        for pk in po.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel')):
            pk = as_('purchase_stores')['stock.picking'].browse(pk.id)
            for m in pk.move_ids:
                m.quantity = m.product_uom_qty
                m.picked = True
            pk.with_context(skip_backorder=True).button_validate()
        bill = as_('purchase_user')['account.move'].browse(
            as_('purchase_user')['purchase.order'].browse(po.id).action_upload_bill()['res_id'])
        SU['ir.attachment'].create({'name': 'invoice.pdf', 'datas': PDF, 'res_model': 'account.move', 'res_id': bill.id})
        ref = '%s/INV/%s' % (po.partner_id.name.split()[0].upper()[:4], tag)
        bill.write({'ref': ref, 'invoice_date': T, 'payment_reference': ref})
        bill.action_bill_submit()
        as_(bill.bill_dept_approver_id.login or 'dept_head')['account.move'].browse(bill.id).action_dept_approve()
        as_('purchase_stores')['account.move'].browse(bill.id).action_stores_approve()
        as_('purchase_audit')['account.move'].browse(bill.id).action_audit_verify()
        as_('purchase_finance')['account.move'].browse(bill.id).action_post()
        log(tag, 'bill', bill.name, bill.state)


for args in [('BUDGET-DEMO-1', 'vignesh', 'LEN-E14G5', 2, [57500, 58900], 'billed'),
             ('BUDGET-DEMO-2', 'rahul', 'DEL-L5440', 3, [63900, 64500], 'confirmed'),
             ('BUDGET-DEMO-3', 'lakshmi', 'HP-PD400G9', 2, [51500, 52800], 'submitted')]:
    try:
        with env.cr.savepoint():  # noqa: F821
            run(*args)
        env.cr.commit()  # noqa: F821
    except Exception as e:  # noqa: BLE001
        log('FAIL', args[0], type(e).__name__, str(e)[:300])
        print(''.join(l for l in traceback.format_exc().splitlines(True) if 'asset_addons_dev' in l)[-1200:])

for b in Budget.search([('line_ids', '!=', False)]) | s_lap:
    log('budget', b.name, 'total', b.total_amount, 'in process', b.reserved_amount, 'spent', b.spent_amount,
        'balance', b.balance_amount)
env.cr.commit()  # noqa: F821
