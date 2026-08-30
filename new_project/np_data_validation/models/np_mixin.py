from odoo import fields
from odoo.exceptions import ValidationError

DATE_MSG = "%s cannot be set in the past. %s given, today is %s."


def check_not_past(record, field_name, label):
    """Raise if the (date|datetime) field of `record` is earlier than today.

    Comparison is made on the *user's* date so a document created late in the
    evening in a +05:30 timezone is not rejected.
    """
    value = record[field_name]
    if not value:
        return
    if hasattr(value, 'hour'):  # datetime field, stored in UTC
        value_date = fields.Datetime.context_timestamp(record, value).date()
    else:
        value_date = value
    today = fields.Date.context_today(record)
    if value_date < today:
        raise ValidationError(DATE_MSG % (label, value_date, today))


def check_order(record, early_field, late_field, early_label, late_label):
    """Raise if `late_field` happens strictly before `early_field`."""
    early, late = record[early_field], record[late_field]
    if not early or not late:
        return
    if hasattr(early, 'date') and not hasattr(late, 'date'):
        early = early.date()
    if hasattr(late, 'date') and not hasattr(early, 'date'):
        late = late.date()
    if late < early:
        raise ValidationError(
            "%s (%s) cannot be earlier than %s (%s)."
            % (late_label, late, early_label, early)
        )


def check_locked_lines(lines, locked_states, state_field='state', doc_label="document",
                       operation="modified"):
    """Raise if any line belongs to a parent in one of `locked_states`."""
    for line in lines:
        order = line[line._np_parent_field]
        if order and order[state_field] in locked_states:
            raise ValidationError(
                "%s %s is confirmed: its lines can no longer be %s.\n"
                "Cancel or reset the %s to draft first."
                % (doc_label, order.display_name, operation, doc_label.lower())
            )
