# -*- coding: utf-8 -*-
from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ConstructionTestCommon


@tagged('post_install', '-at_install')
class TestHandoverCertificate(ConstructionTestCommon):

    def _ready_to_close_project(self):
        self._certify_and_close_stage(self.stage)
        self._make_paid_bill_for_stage(self.stage)
        self.project.invalidate_recordset()
        self.assertTrue(self.project.final_payment_cleared)

    def test_close_blocked_until_checklist_complete(self):
        self._ready_to_close_project()
        with self.assertRaises(UserError):
            self.project.action_close_subproject()

        self.project.write({
            'final_inspection_done': True, 'client_signoff': True,
            'keys_handed_over': True, 'electricity_connection_transferred': True,
        })
        # warranty_documents_handed_over and water_connection_transferred still missing
        with self.assertRaises(UserError):
            self.project.action_close_subproject()

    def test_close_creates_certificate_idempotently(self):
        self._ready_to_close_project()
        self.project.write({
            'final_inspection_done': True, 'client_signoff': True, 'keys_handed_over': True,
            'electricity_connection_transferred': True, 'water_connection_transferred': True,
            'warranty_documents_handed_over': True, 'warranty_months': 12,
        })
        self.project.action_close_subproject()
        self.assertEqual(self.project.cm_status, 'closed')
        self.assertEqual(self.project.handover_certificate_count, 1)

        cert = self.project.handover_certificate_ids
        self.assertTrue(cert.name.startswith('HC-'))

        from dateutil.relativedelta import relativedelta
        self.assertEqual(cert.warranty_expiry_date, cert.handover_date + relativedelta(months=12))

        # calling the helper again must not create a duplicate
        dup = self.project._cm_create_handover_certificate()
        self.assertEqual(dup, cert)
        self.assertEqual(self.project.handover_certificate_count, 1)


@tagged('post_install', '-at_install')
class TestSnagItem(ConstructionTestCommon):

    def _closed_project_with_certificate(self):
        self._certify_and_close_stage(self.stage)
        self._make_paid_bill_for_stage(self.stage)
        self.project.write({
            'final_inspection_done': True, 'client_signoff': True, 'keys_handed_over': True,
            'electricity_connection_transferred': True, 'water_connection_transferred': True,
            'warranty_documents_handed_over': True, 'warranty_months': 6,
        })
        self.project.action_close_subproject()
        return self.project

    def test_rectification_material_kept_separate_from_stage(self):
        project = self._closed_project_with_certificate()
        snag = self.env['cm.snag.item'].create({
            'project_id': project.id,
            'description': 'Bathroom leak.',
            'line_ids': [Command.create({'product_id': self.product.id, 'quantity': 2, 'rate': 150.0})],
        })
        self.assertTrue(snag.is_within_dlp)
        self.assertEqual(snag.rectification_cost_total, 300.0)

        issued_qty_before = self.boq_line.issued_qty
        action = snag.action_issue_rectification_material()
        picking = self.env['stock.picking'].browse(action['res_id'])

        self.assertTrue(picking.is_snag_rectification)
        self.assertEqual(picking.snag_id, snag)
        self.assertFalse(picking.is_material_issue)
        self.assertFalse(picking.stage_id)
        self.boq_line.invalidate_recordset()
        self.assertEqual(self.boq_line.issued_qty, issued_qty_before,
                          "rectification material must not appear in the original stage's own "
                          "Store Ledger issued_qty")

    def test_status_flow_and_client_reject(self):
        project = self._closed_project_with_certificate()
        snag = self.env['cm.snag.item'].create({
            'project_id': project.id, 'description': 'Door not closing.',
        })
        with self.assertRaises(UserError):
            snag.action_mark_fixed()
        snag.action_start()
        snag.action_mark_fixed()
        with self.assertRaises(UserError):
            snag.action_client_reject()
        snag.client_remarks = "Still not fixed."
        snag.action_client_reject()
        self.assertEqual(snag.state, 'in_progress')
        snag.action_mark_fixed()
        snag.action_client_verify()
        self.assertEqual(snag.state, 'verified')

    def test_retention_invoice_held_back_while_snag_open(self):
        project = self._closed_project_with_certificate()
        lead = self.env['crm.lead'].create({'name': 'Lead For Retention', 'partner_id': self.partner.id})
        lead.master_project_id = self.master_project.id
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'opportunity_id': lead.id,
            'order_line': [Command.create({
                'product_id': self.product.id, 'name': 'Contract', 'product_uom_qty': 1,
                'product_uom_id': self.uom_unit.id, 'price_unit': 100000.0,
                'tax_ids': [Command.clear()],
            })],
        })
        retention = self.env['cm.milestone.bill'].create({
            'sale_order_id': order.id,
            'name': 'Retention Release',
            'milestone_type': 'retention',
            'amount_type': 'percent',
            'percentage': 10.0,
            'release_date': fields.Date.context_today(self.env['cm.milestone.bill']),
        })
        open_snag = self.env['cm.snag.item'].create({
            'project_id': project.id, 'description': 'Still open defect.',
        })

        self.env['cm.milestone.bill']._cron_create_retention_invoices()
        retention.invalidate_recordset()
        self.assertEqual(retention.state, 'to_invoice',
                          "retention must be held back while a snag on this project is open")
        self.assertTrue(any('held back' in (m.body or '') for m in retention.message_ids))

        open_snag.action_start()
        open_snag.action_mark_fixed()
        open_snag.action_client_verify()
        self.env['cm.milestone.bill']._cron_create_retention_invoices()
        retention.invalidate_recordset()
        self.assertEqual(retention.state, 'invoiced',
                          "retention should release once every snag is client-verified")
