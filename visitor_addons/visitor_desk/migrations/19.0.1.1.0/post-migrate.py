"""Purpose of visit moved from a selection field to the configurable visitor.purpose model."""

KEYS = ['meeting', 'interview', 'delivery', 'vendor', 'service', 'personal', 'other']


def migrate(cr, version):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'visitor_visit' AND column_name = 'purpose'
    """)
    if not cr.fetchone():
        return
    for key in KEYS:
        cr.execute("""
            UPDATE visitor_visit v
               SET purpose_id = d.res_id
              FROM ir_model_data d
             WHERE d.module = 'visitor_desk' AND d.name = %s
               AND v.purpose = %s AND v.purpose_id IS NULL
        """, ('purpose_' + key, key))
    cr.execute("""
        UPDATE visitor_visit
           SET purpose_id = (SELECT res_id FROM ir_model_data
                              WHERE module = 'visitor_desk' AND name = 'purpose_other')
         WHERE purpose_id IS NULL
    """)
    cr.execute("ALTER TABLE visitor_visit DROP COLUMN purpose")
