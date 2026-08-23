from odoo import models, fields, api, _
from datetime import timedelta
from dateutil.relativedelta import relativedelta
from .vendor_compat_utils import group_members


class VendorPerformance(models.Model):
    _name = 'vendor.performance'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Vendor Performance (Periodic)'
    _order = 'review_date desc'

    vendor_id = fields.Many2one(
        'res.partner',
        string="Vendor",
        domain=[('is_vendor', '=', True)],
        required=True,
        ondelete='cascade'
    )

    review_date = fields.Date(string="Review Date", required=True, default=fields.Date.today)
    next_review_date = fields.Date(
        string="Next Review Date",
        compute="_compute_next_review_date",
        store=True,
    )
    
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted')
    ], string="Status", default='draft', tracking=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if not res.get('vendor_id') and self.env.context.get('active_id'):
            # Double check if active_id is a partner
            active_model = self.env.context.get('active_model')
            if active_model == 'res.partner' or not active_model:
                res['vendor_id'] = self.env.context.get('active_id')
        return res

    # 🔹 Manual Rating for System B
    reliability = fields.Selection([(str(i), str(i)) for i in range(1, 6)], string="Reliability", default='5')
    compliance = fields.Selection([(str(i), str(i)) for i in range(1, 6)], string="Compliance", default='5')
    behavior = fields.Selection([(str(i), str(i)) for i in range(1, 6)], string="Behavior", default='5')
    partnership = fields.Selection([(str(i), str(i)) for i in range(1, 6)], string="Partnership", default='5')

    # Historical performance data for the same vendor (for notebook tree view)
    vendor_history_ids = fields.One2many(
        'vendor.performance',
        compute='_compute_vendor_history',
        string="Vendor Performance History"
    )

    periodic_score = fields.Float(compute="_compute_periodic_score", store=True, string="Periodic Score")
    
    @api.depends('reliability', 'compliance', 'behavior', 'partnership')
    def _compute_periodic_score(self):
        for rec in self:
            scores = [int(rec.reliability), int(rec.compliance), int(rec.behavior), int(rec.partnership)]
            rec.periodic_score = (sum(scores) / 20.0) * 100

    @api.depends('review_date')
    def _compute_next_review_date(self):
        param = self.env['ir.config_parameter'].sudo()
        frequency = int(param.get_param(
            'vendor_management.review_frequency', default='1'
        ))

        for rec in self:
            if rec.review_date:
                rec.next_review_date = rec.review_date + relativedelta(months=frequency)
            else:
                rec.next_review_date = False

    def _compute_vendor_history(self):
        """Get all performance records for the same vendor"""
        for rec in self:
            if rec.vendor_id:
                # Get all performance records for this vendor
                rec.vendor_history_ids = self.search([
                    ('vendor_id', '=', rec.vendor_id.id)
                ]).ids
            else:
                rec.vendor_history_ids = []

    def action_submit(self):
        """Submit review and update vendor review Cycle"""
        for rec in self:
            rec.state = 'submitted'
            
            # Synchronize with partner's review cycle
            if rec.vendor_id:
                rec.vendor_id.write({
                    'review_start_date': rec.review_date,
                    'last_review_date': rec.review_date
                })
        return True

    @api.model_create_multi
    def create(self, vals_list):
        return super().create(vals_list)

    @api.model
    def cron_update_performance(self):
        """Cron to check for vendors due for review and create activities."""
        vendors = self.env['res.partner'].search([
            ('is_vendor', '=', True),
            ('vendor_state', '=', 'active'),
            ('review_status', 'in', ('soon', 'expired'))
        ])
        
        for vendor in vendors:
            # Check if activity already exists to avoid duplicates
            manager_group = self.env.ref('vendor_management.group_vendor_manager')
            md_group = self.env.ref('vendor_management.group_vendor_md')
            reviewer_user_ids = group_members(manager_group).ids + group_members(md_group).ids
            existing_activity = self.env['mail.activity'].search([
                ('res_model', '=', 'res.partner'),
                ('res_id', '=', vendor.id),
                ('summary', 'ilike', 'Periodic Vendor Review Due'),
                ('user_id', 'in', reviewer_user_ids)
            ], limit=1)
            
            if not existing_activity:
                # Create activity for Manager and MD
                groups = [
                    'vendor_management.group_vendor_manager',
                    'vendor_management.group_vendor_md'
                ]
                for group_xmlid in groups:
                    group = self.env.ref(group_xmlid, raise_if_not_found=False)
                    if group:
                        for user in group_members(group):
                            vendor.activity_schedule(
                                'mail.mail_activity_data_todo',
                                summary=_('Periodic Vendor Review Due: %s', vendor.name),
                                note=_('Periodic review is due based on frequency settings.'),
                                user_id=user.id
                            )

class PerformanceOverview(models.Model):
    _name = 'vendor.performance.overview'
    _description = 'Performance Overview (Transaction-based)'
    _order = 'create_date desc'

    vendor_id = fields.Many2one('res.partner', required=True, ondelete='cascade')
    purchase_id = fields.Many2one('purchase.order', string="PO Reference")
    move_id = fields.Many2one('account.move', string="Bill Reference")
    
    quality = fields.Selection([(str(i), str(i)) for i in range(1, 6)], string="Quality", default='5')
    delivery = fields.Selection([(str(i), str(i)) for i in range(1, 6)], string="Delivery", default='5')
    service = fields.Selection([(str(i), str(i)) for i in range(1, 6)], string="Service", default='5')
    overall = fields.Selection([(str(i), str(i)) for i in range(1, 6)], string="Overall", default='5')

    performance_overview_score = fields.Float(compute="_compute_overview_score", store=True)

    @api.depends('quality', 'delivery', 'service', 'overall')
    def _compute_overview_score(self):
        for rec in self:
            scores = [int(rec.quality), int(rec.delivery), int(rec.service), int(rec.overall)]
            rec.performance_overview_score = (sum(scores) / 20.0) * 100

class VendorSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    transaction_review_threshold = fields.Float(
        string="Transaction Review Threshold",
        config_parameter='vendor_management.transaction_review_threshold',
        default=1000.0
    )
    review_frequency = fields.Integer(
        string="Review Frequency (Months)",
        default=1
    )

    def set_values(self):
        super().set_values()
        self.env['ir.config_parameter'].sudo().set_param(
            'vendor_management.review_frequency',
            self.review_frequency or 1
        )

    def get_values(self):
        res = super().get_values()
        param = self.env['ir.config_parameter'].sudo()
        res.update(
            review_frequency=int(param.get_param(
                'vendor_management.review_frequency', default='1'
            ))
        )
        return res

    risk_score_weight_missing = fields.Integer(
        string="Weight: Missing Documents",
        config_parameter='vendor_management.risk_score_weight_missing',
        default=30
    )
    risk_score_weight_expired = fields.Integer(
        string="Weight: Expired Documents",
        config_parameter='vendor_management.risk_score_weight_expired',
        default=50
    )
    risk_score_weight_rejected = fields.Integer(
        string="Weight: Rejected Documents",
        config_parameter='vendor_management.risk_score_weight_rejected',
        default=20
    )
