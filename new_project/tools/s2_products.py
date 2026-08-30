import sys, time
sys.path.insert(0, "/tmp/claude-1000/-home-fnetadmin-asset-management/48375b55-f903-49f6-822d-b4640687924b/scratchpad")
from names import product_names

TARGET = int(__import__('os').environ.get('NP_TARGET', '50000'))
BATCH = 1000
companies = env['res.company'].search([('name', 'like', 'Branch %')], order='id')
categ = env.ref('product.product_category_goods')
uom = env.ref('uom.product_uom_unit')

for idx, comp in enumerate(companies):
    Tmpl = env['product.template'].with_company(comp).with_context(
        tracking_disable=True, mail_create_nolog=True, mail_notrack=True)
    existing = Tmpl.search_count([('company_id', '=', comp.id)])
    todo = TARGET - existing
    print("%s: %s existing, creating %s" % (comp.name, existing, todo)); sys.stdout.flush()
    if todo <= 0:
        continue
    gen = product_names(todo, offset=idx * 50000 + existing)
    made, t0 = 0, time.time()
    while made < todo:
        n = min(BATCH, todo - made)
        vals = []
        for j in range(n):
            seq = idx * 50000 + existing + made + j
            cost = round(5 + (seq % 4000) * 0.37, 2)
            vals.append({
                'name': next(gen),
                'default_code': 'B%s-%06d' % (idx + 1, existing + made + j + 1),
                'type': 'consu',
                'is_storable': True,
                'categ_id': categ.id,
                'uom_id': uom.id,
                'standard_price': cost,
                'list_price': round(cost * 1.35, 2),
                'company_id': comp.id,
                'purchase_ok': True,
                'sale_ok': True,
            })
        Tmpl.create(vals)
        made += n
        env.cr.commit()
        if made % 10000 == 0 or made == todo:
            print("   %s/%s in %.0fs" % (made, todo, time.time() - t0)); sys.stdout.flush()
print("STEP2 OK")
