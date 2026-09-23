# -*- coding: utf-8 -*-
from odoo.tests import tagged

from .common import ConstructionTestCommon


@tagged('post_install', '-at_install')
class TestStagePayments(ConstructionTestCommon):
    """Regression coverage for cm.stage._compute_payments().

    account.payment.action_cancel() un-reconciles the accounting entries but does not
    reliably clear the payment's own reconciled_bill_ids - without an explicit state filter,
    a cancelled payment kept inflating Payments Made / payment_count on the stage. Found via
    a real screenshot mismatch mid-session ($1.72M shown instead of $800K); fixed by filtering
    ('state', 'not in', ('canceled', 'rejected')) in the search.
    """

    def test_cancelled_payment_excluded_from_stage_totals(self):
        bill, payment = self._make_paid_bill_for_stage(self.stage, amount=1000.0)
        self.stage.invalidate_recordset()
        self.assertEqual(self.stage.payment_count, 1)
        self.assertAlmostEqual(self.stage.payment_total, payment.amount)

        payment.action_cancel()
        self.stage.invalidate_recordset()
        self.assertEqual(self.stage.payment_count, 0,
                          "a cancelled payment must not still be counted in payment_count")
        self.assertEqual(self.stage.payment_total, 0.0,
                          "a cancelled payment must not still be counted in payment_total")
