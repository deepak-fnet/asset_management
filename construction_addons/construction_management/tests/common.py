# -*- coding: utf-8 -*-
from odoo import Command
from odoo.tests.common import TransactionCase


class ConstructionTestCommon(TransactionCase):
    """Shared fixtures: a master project, one sub-project, one stage, one product.

    Deliberately builds everything from scratch rather than relying on demo data - so these
    tests stay meaningful (and pass/fail for the right reason) whether or not demo data is
    loaded, and don't silently start depending on demo records someone edits later.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Test Client'})
        cls.master_project = cls.env['cm.master.project'].create({
            'partner_id': cls.partner.id,
            'total_contract_value': 1000000.0,
        })
        cls.project = cls.env['project.project'].create({
            'name': 'Test Sub-Project',
            'master_project_id': cls.master_project.id,
            'partner_id': cls.partner.id,
            'cm_budget': 500000.0,
        })
        cls.stage = cls.env['cm.stage'].create({
            'name': 'Test Stage',
            'project_id': cls.project.id,
            'budget_allocation': 100000.0,
        })
        cls.uom_unit = cls.env.ref('uom.product_uom_unit')
        cls.product = cls.env['product.product'].create({
            'name': 'Test Cement',
            'type': 'consu',
            'is_storable': True,
            'uom_id': cls.uom_unit.id,
            # Bill on ordered quantity, not received quantity - the tests confirm a PO and
            # invoice it straight away without validating the goods receipt, so 'receive'
            # (the default) would price every bill line at qty_to_invoice=0.
            'purchase_method': 'purchase',
        })
        cls.boq_line = cls.env['cm.boq.line'].create({
            'stage_id': cls.stage.id,
            'product_id': cls.product.id,
            'quantity': 10000,
            'rate': 100.0,
        })

    def _issue_material(self, boq_line, qty):
        wizard = self.env['cm.material.issue.wizard'].create({
            'stage_id': boq_line.stage_id.id,
            'line_ids': [Command.create({'boq_line_id': boq_line.id, 'quantity': qty})],
        })
        wizard.action_issue()

    def _return_material(self, boq_line, qty):
        wizard = self.env['cm.material.return.wizard'].create({
            'stage_id': boq_line.stage_id.id,
            'line_ids': [Command.create({'boq_line_id': boq_line.id, 'quantity': qty})],
        })
        wizard.action_return()

    def _certify_measurement(self, boq_line, qty):
        measurement = self.env['cm.measurement'].create({
            'boq_line_id': boq_line.id,
            'qty_this_measurement': qty,
        })
        measurement.action_certify()
        return measurement

    def _make_paid_bill_for_stage(self, stage, amount=1000.0):
        """Create + confirm a PO, post its vendor bill, and register a full payment - needed
        anywhere final_payment_cleared must be True (e.g. before a sub-project can close)."""
        vendor = self.env['res.partner'].create({'name': 'Test Vendor', 'supplier_rank': 1})
        po = self.env['purchase.order'].create({
            'partner_id': vendor.id,
            'stage_id': stage.id,
            'project_id': stage.project_id.id,
            # vendor_management's button_confirm() requires vendor_finalized=True UNLESS
            # exactly one vendor is selected, in which case it auto-syncs partner_id and skips
            # the finalization gate - the single-vendor path used throughout this test suite.
            'vendor_ids': [Command.set([vendor.id])],
            'order_line': [Command.create({
                'product_id': self.product.id,
                'name': self.product.name,
                'product_qty': 1,
                'product_uom_id': self.uom_unit.id,
                'price_unit': amount,
                'tax_ids': [Command.clear()],
            })],
        })
        po.button_confirm()
        po.action_create_invoice()
        bill = self.env['account.move'].search([
            ('invoice_origin', '=', po.name), ('move_type', '=', 'in_invoice'),
        ], limit=1)
        bill.invoice_date = bill.date
        bill.action_post()
        register = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=bill.ids,
        ).create({})
        payment = register._create_payments()
        return bill, payment

    def _certify_and_close_stage(self, stage):
        if stage.state == 'not_started':
            stage.action_start()
        if stage.state == 'in_progress':
            stage.action_mark_completed()
        if stage.state == 'completed':
            stage.action_certify()

    def _refresh(self, record):
        """cm.boq.line's purchase/store-ledger stats (issued_qty, consumed_qty, etc.) are
        non-stored computes with NO @api.depends, by design - they're recomputed fresh on
        every new page load in the real UI (a new request = a new env = no stale cache), but
        a single long-lived recordset (exactly what a test method holds) will keep returning
        whatever value it first computed until explicitly invalidated. Call this after any
        action that changes the underlying stock moves / measurements, before re-reading."""
        record.invalidate_recordset()
