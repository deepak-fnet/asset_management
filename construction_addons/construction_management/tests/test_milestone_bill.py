# -*- coding: utf-8 -*-
from odoo import Command
from odoo.tests import tagged

from .common import ConstructionTestCommon


@tagged('post_install', '-at_install')
class TestMilestoneBill(ConstructionTestCommon):
    """Regression coverage for the cumulative milestone billing math: for milestone-type rows,
    the percentage is the CUMULATIVE entitlement, and the bill amount nets out whatever the
    advance/earlier milestones already claimed - a running-account (telescoping) sum, not an
    independent flat slice. This is the exact correction the user gave a real example for:
    contract 1,100,000 -> Advance 20% (220,000), Basement 35% cumulative, Wall Raising 50%
    cumulative, Roofing 80%, Interior 90%, Retention 100%.
    """

    def _order(self, amount):
        lead = self.env['crm.lead'].create({'name': 'Lead For Milestones', 'partner_id': self.partner.id})
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'opportunity_id': lead.id,
            'order_line': [Command.create({
                'product_id': self.product.id, 'name': 'Contract', 'product_uom_qty': 1,
                'product_uom_id': self.uom_unit.id, 'price_unit': amount,
                'tax_ids': [Command.clear()],
            })],
        })

    def test_cumulative_milestone_math_matches_real_example(self):
        order = self._order(1100000.0)
        Bill = self.env['cm.milestone.bill']
        advance = Bill.create({
            'sale_order_id': order.id, 'sequence': 10, 'name': 'Advance',
            'milestone_type': 'advance', 'amount_type': 'percent', 'percentage': 20.0,
        })
        basement = Bill.create({
            'sale_order_id': order.id, 'sequence': 20, 'name': 'Basement',
            'milestone_type': 'milestone', 'amount_type': 'percent', 'percentage': 35.0,
        })
        wall_raising = Bill.create({
            'sale_order_id': order.id, 'sequence': 30, 'name': 'Wall Raising',
            'milestone_type': 'milestone', 'amount_type': 'percent', 'percentage': 50.0,
        })
        roofing = Bill.create({
            'sale_order_id': order.id, 'sequence': 40, 'name': 'Roofing',
            'milestone_type': 'milestone', 'amount_type': 'percent', 'percentage': 80.0,
        })
        interior = Bill.create({
            'sale_order_id': order.id, 'sequence': 50, 'name': 'Interior Works',
            'milestone_type': 'milestone', 'amount_type': 'percent', 'percentage': 90.0,
        })
        retention = Bill.create({
            'sale_order_id': order.id, 'sequence': 60, 'name': 'Retention',
            'milestone_type': 'retention', 'amount_type': 'percent', 'percentage': 10.0,
        })

        self.assertAlmostEqual(advance.amount, 220000.0)
        # Basement: cumulative 35% (385,000) minus Advance's 20% (220,000) = 165,000
        self.assertAlmostEqual(basement.amount, 385000.0 - 220000.0)
        # Wall Raising: cumulative 50% (550,000) minus Basement's cumulative 35% (385,000)
        self.assertAlmostEqual(wall_raising.amount, 550000.0 - 385000.0)
        self.assertAlmostEqual(roofing.amount, 880000.0 - 550000.0)
        self.assertAlmostEqual(interior.amount, 990000.0 - 880000.0)
        # Retention is a standalone flat 10% of contract value, NOT part of the milestone ladder.
        self.assertAlmostEqual(retention.amount, 110000.0)

        total_billed = (advance.amount + basement.amount + wall_raising.amount
                         + roofing.amount + interior.amount + retention.amount)
        self.assertAlmostEqual(total_billed, order.amount_total)

    def test_invoice_line_has_no_taxes(self):
        # Regression for the tax-inflation bug: a milestone invoice line must never carry taxes,
        # since the percentage is already computed off the (tax-inclusive) contract value.
        order = self._order(100000.0)
        bill = self.env['cm.milestone.bill'].create({
            'sale_order_id': order.id, 'name': 'Advance',
            'milestone_type': 'advance', 'amount_type': 'percent', 'percentage': 20.0,
        })
        bill.action_create_invoice()
        self.assertFalse(bill.invoice_id.invoice_line_ids.tax_ids)
