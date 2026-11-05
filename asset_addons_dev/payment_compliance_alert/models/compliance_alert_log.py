# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ComplianceAlertLog(models.Model):
    _name = 'compliance.alert.log'
    _description = 'Payment Compliance Audit Log'
    _order = 'acknowledged_date desc'
    _rec_name = 'name'

    name = fields.Char(
        string='Reference', required=True, copy=False, readonly=True,
        default=lambda self: self.env['ir.sequence'].next_by_code('compliance.alert.log') or 'CAL/NEW',
    )
    move_id = fields.Many2one(
        'account.move', string='Vendor Bill', required=True, ondelete='cascade', index=True,
    )
    partner_id = fields.Many2one(
        'res.partner', string='Vendor', related='move_id.partner_id', store=True,
    )
    # Plain Char — copied at log creation time, avoids dependency on udyog_adhaar field
    udyog_adhaar = fields.Char(string='Udyog Aadhaar No.', readonly=True)
    user_id = fields.Many2one(
        'res.users', string='Acknowledged By', required=True, default=lambda self: self.env.user,
    )
    acknowledged_date = fields.Datetime(
        string='Acknowledged On', required=True, default=fields.Datetime.now,
    )
    days_overdue    = fields.Integer(string='Days Overdue at Ack.', required=True)
    amount_residual = fields.Monetary(string='Amount Pending', currency_field='currency_id')
    currency_id     = fields.Many2one('res.currency', string='Currency')
    alert_type = fields.Selection(
        selection=[('45', '45-Day MSME Alert'), ('180', '180-Day General Alert')],
        string='Alert Type', default='180', required=True,
    )
    statutory_law = fields.Selection(
        selection=[
            ('msme', 'MSME Act — Section 15'),
            ('43bh', 'Income Tax Act — Section 43B(h)'),
            ('both', 'MSME + Section 43B(h)'),
        ],
        string='Applicable Law', default='both', required=True,
    )
    notes = fields.Text(string='Notes')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'CAL/NEW') == 'CAL/NEW':
                vals['name'] = self.env['ir.sequence'].next_by_code('compliance.alert.log') or 'CAL/NEW'
        return super().create(vals_list)
