"""Asset Request line description is now required (the requester only
describes the need in Draft). Older lines without one take the product or
category name."""


def migrate(cr, version):
    cr.execute("""
        UPDATE asset_request_line l
           SET description = COALESCE(pt.name->>'en_US', c.name, 'Item')
          FROM asset_request_line l2
     LEFT JOIN product_product pp ON pp.id = l2.product_id
     LEFT JOIN product_template pt ON pt.id = pp.product_tmpl_id
     LEFT JOIN asset_category c ON c.id = l2.asset_category_id
         WHERE l.id = l2.id AND (l.description IS NULL OR l.description = '')
    """)
