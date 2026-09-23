# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ConstructionMilestoneBill(models.Model):
    _name = 'cm.milestone.bill'
    _description = 'Construction Milestone / Advance / Retention Bill'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sale_order_id, sequence, id'

    sale_order_id = fields.Many2one('sale.order', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, help="e.g. 'Advance Payment', 'Milestone 1 - Foundation Complete', "
                                            "'Retention Release'.")
    milestone_type = fields.Selection([
        ('advance', 'Advance'),
        ('milestone', 'Milestone'),
        ('retention', 'Retention'),
    ], default='milestone', required=True)
    amount_type = fields.Selection([
        ('percent', 'Percentage'),
        ('actual', 'Actual Amount'),
    ], default='percent', required=True)
    percentage = fields.Float(
        string='Percentage (%)',
        help="For Advance/Retention: a straight % of the contract value. For Milestone: the "
             "CUMULATIVE % of the contract earned by this point (e.g. 20, then 50, then 100) - "
             "not a standalone slice. The Bill Amount is computed net of what earlier rows "
             "already claimed.")
    manual_amount = fields.Monetary(
        string='Actual Amount',
        help="Same cumulative rule as Percentage applies here for Milestone rows: enter the "
             "running total claimed so far, not an incremental slice.")
    currency_id = fields.Many2one(related='sale_order_id.currency_id')
    amount = fields.Monetary(
        string='Bill Amount', compute='_compute_amount', store=True,
        help="The actual net amount to invoice for this row. For Milestone rows this is the "
             "cumulative entitlement minus whatever the Advance/earlier Milestones ahead of it "
             "already claimed, so running bills only charge the incremental progress.")
    release_date = fields.Date(
        help="For Retention milestones: the date this becomes billable (e.g. 6 months after "
             "project completion / end of Defect Liability Period). A daily cron automatically "
             "raises the invoice once this date arrives - no manual step needed.")
    state = fields.Selection([
        ('to_invoice', 'To Invoice'),
        ('invoiced', 'Invoiced'),
    ], default='to_invoice', copy=False, tracking=True)
    invoice_id = fields.Many2one('account.move', readonly=True, copy=False)
    invoice_date = fields.Date(related='invoice_id.invoice_date', string='Invoiced On')

    @api.depends(
        'sale_order_id.amount_untaxed', 'amount_type', 'percentage', 'manual_amount',
        'milestone_type', 'sequence',
        'sale_order_id.milestone_bill_ids.milestone_type', 'sale_order_id.milestone_bill_ids.sequence',
        'sale_order_id.milestone_bill_ids.amount_type', 'sale_order_id.milestone_bill_ids.percentage',
        'sale_order_id.milestone_bill_ids.manual_amount')
    def _compute_amount(self):
        def gross(line, contract_value):
            if line.amount_type == 'actual':
                return line.manual_amount
            return contract_value * line.percentage / 100.0

        for rec in self:
            order = rec.sale_order_id
            contract_value = order.amount_untaxed if order else 0.0

            if rec.milestone_type != 'milestone':
                # Advance and Retention are each a straight, standalone % (or actual amount)
                # of the contract - not part of the cumulative milestone ladder.
                rec.amount = gross(rec, contract_value)
                continue

            # Milestone rows: percentage/manual_amount is the CUMULATIVE entitlement as of
            # this point in the project. The actual bill is that entitlement minus whatever
            # the Advance and earlier Milestones ahead of it in sequence already claimed -
            # exactly like a running-account (RA) bill.
            ladder = order.milestone_bill_ids.filtered(
                lambda l: l.milestone_type in ('advance', 'milestone')
            ).sorted(key=lambda l: (l.sequence, l.id))
            prior_gross = 0.0
            for sib in ladder:
                if sib.id == rec.id:
                    break
                prior_gross = gross(sib, contract_value)
            rec.amount = gross(rec, contract_value) - prior_gross

    @api.constrains('amount_type', 'percentage')
    def _check_percentage(self):
        for rec in self:
            if rec.amount_type == 'percent' and not (0 < rec.percentage <= 100):
                raise UserError(_("Percentage must be between 0 and 100 for '%s'.") % rec.name)

    @api.onchange('milestone_type')
    def _onchange_milestone_type(self):
        for rec in self:
            if rec.milestone_type == 'retention' and not rec.release_date:
                dlp_months = rec.sale_order_id.dlp_months or 0
                rec.release_date = fields.Date.context_today(rec) + relativedelta(months=dlp_months)

    def action_create_invoice(self):
        self.ensure_one()
        if self.state == 'invoiced':
            raise UserError(_("This milestone has already been invoiced: %s") % self.invoice_id.name)
        if self.amount <= 0:
            raise UserError(_("Nothing to invoice - the computed amount is zero or negative."))
        order = self.sale_order_id
        move = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': order.partner_id.id,
            'invoice_origin': order.name,
            'currency_id': order.currency_id.id,
            'milestone_bill_id': self.id,
            'invoice_line_ids': [(0, 0, {
                'name': self.name,
                'quantity': 1,
                'price_unit': self.amount,
                'tax_ids': [(6, 0, [])],
            })],
        })
        self.write({'invoice_id': move.id, 'state': 'invoiced'})
        return {
            'type': 'ir.actions.act_window',
            'name': _('Invoice'),
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': move.id,
        }

    def action_view_invoice(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Invoice'),
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.invoice_id.id,
        }

    def _cm_open_snags(self):
        self.ensure_one()
        master = self.sale_order_id.master_project_id
        if not master:
            return self.env['cm.snag.item']
        return self.env['cm.snag.item'].search([
            ('project_id', 'in', master.subproject_ids.ids),
            ('state', '!=', 'verified'),
        ])

    @api.model
    def _cron_create_retention_invoices(self):
        due = self.search([
            ('milestone_type', '=', 'retention'),
            ('state', '=', 'to_invoice'),
            ('release_date', '<=', fields.Date.context_today(self)),
        ])
        for rec in due:
            try:
                with self.env.cr.savepoint():
                    open_snags = rec._cm_open_snags()
                    if open_snags:
                        rec.message_post(body=_(
                            "Retention invoice held back - %(count)s unresolved Snag Item(s) still "
                            "open on this project's sub-projects: %(names)s. Resolve them and get "
                            "Client Verification before releasing retention."
                        ) % {'count': len(open_snags), 'names': ', '.join(open_snags.mapped('name'))})
                        continue
                    rec.action_create_invoice()
            except Exception:
                continue
