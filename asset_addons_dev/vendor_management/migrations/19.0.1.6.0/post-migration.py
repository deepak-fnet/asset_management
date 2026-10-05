"""Bill approval gained Submit and Department Head steps: the old first
step 'to_approve' (waiting Stores) no longer exists. Such bills restart from
Draft so they go through the full chain."""


def migrate(cr, version):
    cr.execute("""
        UPDATE account_move SET bill_approval_state = 'draft'
        WHERE bill_approval_state = 'to_approve'
    """)
