"""Asset Assign Process references move from JOIN/ to AAP/. The sequence is
noupdate, so its prefix is changed here, and existing records are renamed."""


def migrate(cr, version):
    cr.execute("""
        UPDATE ir_sequence SET prefix = 'AAP/%(year)s/'
         WHERE code = 'asset.joining.process'
    """)
    cr.execute("""
        UPDATE asset_joining_process SET name = 'AAP/' || substr(name, 6)
         WHERE name LIKE 'JOIN/%'
    """)
    cr.execute("""
        UPDATE asset_joining_process SET requested_by_id = create_uid
         WHERE requested_by_id IS NULL
    """)
