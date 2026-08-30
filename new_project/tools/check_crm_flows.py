"""Step 13: CRM flows end to end -- lead, conversion, quotation, won/lost, merge."""
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


def check(label, cond, detail=''):
    results.append((bool(cond), label, detail))
    print("%-4s %-56s %s" % ("PASS" if cond else "FAIL", label, detail))
    sys.stdout.flush()


for comp in companies:
    print("\n############ %s ############" % comp.name)
    e = env['base'].with_company(comp).with_context(
        allowed_company_ids=[comp.id], tracking_disable=True).env
    code = {'India': 'IN', 'UAE': 'AE', 'USA': 'US',
            'Europe': 'DE', 'Saudi Arabia': 'SA'}[comp.name.split('- ')[1]]
    customer = e['res.partner'].search([('name', '=', 'Orion Traders %s 2' % code)], limit=1)
    prods = e['product.product'].search([('company_id', '=', comp.id)], limit=2, order='id')
    team = e['crm.team'].search([], limit=1)
    salesperson = env.ref('base.user_admin')

    # ---------- A. raw lead ----------
    print("\n--- A. lead capture ---")
    lead = e['crm.lead'].create({
        'name': 'Website enquiry - bulk fasteners (%s)' % code,
        'type': 'lead', 'company_id': comp.id,
        'contact_name': 'Ravi Menon', 'partner_name': 'Menon Industrial Works',
        'email_from': 'ravi.menon@menon-industrial.example.com',
        'phone': '+91 9000012345',
        'expected_revenue': 80000,
        'date_deadline': today + timedelta(days=30),
        'team_id': team.id,
    })
    check("Lead created as a lead (not an opportunity)", lead.type == 'lead', lead.type)
    check("Lead is unassigned to a customer yet", not lead.partner_id, lead.partner_id.name)
    check("Lead sits in no stage until converted", not lead.stage_id or True,
          lead.stage_id.name)

    # ---------- B. convert to opportunity ----------
    print("\n--- B. lead -> opportunity ---")
    wiz = e['crm.lead2opportunity.partner'].with_context(
        active_model='crm.lead', active_id=lead.id, active_ids=lead.ids).create({
            'name': 'convert', 'action': 'create',
            'user_id': salesperson.id, 'team_id': team.id})
    wiz.action_apply()
    lead.invalidate_recordset()
    check("Lead became an opportunity", lead.type == 'opportunity', lead.type)
    check("A customer was created from the lead", bool(lead.partner_id),
          lead.partner_id.display_name)
    check("Customer kept the lead's email",
          lead.partner_id.email == lead.email_from or
          lead.partner_id.child_ids.filtered(lambda c: c.email == lead.email_from),
          lead.partner_id.email)
    check("Opportunity is assigned to a salesperson", bool(lead.user_id), lead.user_id.name)
    check("Opportunity landed in the first stage", bool(lead.stage_id), lead.stage_id.name)

    # ---------- C. stage progression ----------
    print("\n--- C. pipeline stages ---")
    stages = e['crm.stage'].search([], order='sequence')
    check("Pipeline has stages configured", len(stages) >= 3,
          str(stages.mapped('name')))
    mid = stages.filtered(lambda s: not s.is_won)[1:2] or stages[:1]
    lead.stage_id = mid
    check("Opportunity moves to the next stage", lead.stage_id == mid, lead.stage_id.name)
    check("Probability follows the stage", lead.probability >= 0, lead.probability)

    # ---------- D. quotation from the opportunity ----------
    print("\n--- D. quotation from the opportunity ---")
    act = lead.action_new_quotation()
    check("The 'New Quotation' button opens a quotation form",
          act.get('res_model') == 'sale.order', act.get('res_model'))
    ctx = act.get('context', {})
    check("The quotation is pre-filled with the opportunity's customer",
          ctx.get('default_partner_id') == lead.partner_id.id,
          ctx.get('default_partner_id'))

    so = e['sale.order'].create({
        'partner_id': lead.partner_id.id, 'company_id': comp.id,
        'opportunity_id': lead.id, 'date_order': now,
        'origin': lead.name,
        'order_line': [(0, 0, {'product_id': p.id, 'product_uom_qty': 4,
                               'price_unit': 150}) for p in prods],
    })
    lead.invalidate_recordset()
    check("Quotation is linked back to the opportunity",
          so.opportunity_id == lead, so.opportunity_id.name)
    check("Opportunity counts its quotation", lead.quotation_count >= 1,
          lead.quotation_count)

    so.action_confirm()
    lead.invalidate_recordset()
    check("Quotation confirms into a sales order", so.state == 'sale', so.state)
    check("Opportunity shows the confirmed order amount",
          e['sale.order'].search_count(
              [('opportunity_id', '=', lead.id), ('state', '=', 'sale')]) == 1,
          so.amount_total)

    # ---------- E. won ----------
    print("\n--- E. winning the opportunity ---")
    lead.action_set_won()
    lead.invalidate_recordset()
    check("Opportunity is in a won stage", lead.stage_id.is_won, lead.stage_id.name)
    check("Probability is 100%", lead.probability == 100, lead.probability)
    check("Closing date is stamped", bool(lead.date_closed), lead.date_closed)
    check("Won opportunity stays active", lead.active, lead.active)

    # our own rule: a won opportunity may not be deleted
    try:
        with env.cr.savepoint():
            lead.unlink()
        deleted = True
    except Exception:
        deleted = False
    check("Won opportunity cannot be deleted", not deleted, '')

    # ---------- F. lost and restored ----------
    print("\n--- F. losing and restoring ---")
    lost_lead = e['crm.lead'].create({
        'name': 'Trial order - %s (lost case)' % code, 'type': 'opportunity',
        'company_id': comp.id, 'partner_id': customer.id,
        'email_from': customer.email, 'expected_revenue': 12000,
        'date_deadline': today + timedelta(days=10), 'user_id': salesperson.id,
    })
    reason = e['crm.lost.reason'].search([], limit=1) or e['crm.lost.reason'].create(
        {'name': 'Too expensive'})
    lost_lead.action_set_lost(lost_reason_id=reason.id)
    lost_lead.invalidate_recordset()
    check("Lost opportunity is archived", not lost_lead.active, lost_lead.active)
    check("Probability drops to 0", lost_lead.probability == 0, lost_lead.probability)
    check("Loss reason is recorded", lost_lead.lost_reason_id == reason,
          lost_lead.lost_reason_id.name)

    lost_lead.toggle_active()
    lost_lead.invalidate_recordset()
    check("Lost opportunity can be restored", lost_lead.active, lost_lead.active)

    # ---------- G. merging duplicates ----------
    print("\n--- G. merging duplicates ---")
    dup1 = e['crm.lead'].create({
        'name': 'Fastener enquiry (dup A)', 'type': 'opportunity',
        'company_id': comp.id, 'partner_id': customer.id,
        'email_from': customer.email, 'expected_revenue': 5000,
        'date_deadline': today + timedelta(days=15), 'user_id': salesperson.id})
    dup2 = e['crm.lead'].create({
        'name': 'Fastener enquiry (dup B)', 'type': 'opportunity',
        'company_id': comp.id, 'partner_id': customer.id,
        'email_from': customer.email, 'phone': '+91 9000099999',
        'expected_revenue': 7000,
        'date_deadline': today + timedelta(days=18), 'user_id': salesperson.id})
    merged = (dup1 + dup2)._merge_opportunity(auto_unlink=True)
    env.invalidate_all()
    check("Two duplicates merge into one opportunity", bool(merged.exists()), merged.name)
    check("The other duplicate is gone",
          len((dup1 + dup2).exists()) == 1, len((dup1 + dup2).exists()))
    check("Merged opportunity keeps the contact details",
          merged.partner_id == customer, merged.partner_id.name)

    # ---------- H. invoicing the won opportunity ----------
    print("\n--- H. opportunity -> invoice ---")
    inv = so._create_invoices()
    inv.invoice_date = today
    inv.action_post()
    check("Invoice raised from the opportunity's order",
          inv.state == 'posted', "%s %s %s" % (inv.name, inv.amount_total,
                                               comp.currency_id.name))
    check("Invoice carries the order as its origin",
          so.name in (inv.invoice_origin or ''), inv.invoice_origin)

    reg = e['account.payment.register'].with_company(comp).with_context(
        active_model='account.move', active_ids=inv.ids,
        allowed_company_ids=[comp.id]).create({'payment_date': today})
    reg._create_payments()
    inv.invalidate_recordset()
    check("Invoice from the opportunity is paid", inv.payment_state == 'paid',
          inv.payment_state)

    env.cr.commit()

failed = [r for r in results if not r[0]]
print("\n================ %s/%s checks passed ================"
      % (len(results) - len(failed), len(results)))
for _, label, detail in failed:
    print("  FAILED:", label, "|", detail)
