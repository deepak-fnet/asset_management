"""Bill approval chain gained an Audit step: the old 'stores_approved' state
(waiting for Finance) is now 'to_finance'. Bills already waiting for Finance
stay there (they were approved under the old chain)."""


def migrate(cr, version):
    cr.execute("UPDATE account_move SET bill_approval_state = 'to_finance' WHERE bill_approval_state = 'stores_approved'")
