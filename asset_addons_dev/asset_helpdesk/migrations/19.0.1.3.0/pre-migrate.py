"""Ticket states are now New -> Created (task created) -> Closed; the
ticket-level Assigned / In Progress steps are gone."""


def migrate(cr, version):
    cr.execute("UPDATE asset_helpdesk SET state = 'created' WHERE state IN ('assigned', 'in_progress')")
    for col in ('old_state', 'new_state'):
        cr.execute("UPDATE asset_helpdesk_history SET %s = 'created' WHERE %s IN ('assigned', 'in_progress')" % (col, col))
