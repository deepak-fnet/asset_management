# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ConstructionTestCommon


@tagged('post_install', '-at_install')
class TestStoreLedger(ConstructionTestCommon):
    """Regression coverage for cm.boq.line._compute_purchase_stats().

    This is the exact method named in the 'automated tests' discussion: a silent typo here
    (e.g. an accidentally-removed return-move exclusion) would previously only be caught if
    someone happened to manually re-test Store Ledger after touching this file.
    """

    def test_issue_material_increases_issued_qty(self):
        self._issue_material(self.boq_line, 1600)
        self.assertEqual(self.boq_line.issued_qty, 1600.0)
        self.assertEqual(self.boq_line.returned_qty, 0.0)

    def test_return_nets_out_of_issued_qty_not_double_counted(self):
        # This is the exact 1600 issued / 1500 used / 100 returned example this feature was
        # built around - and the exact bug (received_qty double-counting the return move,
        # inflating balance_qty by 200 instead of 100) found and fixed while building it.
        self._issue_material(self.boq_line, 1600)
        self._certify_measurement(self.boq_line, 1500)
        self.assertEqual(self.boq_line.returnable_qty, 100.0)

        balance_before = self.boq_line.balance_qty
        self._return_material(self.boq_line, 100)
        self._refresh(self.boq_line)

        self.assertEqual(self.boq_line.issued_qty, 1500.0,
                          "net issued_qty should drop by exactly the returned amount")
        self.assertEqual(self.boq_line.returned_qty, 100.0)
        self.assertEqual(self.boq_line.returnable_qty, 0.0)
        self.assertEqual(self.boq_line.balance_qty, balance_before + 100.0,
                          "balance_qty must rise by exactly 100, not double-count the return "
                          "as if it were also a fresh purchase receipt")

    def test_consumed_qty_only_counts_certified_measurements(self):
        self._issue_material(self.boq_line, 500)
        draft_measurement = self.env['cm.measurement'].create({
            'boq_line_id': self.boq_line.id, 'qty_this_measurement': 200,
        })
        self.assertEqual(self.boq_line.consumed_qty, 0.0,
                          "a draft (uncertified) measurement must not count as consumed")
        draft_measurement.action_certify()
        self._refresh(self.boq_line)
        self.assertEqual(self.boq_line.consumed_qty, 200.0)

    def test_rejected_measurement_does_not_count_as_consumed(self):
        self._issue_material(self.boq_line, 500)
        measurement = self.env['cm.measurement'].create({
            'boq_line_id': self.boq_line.id, 'qty_this_measurement': 200,
            'remarks': 'Overstated vs site photo.',
        })
        measurement.action_reject()
        self.assertEqual(self.boq_line.consumed_qty, 0.0)
        self.assertEqual(self.boq_line.returnable_qty, 500.0)

    def test_material_issue_auto_creates_draft_measurement(self):
        count_before = self.env['cm.measurement'].search_count([('boq_line_id', '=', self.boq_line.id)])
        self._issue_material(self.boq_line, 300)
        measurements = self.env['cm.measurement'].search([('boq_line_id', '=', self.boq_line.id)])
        self.assertEqual(len(measurements), count_before + 1)
        self.assertEqual(measurements[-1].state, 'draft')
        self.assertFalse(measurements[-1].remarks,
                          "remarks must stay empty on auto-created measurements - reserved for "
                          "the human reviewer, not the system note (which goes to chatter instead)")

    def test_quick_issue_wizard_only_offers_outstanding_lines(self):
        self.boq_line.quantity = 100
        self._issue_material(self.boq_line, 100)
        with self.assertRaises(UserError):
            self.stage.action_open_quick_material_issue()
