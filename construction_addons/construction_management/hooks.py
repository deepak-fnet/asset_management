# -*- coding: utf-8 -*-
def post_init_hook(env):
    """Force the company currency to INR for a fresh install with demo data.

    Odoo refuses a normal write to res.company.currency_id once any account.move.line
    exists (which demo data for crm/sale/account may already have created by this point),
    so this goes straight to SQL - the same approach already used to fix the currency on
    the construction_test database during development.
    """
    inr = env['res.currency'].with_context(active_test=False).search([('name', '=', 'INR')], limit=1)
    if not inr:
        return
    if not inr.active:
        inr.active = True
    if not env['res.currency.rate'].search_count([('currency_id', '=', inr.id)]):
        env['res.currency.rate'].create({'currency_id': inr.id, 'rate': 1.0})
    env.cr.execute(
        "UPDATE res_company SET currency_id = %s WHERE currency_id != %s",
        (inr.id, inr.id),
    )
