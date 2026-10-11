# -*- coding: utf-8 -*-
"""Procedural demo data generator.

Odoo's declarative demo/ XML can only assign fields - it cannot confirm a PO, validate a
stock picking or post an invoice. This model does that multi-step work in Python and is
invoked once, via demo/cm_demo_data.xml's <function> tag, on a fresh database with
demo data enabled.
"""
from datetime import timedelta

from odoo import Command, api, fields, models


class ConstructionDemoData(models.AbstractModel):
    _name = 'cm.demo.data'
    _description = 'Construction Management Demo Data Generator'

    @api.model
    def _generate_demo_data(self):
        vendor = self._demo_vendor()
        products = self._demo_products()
        self._demo_flagship_project(vendor, products)
        self._demo_second_project(vendor, products)

    # ------------------------------------------------------------------
    # Shared masters
    # ------------------------------------------------------------------
    def _demo_vendor(self):
        return self.env['res.partner'].create({
            'name': 'Chennai Building Materials Co.',
            'company_type': 'company',
            'supplier_rank': 1,
            'city': 'Chennai',
            'state_id': self.env.ref('base.state_in_tn').id,
            'country_id': self.env.ref('base.in').id,
        })

    def _demo_products(self):
        uom_unit = self.env.ref('uom.product_uom_unit')
        uom_kg = self.env.ref('uom.product_uom_kgm')
        uom_bag = self.env.ref('uom.product_uom_unit')
        uom_sqft = self.env.ref('uom.product_uom_unit')
        uom_hour = self.env.ref('uom.product_uom_hour')

        def make(name, uom, cost, price, storable=True):
            vals = {
                'name': name,
                'type': 'consu' if storable else 'service',
                'is_storable': storable,
                'uom_id': uom.id,
                'standard_price': cost,
                'list_price': price,
                'purchase_method': 'purchase',
            }
            return self.env['product.product'].create(vals)

        return {
            'cement': make('Cement (OPC 53 Grade)', uom_bag, 380.0, 420.0),
            'steel': make('TMT Steel Bar 12mm', uom_kg, 68.0, 75.0),
            'bricks': make('Red Clay Bricks', uom_unit, 8.0, 10.0),
            'sand': make('River Sand', uom_unit, 55.0, 62.0),
            'tiles': make('Vitrified Floor Tiles', uom_sqft, 45.0, 55.0),
            'paint': make('Exterior Emulsion Paint', uom_unit, 320.0, 380.0),
            'pipe': make('PVC Plumbing Pipe', uom_unit, 210.0, 250.0),
            'electrical': make('Electrical Wiring Kit', uom_unit, 1800.0, 2100.0),
            'mason_labour': make('Mason Labour (Contract)', uom_hour, 45.0, 60.0, storable=False),
            'plumber_labour': make('Plumbing Labour (Contract)', uom_hour, 50.0, 65.0, storable=False),
        }

    def _po_vendor_bill(self, stage, product, qty, rate, vendor, days_ago):
        """Confirm a single-vendor PO for `product`, receive it and post+pay a matching bill."""
        order = self.env['purchase.order'].create({
            'partner_id': vendor.id,
            'stage_id': stage.id,
            'project_id': stage.project_id.id,
            'vendor_ids': [Command.set([vendor.id])],
            'date_order': fields.Datetime.now() - timedelta(days=days_ago),
            'order_line': [Command.create({
                'product_id': product.id,
                'name': product.display_name,
                'product_qty': qty,
                'product_uom_id': product.uom_id.id,
                'price_unit': rate,
                'tax_ids': [Command.clear()],
            })],
        })
        order.button_confirm()
        if product.type != 'service':
            picking = order.picking_ids[:1]
            if picking:
                picking.action_confirm()
                picking.action_assign()
                for move in picking.move_ids:
                    move.quantity = move.product_uom_qty
                    move.picked = True
                picking.button_validate()
        order.action_create_invoice()
        bill = order.invoice_ids[:1]
        if bill:
            bill.invoice_date = fields.Date.context_today(self) - timedelta(days=max(days_ago - 2, 0))
            bill.action_post()
            register = self.env['account.payment.register'].with_context(
                active_model='account.move', active_ids=bill.ids,
            ).create({})
            register._create_payments()
        return order

    def _issue_material(self, stage, product, qty):
        wizard = self.env['cm.material.issue.wizard'].create({
            'stage_id': stage.id,
            'line_ids': [Command.create({
                'boq_line_id': stage.boq_line_ids.filtered(lambda l: l.product_id == product)[:1].id,
                'quantity': qty,
            })],
        })
        wizard.action_issue()

    def _certify_measurement(self, stage, product, qty):
        boq_line = stage.boq_line_ids.filtered(lambda l: l.product_id == product)[:1]
        measurement = self.env['cm.measurement'].search([
            ('boq_line_id', '=', boq_line.id), ('state', '=', 'draft'),
        ], limit=1)
        if measurement:
            measurement.qty_this_measurement = qty
        else:
            measurement = self.env['cm.measurement'].create({
                'boq_line_id': boq_line.id,
                'qty_this_measurement': qty,
            })
        measurement.action_certify()
        return measurement

    # ------------------------------------------------------------------
    # Flagship project: full lifecycle walkthrough
    # ------------------------------------------------------------------
    def _demo_flagship_project(self, vendor, products):
        client = self.env['res.partner'].create({
            'name': 'Anitha Sundaram',
            'company_type': 'person',
            'city': 'Chennai',
            'state_id': self.env.ref('base.state_in_tn').id,
            'country_id': self.env.ref('base.in').id,
        })

        lead = self.env['crm.lead'].create({
            'name': '2BHK Independent House Construction - Anitha Sundaram',
            'partner_id': client.id,
            'type': 'opportunity',
            'land_extent': '2400 sq.ft plot',
        })

        boq_specs = [
            (products['cement'], 500, 400.0),
            (products['steel'], 3500, 70.0),
            (products['bricks'], 20000, 9.0),
            (products['sand'], 800, 58.0),
            (products['tiles'], 1800, 50.0),
            (products['paint'], 40, 350.0),
            (products['pipe'], 120, 230.0),
            (products['electrical'], 1, 1900.0),
            (products['mason_labour'], 600, 50.0),
        ]
        self.env['cm.boq.line'].create([{
            'lead_id': lead.id,
            'product_id': product.id,
            'quantity': qty,
            'rate': rate,
        } for product, qty, rate in boq_specs])

        lead.action_create_costing()
        costing = lead.costing_id
        costing.line_ids.write({'margin_percent': 18.0})
        costing.action_confirm()
        costing.action_create_quotation()
        order = costing.sale_order_id
        order.retention_percent = 10.0
        order.dlp_months = 6

        # Replace the raw per-BOQ-item costing lines with two contract lines, one per
        # Sub-Project (Foundation vs Finishing), each carrying its own share of the value.
        order.order_line.unlink()
        order.write({'order_line': [
            Command.create({
                'product_id': products['cement'].id,
                'name': 'Foundation & Structure Work',
                'product_uom_qty': 1,
                'product_uom_id': products['cement'].uom_id.id,
                'price_unit': 850000.0,
                'tax_ids': [Command.clear()],
            }),
            Command.create({
                'product_id': products['tiles'].id,
                'name': 'Finishing & Interiors Work',
                'product_uom_qty': 1,
                'product_uom_id': products['tiles'].uom_id.id,
                'price_unit': 650000.0,
                'tax_ids': [Command.clear()],
            }),
        ]})
        order.action_confirm()

        master = order.master_project_id
        master.advance_received = 150000.0
        foundation_project, finishing_project = master.subproject_ids.sorted('id')

        today = fields.Date.context_today(self)
        foundation_stage = self.env['cm.stage'].create({
            'project_id': foundation_project.id,
            'name': 'Foundation & Basement',
            'budget_allocation': 500000.0,
            'target_date': today - timedelta(days=30),
        })
        walls_stage = self.env['cm.stage'].create({
            'project_id': foundation_project.id,
            'name': 'Walls & Roofing',
            'budget_allocation': 350000.0,
            'target_date': today + timedelta(days=20),
        })
        finishing_stage = self.env['cm.stage'].create({
            'project_id': finishing_project.id,
            'name': 'Flooring & Painting',
            'budget_allocation': 400000.0,
            'target_date': today + timedelta(days=60),
        })
        electrical_stage = self.env['cm.stage'].create({
            'project_id': finishing_project.id,
            'name': 'Electrical & Plumbing',
            'budget_allocation': 250000.0,
            'target_date': today + timedelta(days=75),
        })

        self.env['cm.boq.line'].create([
            {'stage_id': foundation_stage.id, 'product_id': products['cement'].id, 'quantity': 250, 'rate': 400.0},
            {'stage_id': foundation_stage.id, 'product_id': products['steel'].id, 'quantity': 1800, 'rate': 70.0},
            {'stage_id': foundation_stage.id, 'product_id': products['sand'].id, 'quantity': 400, 'rate': 58.0},
            {'stage_id': walls_stage.id, 'product_id': products['bricks'].id, 'quantity': 20000, 'rate': 9.0},
            {'stage_id': walls_stage.id, 'product_id': products['cement'].id, 'quantity': 200, 'rate': 400.0},
            {'stage_id': walls_stage.id, 'product_id': products['mason_labour'].id, 'quantity': 400, 'rate': 50.0},
            {'stage_id': finishing_stage.id, 'product_id': products['tiles'].id, 'quantity': 1800, 'rate': 50.0},
            {'stage_id': finishing_stage.id, 'product_id': products['paint'].id, 'quantity': 40, 'rate': 350.0},
            {'stage_id': electrical_stage.id, 'product_id': products['pipe'].id, 'quantity': 120, 'rate': 230.0},
            {'stage_id': electrical_stage.id, 'product_id': products['electrical'].id, 'quantity': 1, 'rate': 1900.0},
            {'stage_id': electrical_stage.id, 'product_id': products['plumber_labour'].id, 'quantity': 150, 'rate': 55.0},
        ])

        self.env['cm.budget.line'].create([
            {'project_id': foundation_project.id, 'cost_head': 'material', 'planned_amount': 700000.0},
            {'project_id': foundation_project.id, 'cost_head': 'labour', 'planned_amount': 30000.0},
            {'project_id': finishing_project.id, 'cost_head': 'material', 'planned_amount': 550000.0},
            {'project_id': finishing_project.id, 'cost_head': 'labour', 'planned_amount': 12000.0},
        ])

        # --- Foundation stage: fully executed and certified ---
        foundation_stage.action_start()
        self._po_vendor_bill(foundation_stage, products['cement'], 250, 405.0, vendor, days_ago=40)
        self._po_vendor_bill(foundation_stage, products['steel'], 1800, 72.0, vendor, days_ago=38)
        self._po_vendor_bill(foundation_stage, products['sand'], 400, 60.0, vendor, days_ago=36)
        self._issue_material(foundation_stage, products['cement'], 250)
        self._issue_material(foundation_stage, products['steel'], 1800)
        self._issue_material(foundation_stage, products['sand'], 400)
        self._certify_measurement(foundation_stage, products['cement'], 245)
        self._certify_measurement(foundation_stage, products['steel'], 1790)
        self._certify_measurement(foundation_stage, products['sand'], 395)
        foundation_stage.action_mark_completed()
        foundation_stage.action_certify()

        # Return leftover cement/steel/sand to the warehouse (item 10's flagship scenario).
        return_wizard = self.env['cm.material.return.wizard'].create({
            'stage_id': foundation_stage.id,
            'line_ids': [Command.create({
                'boq_line_id': line.id, 'quantity': line.returnable_qty,
            }) for line in foundation_stage.boq_line_ids if line.returnable_qty > 0],
        })
        if return_wizard.line_ids:
            return_wizard.action_return()

        # --- Walls stage: in progress, partially executed ---
        walls_stage.action_start()
        self._po_vendor_bill(walls_stage, products['bricks'], 20000, 9.2, vendor, days_ago=15)
        self._po_vendor_bill(walls_stage, products['cement'], 200, 405.0, vendor, days_ago=14)
        self._issue_material(walls_stage, products['bricks'], 15000)
        self._issue_material(walls_stage, products['cement'], 150)
        self._certify_measurement(walls_stage, products['bricks'], 14500)

        # --- Finishing/Electrical stages: not started yet (future targets, for schedule panel) ---

        # --- Daily Progress Report (approval mixin flagship example) ---
        dpr = self.env['cm.dpr'].create({
            'project_id': foundation_project.id,
            'stage_id': foundation_stage.id,
            'date': today - timedelta(days=32),
            'narrative': 'Foundation excavation and PCC laid for the north wing. Steel binding 60% complete.',
            'material_consumed': 'Cement 25 bags, TMT Steel 400kg',
            'equipment_used': 'Concrete mixer, vibrator',
        })
        gang = self.env['cm.labour.gang'].create({
            'name': 'Ramasamy Mason Gang', 'category': 'mason', 'standard_hour_rate': 55.0,
        })
        self.env['cm.labour.attendance'].create({
            'project_id': foundation_project.id, 'stage_id': foundation_stage.id,
            'date': today - timedelta(days=32), 'gang_id': gang.id, 'count_present': 8,
        })
        dpr.action_submit()
        dpr.action_verify()
        dpr.action_approve()

        # --- Milestone billing: advance already implicit, one milestone invoiced+paid, retention pending ---
        advance = self.env['cm.milestone.bill'].create({
            'sale_order_id': order.id, 'sequence': 1, 'name': 'Advance Payment',
            'milestone_type': 'advance', 'amount_type': 'percent', 'percentage': 10.0,
        })
        milestone1 = self.env['cm.milestone.bill'].create({
            'sale_order_id': order.id, 'sequence': 2, 'name': 'Milestone 1 - Foundation Complete',
            'milestone_type': 'milestone', 'amount_type': 'percent', 'percentage': 35.0,
        })
        self.env['cm.milestone.bill'].create({
            'sale_order_id': order.id, 'sequence': 3, 'name': 'Milestone 2 - Walls & Roofing Complete',
            'milestone_type': 'milestone', 'amount_type': 'percent', 'percentage': 65.0,
        })
        self.env['cm.milestone.bill'].create({
            'sale_order_id': order.id, 'sequence': 4, 'name': 'Retention Release',
            'milestone_type': 'retention', 'amount_type': 'percent', 'percentage': 10.0,
            'release_date': today + timedelta(days=200),
        })
        for bill in (advance, milestone1):
            action = bill.action_create_invoice()
            invoice = self.env['account.move'].browse(action['res_id'])
            invoice.invoice_date = today - timedelta(days=25)
            invoice.action_post()
            register = self.env['account.payment.register'].with_context(
                active_model='account.move', active_ids=invoice.ids,
            ).create({})
            register._create_payments()

        # --- Variation Order: client asks for an extra covered car porch (new sub-project) ---
        vo = self.env['cm.variation.order'].create({
            'sale_order_id': order.id,
            'reason': 'Client requested an additional covered car porch after seeing the foundation layout.',
            'line_ids': [Command.create({
                'product_id': products['cement'].id,
                'name': 'Covered Car Porch - Additional Scope',
                'quantity': 1,
                'price_unit': 120000.0,
            })],
        })
        vo.action_approve()

        porch_project = master.subproject_ids.filtered(lambda p: p.variation_order_id == vo)
        if porch_project:
            self.env['cm.stage'].create({
                'project_id': porch_project.id,
                'name': 'Car Porch Construction',
                'budget_allocation': 100000.0,
                'target_date': today + timedelta(days=90),
            })

    # ------------------------------------------------------------------
    # Second project: a completed, closed, handed-over house with one snag
    # ------------------------------------------------------------------
    def _demo_second_project(self, vendor, products):
        client = self.env['res.partner'].create({
            'name': 'Prime Developers Pvt Ltd',
            'company_type': 'company',
            'city': 'Coimbatore',
            'state_id': self.env.ref('base.state_in_tn').id,
            'country_id': self.env.ref('base.in').id,
        })

        lead = self.env['crm.lead'].create({
            'name': 'Row House Villa - Prime Developers', 'partner_id': client.id, 'type': 'opportunity',
        })
        self.env['cm.boq.line'].create([{
            'lead_id': lead.id, 'product_id': products['cement'].id, 'quantity': 300, 'rate': 400.0,
        }])
        lead.action_create_costing()
        costing = lead.costing_id
        costing.line_ids.write({'margin_percent': 15.0})
        costing.action_confirm()
        costing.action_create_quotation()
        order = costing.sale_order_id
        order.retention_percent = 10.0
        order.dlp_months = 6
        order.order_line.unlink()
        order.write({'order_line': [Command.create({
            'product_id': products['cement'].id,
            'name': 'Villa Construction - Complete',
            'product_uom_qty': 1,
            'product_uom_id': products['cement'].uom_id.id,
            'price_unit': 480000.0,
            'tax_ids': [Command.clear()],
        })]})
        order.action_confirm()

        master = order.master_project_id
        master.advance_received = 100000.0
        villa_project = master.subproject_ids[:1]

        today = fields.Date.context_today(self)
        stage = self.env['cm.stage'].create({
            'project_id': villa_project.id, 'name': 'Complete Construction',
            'budget_allocation': 400000.0, 'target_date': today - timedelta(days=100),
        })
        self.env['cm.boq.line'].create([
            {'stage_id': stage.id, 'product_id': products['cement'].id, 'quantity': 300, 'rate': 400.0},
            {'stage_id': stage.id, 'product_id': products['pipe'].id, 'quantity': 80, 'rate': 230.0},
        ])
        stage.action_start()
        self._po_vendor_bill(stage, products['cement'], 300, 400.0, vendor, days_ago=150)
        self._po_vendor_bill(stage, products['pipe'], 80, 230.0, vendor, days_ago=148)
        self._issue_material(stage, products['cement'], 300)
        self._issue_material(stage, products['pipe'], 80)
        self._certify_measurement(stage, products['cement'], 300)
        self._certify_measurement(stage, products['pipe'], 80)
        stage.action_mark_completed()
        stage.action_certify()

        advance = self.env['cm.milestone.bill'].create({
            'sale_order_id': order.id, 'sequence': 1, 'name': 'Advance Payment',
            'milestone_type': 'advance', 'amount_type': 'percent', 'percentage': 20.0,
        })
        final = self.env['cm.milestone.bill'].create({
            'sale_order_id': order.id, 'sequence': 2, 'name': 'Milestone - Full Construction Complete',
            'milestone_type': 'milestone', 'amount_type': 'percent', 'percentage': 90.0,
        })
        self.env['cm.milestone.bill'].create({
            'sale_order_id': order.id, 'sequence': 3, 'name': 'Retention Release',
            'milestone_type': 'retention', 'amount_type': 'percent', 'percentage': 10.0,
            'release_date': today - timedelta(days=1),
        })
        for bill in (advance, final):
            action = bill.action_create_invoice()
            invoice = self.env['account.move'].browse(action['res_id'])
            invoice.invoice_date = today - timedelta(days=130)
            invoice.action_post()
            register = self.env['account.payment.register'].with_context(
                active_model='account.move', active_ids=invoice.ids,
            ).create({})
            register._create_payments()

        # --- Handover ---
        villa_project.write({
            'final_inspection_done': True,
            'client_signoff': True,
            'keys_handed_over': True,
            'electricity_connection_transferred': True,
            'water_connection_transferred': True,
            'warranty_documents_handed_over': True,
            'handover_date': today - timedelta(days=100),
            'warranty_months': 6,
        })
        villa_project.action_close_subproject()

        # --- Snag reported and fixed after handover (item 10's flagship scenario) ---
        snag = self.env['cm.snag.item'].create({
            'project_id': villa_project.id,
            'stage_id': stage.id,
            'description': 'Minor water leakage noticed near the bathroom wall joint.',
            'severity': 'minor',
            'reported_date': today - timedelta(days=60),
            'assigned_to': vendor.id,
            'cost_borne_by': 'company',
            'line_ids': [Command.create({
                'product_id': products['pipe'].id, 'quantity': 5, 'rate': products['pipe'].standard_price,
            })],
        })
        snag.action_start()
        snag.action_issue_rectification_material()
        snag.action_mark_fixed()
        snag.action_client_verify()
