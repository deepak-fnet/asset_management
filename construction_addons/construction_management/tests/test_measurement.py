# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ConstructionTestCommon


@tagged('post_install', '-at_install')
class TestMeasurement(ConstructionTestCommon):

    def test_certify_from_draft(self):
        m = self.env['cm.measurement'].create({
            'boq_line_id': self.boq_line.id, 'qty_this_measurement': 50,
        })
        m.action_certify()
        self.assertEqual(m.state, 'certified')
        self.assertEqual(m.cumulative_qty, 50.0)

    def test_cannot_certify_twice(self):
        m = self._certify_measurement(self.boq_line, 50)
        with self.assertRaises(UserError):
            m.action_certify()

    def test_reject_requires_remarks(self):
        m = self.env['cm.measurement'].create({
            'boq_line_id': self.boq_line.id, 'qty_this_measurement': 50,
        })
        with self.assertRaises(UserError):
            m.action_reject()
        m.remarks = "Quantity overstated vs site photo."
        m.action_reject()
        self.assertEqual(m.state, 'rejected')

    def test_reset_to_draft_from_rejected(self):
        m = self.env['cm.measurement'].create({
            'boq_line_id': self.boq_line.id, 'qty_this_measurement': 50,
            'remarks': 'Not matching site.',
        })
        m.action_reject()
        m.action_reset_to_draft()
        self.assertEqual(m.state, 'draft')

    def test_cumulative_qty_only_sums_certified(self):
        self._certify_measurement(self.boq_line, 50)
        m2 = self.env['cm.measurement'].create({
            'boq_line_id': self.boq_line.id, 'qty_this_measurement': 30,
        })
        self.assertEqual(m2.cumulative_qty, 50.0, "draft record should not count itself yet")
        m2.action_certify()
        self.assertEqual(m2.cumulative_qty, 80.0)
