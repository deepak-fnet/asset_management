"""Advance % left the payment terms: the buyer now enters a percentage or an
amount when requesting an advance. Drop the unused column."""


def migrate(cr, version):
    cr.execute("ALTER TABLE account_payment_term DROP COLUMN IF EXISTS advance_percent")
    cr.execute("DELETE FROM ir_model_fields WHERE model = 'account.payment.term' AND name = 'advance_percent'")
