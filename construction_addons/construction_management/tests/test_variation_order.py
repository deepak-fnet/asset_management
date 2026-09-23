# -*- coding: utf-8 -*-
from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ConstructionTestCommon


@tagged('post_install', '-at_install')
class TestVariationOrder(ConstructionTestCommon):

    def _confirmed_order(self):
        lead = self.env['crm.lead'].create({'name': 'Test Lead VO', 'partner_id': self.partner.id})
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'opportunity_id': lead.id,
            'order_line': [Command.create({
                'product_id': self.product.id,
                'name': 'Basement',
                'product_uom_qty': 1,
                'product_uom_id': self.uom_unit.id,
                'price_unit': 500000.0,
                'tax_ids': [Command.clear()],
            })],
        })
        order.action_confirm()
        return order

    def test_approve_adds_line_and_creates_subproject(self):
        order = self._confirmed_order()
        master = order.master_project_id
        self.assertTrue(master)
        subproject_count_before = len(master.subproject_ids)
        contract_value_before = order.amount_total

        vo = self.env['cm.variation.order'].create({
            'sale_order_id': order.id,
            'reason': 'Client requested an additional balcony.',
            'line_ids': [Command.create({
                'product_id': self.product.id,
                'name': 'Extra Balcony',
                'quantity': 1,
                'price_unit': 80000.0,
            })],
        })
        self.assertEqual(vo.amount_total, 80000.0)
        vo.action_approve()

        self.assertEqual(vo.state, 'approved')
        new_line = order.order_line.filtered(lambda l: l.variation_order_id == vo)
        self.assertTrue(new_line)
        self.assertEqual(new_line.price_unit, 80000.0)

        new_subprojects = self.env['project.project'].search([('variation_order_id', '=', vo.id)])
        self.assertEqual(len(new_subprojects), 1)
        self.assertEqual(len(master.subproject_ids), subproject_count_before + 1)
        self.assertAlmostEqual(order.amount_total, contract_value_before + 80000.0)
        self.assertAlmostEqual(master.total_contract_value, order.amount_total)

    def test_new_contract_value_preview_does_not_double_count_after_approval(self):
        # Regression for a real bug found while building this: new_contract_value used to add
        # amount_total on top of order.amount_total even after approval, when order.amount_total
        # already included the VO's own line - double-counting it.
        order = self._confirmed_order()
        vo = self.env['cm.variation.order'].create({
            'sale_order_id': order.id,
            'reason': 'Test.',
            'line_ids': [Command.create({
                'product_id': self.product.id, 'name': 'Extra', 'quantity': 1, 'price_unit': 80000.0,
            })],
        })
        vo.action_approve()
        self.assertAlmostEqual(vo.new_contract_value, order.amount_total)

    def test_reject_requires_reason(self):
        order = self._confirmed_order()
        vo = self.env['cm.variation.order'].create({
            'sale_order_id': order.id,
            'reason': 'Test.',
            'line_ids': [Command.create({
                'product_id': self.product.id, 'name': 'Extra', 'quantity': 1, 'price_unit': 1000.0,
            })],
        })
        with self.assertRaises(UserError):
            vo.action_reject()
        vo.rejection_reason = "Client did not agree to the added cost."
        vo.action_reject()
        self.assertEqual(vo.state, 'rejected')

    def test_cannot_approve_twice(self):
        order = self._confirmed_order()
        vo = self.env['cm.variation.order'].create({
            'sale_order_id': order.id,
            'reason': 'Test.',
            'line_ids': [Command.create({
                'product_id': self.product.id, 'name': 'Extra', 'quantity': 1, 'price_unit': 1000.0,
            })],
        })
        vo.action_approve()
        with self.assertRaises(UserError):
            vo.action_approve()
