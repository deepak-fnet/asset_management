from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from dateutil.relativedelta import relativedelta
from .vendor_compat_utils import group_member_emails, has_field

class ResPartner(models.Model):
    _inherit = 'res.partner'

    @api.model
    def _get_active_vendor_domain(self):
        return [('is_vendor', '=', True), ('vendor_state', '=', 'active')]

    @api.model
    def _get_under_review_vendor_domain(self):
        return [('is_vendor', '=', True), ('vendor_state', 'in', ['submitted', 'manager_review'])]

    @api.model
    def _get_blocked_vendor_domain(self):
        return [('is_vendor', '=', True), ('vendor_state', '=', 'blocked')]

    @api.model
    def _get_total_vendor_domain(self):
        return [('is_vendor', '=', True)]

    def action_active_vendors(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Active Vendors'),
            'res_model': 'res.partner',
            'view_mode': 'list,form',
            'domain': self._get_active_vendor_domain(),
            'context': {'default_is_vendor': True, 'default_supplier_rank': 1},
        }

    def action_under_review_vendors(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Under Review Vendors'),
            'res_model': 'res.partner',
            'view_mode': 'list,form',
            'domain': self._get_under_review_vendor_domain(),
            'context': {'default_is_vendor': True, 'default_supplier_rank': 1},
        }

    def action_blocked_vendors(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Blocked Vendors'),
            'res_model': 'res.partner',
            'view_mode': 'list,form',
            'domain': self._get_blocked_vendor_domain(),
            'context': {'default_is_vendor': True, 'default_supplier_rank': 1},
        }

    @api.model
    def action_open_category_vendors(self, category_id):
        """
        Returns an action to open the vendor list filtered by category.
        Ensures perfect domain synchronization with the dashboard aggregation.
        """
        return {
            "type": "ir.actions.act_window",
            "name": _("Filtered Vendors"),
            "res_model": "res.partner",
            "view_mode": "list,form",
            "views": [
                (self.env.ref('vendor_management.view_vendor_partner_list').id, "list"),
                (self.env.ref('vendor_management.view_partner_form_vendor_enterprise').id, "form"),
            ],
            "target": "current",
            "domain": [
                ("vendor_category_ids", "in", category_id),
            ],
            "context": {'default_is_vendor': True, 'default_supplier_rank': 1},
        }

    # ---------------- DOCUMENT SUMMARY (REQUIRED FOR KANBAN) ----------------
    doc_count_total = fields.Integer(compute='_compute_doc_counts', store=False)
    doc_count_approved = fields.Integer(compute='_compute_doc_counts', store=False)
    doc_count_pending = fields.Integer(compute='_compute_doc_counts', store=False)
    doc_count_expired = fields.Integer(compute='_compute_doc_counts', store=False)

    @api.depends('document_ids', 'document_ids.state', 'document_ids.is_expired')
    def _compute_doc_counts(self):
        for rec in self:
            docs = rec.document_ids or self.env['vendor.document']
            rec.doc_count_total = len(docs)
            rec.doc_count_approved = len(docs.filtered(lambda d: d.state == 'approved'))
            rec.doc_count_pending = len(docs.filtered(lambda d: d.state == 'submitted'))
            rec.doc_count_expired = len(docs.filtered(lambda d: d.is_expired))

    # ---------------- CORE ----------------
    is_vendor = fields.Boolean(string="Is a Vendor", default=False)

    vendor_category_ids = fields.Many2many(
        'vendor.category',
        string="Vendor Categories"
    )

    document_ids = fields.One2many(
        'vendor.document',
        'vendor_id',
        string="Vendor Documents"
    )

    vendor_document_ids = fields.One2many(
        "vendor.document",
        "vendor_id",
        string="Vendor Documents (Alias)"
    )

    contract_ids = fields.One2many(
        'vendor.contract',
        'vendor_id',
        string="Vendor Contracts"
    )

    audit_log_ids = fields.One2many(
        'vendor.audit.log',
        'vendor_id',
        string="Audit & Activity Logs",
        readonly=True
    )

    performance_ids = fields.One2many(
        'vendor.performance',
        'vendor_id',
        string="Periodic Reviews",
        readonly=True
    )

    performance_overview_ids = fields.One2many(
        'vendor.performance.overview',
        'vendor_id',
        string="Transaction Ratings",
        readonly=True
    )

    performance_overview_score = fields.Float(
        compute="_compute_performance_overview_score",
        string="Performance Overview Score"
    )

    quote_ids = fields.One2many(
        'vendor.quote',
        'vendor_id',
        string="Vendor Quotations"
    )

    # Vendor Statistics (using core models only)
    rfq_count = fields.Integer(
        string="RFQ Count",
        compute="_compute_rfq_po_counts",
        help="Total RFQs in draft or sent state"
    )
    po_count = fields.Integer(
        string="PO Count",
        compute="_compute_rfq_po_counts",
        help="Total confirmed Purchase Orders"
    )

    def _compute_rfq_po_counts(self):
        for rec in self:
            if not rec.id:
                rec.rfq_count = 0
                rec.po_count = 0
                continue
            rec.rfq_count = self.env['purchase.order'].search_count([
                ('partner_id', '=', rec.id),
                ('state', 'in', ['draft', 'sent'])
            ])
            rec.po_count = self.env['purchase.order'].search_count([
                ('partner_id', '=', rec.id),
                ('state', 'in', ['purchase', 'done'])
            ])

    # Multi-Currency Totals (Inline Display)
    purchase_display = fields.Char(
        string="Total Purchased",
        compute="_compute_currency_totals",
        help="Multi-currency purchase totals formatted as a string"
    )
    paid_display = fields.Char(
        string="Total Paid",
        compute="_compute_currency_totals",
        help="Multi-currency paid totals formatted as a string"
    )

    def _compute_currency_totals(self):
        """
        Compute multi-currency totals for inline display.
        Formats as 'Currency Symbol Amount | Currency Symbol Amount'
        """
        for rec in self:
            if not rec.id:
                rec.purchase_display = "0.00"
                rec.paid_display = "0.00"
                continue

            # 1. Purchase Totals
            # Odoo 19: classic read_group() is deprecated; use _read_group()
            # with groupby + aggregates instead. currency_id comes back as
            # an already-browsed res.currency recordset.
            purchase_groups = self.env['purchase.order']._read_group(
                domain=[
                    ('partner_id', '=', rec.id),
                    ('state', 'in', ['purchase', 'done']),
                ],
                groupby=['currency_id'],
                aggregates=['amount_total:sum'],
            )

            p_parts = []
            for curr, amount in purchase_groups:
                if not curr:
                    continue
                formatted = "%s %s" % (curr.symbol, "{:,.2f}".format(amount))
                p_parts.append(formatted)
            rec.purchase_display = " | ".join(p_parts) if p_parts else "0.00"

            # 2. Paid Totals
            paid_groups = self.env['account.move']._read_group(
                domain=[
                    ('partner_id', '=', rec.id),
                    ('move_type', '=', 'in_invoice'),
                    ('state', '=', 'posted'),
                    ('payment_state', '=', 'paid')
                ],
                groupby=['currency_id'],
                aggregates=['amount_total:sum'],
            )

            a_parts = []
            for curr, amount in paid_groups:
                if not curr:
                    continue
                formatted = "%s %s" % (curr.symbol, "{:,.2f}".format(amount))
                a_parts.append(formatted)
            rec.paid_display = " | ".join(a_parts) if a_parts else "0.00"

    def action_view_rfqs(self):
        """View all RFQs for this vendor (Draft/Sent)"""
        self.ensure_one()
        return {
            'name': _('RFQs for %s', self.name),
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id), ('state', 'in', ['draft', 'sent'])],
            'context': {'default_partner_id': self.id},
        }

    def action_view_purchase_orders(self):
        """View all confirmed Purchase Orders"""
        self.ensure_one()
        return {
            'name': _('Purchase Orders for %s', self.name),
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id), ('state', 'in', ['purchase', 'done'])],
            'context': {'default_partner_id': self.id},
        }

    def action_view_purchased(self):
        """Action for Purchased inline row (Redirects to POs)"""
        return self.action_view_purchase_orders()

    def action_view_paid(self):
        """Action for Paid inline row (Redirects to Paid Bills)"""
        self.ensure_one()
        return {
            'name': _('Paid Bills for %s', self.name),
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [
                ('partner_id', '=', self.id),
                ('move_type', '=', 'in_invoice'),
                ('state', '=', 'posted'),
                ('payment_state', '=', 'paid')
            ],
            'context': {'default_partner_id': self.id, 'default_move_type': 'in_invoice'},
        }


    def _compute_performance_overview_score(self):
        for rec in self:
            if rec.performance_overview_ids:
                rec.performance_overview_score = sum(rec.performance_overview_ids.mapped('performance_overview_score')) / len(rec.performance_overview_ids)
            else:
                rec.performance_overview_score = 0.0


    # ---------------- WORKFLOW ----------------
    vendor_state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('manager_review', 'Manager Review'),
        ('active', 'Approved/Active'),
        ('blocked', 'Blocked'),
        ('rejected', 'Rejected'),
    ], default='draft', string="Vendor Status", tracking=True)

    # ---------------- COMPLIANCE & RISK ----------------
    compliance_status = fields.Selection([
        ('pending', 'Pending'),
        ('partial', 'Non-Compliant'),
        ('ok', 'Compliant'),
    ], compute='_compute_compliance_status', store=True, string="Compliance", tracking=True)

    compliance_score = fields.Float(
        compute="_compute_vendor_scores",
        store=True,
        string="Compliance Score"
    )

    performance_grade = fields.Selection(
        [
            ('A', 'A'),
            ('B', 'B'),
            ('C', 'C'),
            ('D', 'D'),
            ('F', 'F'),
        ],
        compute="_compute_latest_performance",
        store=True,
        compute_sudo=True,
        string="Performance Grade"
    )

    delivery_score = fields.Float(compute="_compute_latest_performance", store=True, compute_sudo=True, string="Delivery Score")
    dispute_score = fields.Float(compute="_compute_latest_performance", store=True, compute_sudo=True, string="Dispute Score")

    risk_score = fields.Integer(compute='_compute_risk_score', store=True, string="Risk Score", tracking=True)

    vendor_blacklisted = fields.Boolean(string="Blacklisted", default=False, tracking=True)
    vendor_blacklist_reason = fields.Text()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # A name like " Vendor 1 " reads as a different string than
            # "Vendor 1" to the DB, so the uniqueness check below would miss
            # it entirely - strip before it ever gets that far.
            if vals.get('name'):
                vals['name'] = vals['name'].strip()
        partners = super().create(vals_list)
        for partner in partners:
            # Auto-set review_start_date for vendors
            if partner.is_vendor and not partner.review_start_date:
                partner.review_start_date = fields.Date.today()

            if partner.vendor_category_ids:
                partner._create_document_checklist()

            # Trigger Portal Access for new vendors
            partner._action_trigger_portal_access()
        partners._check_pan_uniqueness()
        return partners

    def write(self, vals):
        if vals.get('name'):
            vals['name'] = vals['name'].strip()

        res = super().write(vals)

        # l10n_in_pan only exists when the l10n_in module is installed - this
        # key can only be present in vals if the field is real, so checking
        # for it here (rather than via @api.constrains, which would crash at
        # registry build time on an unknown field name) is safe either way.
        if 'l10n_in_pan' in vals:
            self._check_pan_uniqueness()

        # Prevent recursion when triggered by portal creation
        if self.env.context.get('skip_portal_trigger'):
            return res

        if 'email' in vals or 'is_vendor' in vals:
            # Unique commercials to prevent duplicate triggers in same transaction
            for commercial in self.mapped('commercial_partner_id'):
                if (
                    commercial.is_vendor
                    and commercial.email
                    and not commercial.user_ids
                ):
                    commercial.with_context(
                        skip_portal_trigger=True
                    )._action_trigger_portal_access()

        # Update vendor category relations
        if 'vendor_category_ids' in vals:
            for rec in self:
                rec._create_document_checklist()

        return res

    @api.constrains('name', 'is_vendor')
    def _check_vendor_name_unique(self):
        for partner in self:
            if not partner.is_vendor or not partner.name:
                continue
            duplicate = self.search([
                ('is_vendor', '=', True),
                ('id', '!=', partner.id),
                ('name', '=ilike', partner.name.strip()),
            ], limit=1)
            if duplicate:
                raise ValidationError(_(
                    "A vendor named \"%s\" already exists. Vendor names must be unique."
                ) % partner.name.strip())

    @api.constrains('vat')
    def _check_gst_number_unique(self):
        for partner in self:
            if not partner.vat:
                continue
            duplicate = self.search([
                ('vat', '=', partner.vat),
                ('id', '!=', partner.id),
            ], limit=1)
            if duplicate:
                raise ValidationError(_(
                    "GST number %(vat)s is already used by %(other)s."
                ) % {'vat': partner.vat, 'other': duplicate.display_name})

    def _check_pan_uniqueness(self):
        """PAN uniqueness - not an @api.constrains because l10n_in_pan only
        exists when the l10n_in module is installed, and decorating on a
        field name the model may not have would crash registry setup.
        Called explicitly from create()/write() instead, guarded there by
        checking the field is actually present in vals.
        """
        for partner in self:
            if not has_field(partner, 'l10n_in_pan') or not partner.l10n_in_pan:
                continue
            duplicate = self.search([
                ('l10n_in_pan', '=', partner.l10n_in_pan),
                ('id', '!=', partner.id),
            ], limit=1)
            if duplicate:
                raise ValidationError(_(
                    "PAN number %(pan)s is already used by %(other)s."
                ) % {'pan': partner.l10n_in_pan, 'other': duplicate.display_name})

    def _action_trigger_portal_access(self):
        """
        Idempotent helper to grant portal access to vendors.
        Strictly follows Odoo 18 patterns using portal.wizard.
        """
        # Iterate over unique commercial partners to prevent redundant triggers
        for commercial in self.mapped('commercial_partner_id'):
            # 1. Basic eligibility check
            if not commercial.is_vendor or not commercial.email:
                continue

            # 2. Normalize email for robust search
            email_login = commercial.email.strip()
            if not email_login:
                continue

            # 3. Idempotency Check A: Does this partner ALREADY have a linked user?
            if commercial.user_ids:
                continue

            # 4. Idempotency Check B: Does ANY user in the system have this login?
            # We check both exact and case-insensitive to prevent duplicate login ValidationError
            existing_user = self.env['res.users'].sudo().search([
                ('login', '=ilike', email_login)
            ], limit=1)
            
            if existing_user:
                # If user exists with same login but for a different partner, we skip safely.
                # Attempting to grant access would crash Odoo with Duplicate Login error.
                continue

            # 5. Safe Creation via Portal Wizard
            try:
                portal_wizard = self.env['portal.wizard'].sudo().with_context(
                    active_ids=[commercial.id],
                    active_model='res.partner',
                    skip_portal_trigger=True  # Avoid recursion
                ).create({})
                
                # Double-check if the wizard actually populated a user to grant access to
                if portal_wizard.user_ids:
                    portal_wizard.user_ids.action_grant_access()
            except Exception:
                # Silent failure to prevent blocking the parent transaction (partner write/create)
                # This handles race conditions or other internal Odoo constraints
                pass

    @api.constrains('vendor_state')
    def _check_blocked_vendor(self):
        for rec in self:
            if rec.vendor_state == 'blocked':
                # Optional: archive or just let the purchase.order check handle it
                pass

    @api.onchange('vendor_category_ids')
    def _onchange_vendor_category_ids(self):
        if self.vendor_category_ids:
            self.is_vendor = True

    def _create_document_checklist(self):
        """Auto-create placeholders for required documents."""
        for rec in self:
            if not rec.id or not rec.vendor_category_ids:
                continue
            
            required_types = rec._get_required_document_types()

            for doc_type in required_types:
                existing = rec.document_ids.filtered(lambda d: d.doc_type_id == doc_type)
                if not existing:
                    self.env['vendor.document'].sudo().create({
                        'vendor_id': rec.id,
                        'doc_type_id': doc_type.id,
                        'state': 'draft'
                    })

    # ---------------- COMPUTE METHODS ----------------

    def _get_required_document_types(self):
        """Get all mandatory document types for a vendor based on category configuration."""
        self.ensure_one()
        
        # Get all mandatory document configurations from all categories
        mandatory_configs = self.vendor_category_ids.mapped('mandatory_document_line_ids').filtered(
            lambda l: l.is_mandatory
        )
        
        # Return unique document types
        return mandatory_configs.mapped('document_type_id')

    def _get_required_document_configs(self):
        """Helper to get all mandatory document configurations for a vendor."""
        self.ensure_one()
        return self.vendor_category_ids.mapped('mandatory_document_line_ids')

    @api.depends(
        'document_ids.state',
        'document_ids.expiry_date',
        'document_ids.is_expired',
        'vendor_category_ids.mandatory_document_line_ids.is_mandatory'
    )
    def _compute_compliance_status(self):
        """
        STRICT SUBSET COMPLIANCE STATUS:
        Depends ONLY on mandatory documents defined in the vendor category.
        Rules: All mandatory type IDs must be present in the set of approved & valid docs.
        Extra documents are explicitly ignored.
        """
        for rec in self:
            if not rec.vendor_category_ids:
                rec.compliance_status = 'ok'
                continue

            # 1. Identify Mandatory Type IDs
            mandatory_types = rec._get_required_document_types()
            mandatory_ids = set(mandatory_types.ids)
            
            if not mandatory_ids:
                rec.compliance_status = 'ok'
                continue

            # 2. Identify Valid (Approved & Non-Expired) Type IDs
            valid_doc_types = rec.document_ids.filtered(
                lambda d: d.state == 'approved' and not d.is_expired
            ).mapped('doc_type_id')
            valid_ids = set(valid_doc_types.ids)

            # 3. Validation: (Mandatory) ⊆ (Valid)
            if mandatory_ids.issubset(valid_ids):
                rec.compliance_status = 'ok'
            else:
                # Differentiate between 'pending' and 'partial'
                # Check if all mandatory placeholders exist but some aren't approved yet
                all_doc_types = rec.document_ids.mapped('doc_type_id')
                all_ids = set(all_doc_types.ids)
                
                # If we don't even have the mandatory types uploaded (even in draft), it's definitely partial
                if not mandatory_ids.issubset(all_ids):
                    rec.compliance_status = 'partial'
                else:
                    # We have the types, but they aren't all 'approved' / valid yet
                    rec.compliance_status = 'pending'

    @api.depends('performance_ids', 'performance_ids.periodic_score')
    def _compute_latest_performance(self):
        for rec in self:
            latest = rec.performance_ids[:1]
            if latest:
                # Map periodic_score to a grade for UI compatibility
                rec.performance_grade = 'A' if latest.periodic_score >= 90 else 'B' if latest.periodic_score >= 75 else 'C' if latest.periodic_score >= 60 else 'D' if latest.periodic_score >= 40 else 'F'
                rec.delivery_score = latest.periodic_score
                rec.dispute_score = 0
            else:
                rec.performance_grade = 'F'
                rec.delivery_score = 0
                rec.dispute_score = 0

    @api.depends(
        'document_ids.state', 
        'document_ids.expiry_date',
        'document_ids.is_expired',
        'document_ids.is_expiring_soon',
        'vendor_category_ids.mandatory_document_line_ids.is_mandatory'
    )
    def _compute_vendor_scores(self):
        """
        WEIGHTED COMPLIANCE SCORE:
        - Mandatory Docs: 70% of score
        - Optional Docs: 30% of score
        - Deductions for Expiries and Pending states
        """
        for rec in self:
            if not rec.vendor_category_ids:
                rec.compliance_score = 100.0
                continue

            mandatory_types = rec._get_required_document_types()
            all_docs = rec.document_ids
            optional_docs = all_docs.filtered(lambda d: d.doc_type_id not in mandatory_types)

            # 1. Mandatory Component (70 points max)
            mandatory_points = 0.0
            if mandatory_types:
                approved_mandatory = all_docs.filtered(
                    lambda d: d.doc_type_id in mandatory_types and d.state == 'approved' and not d.is_expired
                )
                coverage = len(approved_mandatory.mapped('doc_type_id'))
                mandatory_points = (coverage / len(mandatory_types)) * 70.0
            else:
                mandatory_points = 70.0  # Full points if no requirements

            # 2. Optional Component (30 points max)
            optional_points = 0.0
            if optional_docs:
                approved_optional = optional_docs.filtered(lambda d: d.state == 'approved' and not d.is_expired)
                # If we have optional docs, score them by % approved
                optional_points = (len(approved_optional) / len(optional_docs)) * 30.0
            else:
                optional_points = 30.0  # Full points if no optional docs uploaded

            # 3. Deductions (Risk Overlays)
            deductions = 0.0
            for doc in all_docs:
                is_mandatory = doc.doc_type_id in mandatory_types
                if doc.is_expired:
                    deductions += 25.0 if is_mandatory else 10.0
                elif doc.is_expiring_soon:
                    deductions += 5.0
                elif doc.state in ('submitted', 'manager_review', 'md_approval'):
                    # Small deduction for pending items to reflect "In Progress" risk
                    deductions += 2.0

            final_score = (mandatory_points + optional_points) - deductions
            rec.compliance_score = max(0.0, min(100.0, final_score))

    @api.depends(
        'compliance_status',
        'performance_grade',
        'total_outstanding_amount',
        'vendor_blacklisted'
    )
    def _compute_risk_score(self):
        for rec in self:
            if rec.vendor_blacklisted:
                rec.risk_score = 100
                continue

            risk = 0
            # Compliance weight (50/25)
            if rec.compliance_status == 'partial':
                risk += 50
            elif rec.compliance_status == 'pending':
                risk += 25

            # Performance weight (30/15)
            if rec.performance_grade == 'F':
                risk += 30
            elif rec.performance_grade == 'C':
                risk += 15

            # Financial exposure (20)
            if rec.total_outstanding_amount > 0:
                risk += 20

            rec.risk_score = min(risk, 100)

    @api.depends('performance_ids.next_review_date', 'performance_ids.state')
    def _compute_review_status(self):
        """Compute next review due date and overall review status"""
        today = fields.Date.today()
        # Threshold for "Due Soon"
        soon_date = today + relativedelta(days=7)
        
        for rec in self:
            # Find the latest SUBMITTED performance review
            latest_review = self.env['vendor.performance'].search([
                ('vendor_id', '=', rec.id),
                ('state', '=', 'submitted')
            ], order='review_date desc', limit=1)
            
            if not latest_review:
                rec.next_review_due = False
                rec.review_status = 'pending'
                rec.review_status_indicator = 'pending'
                rec.review_due = False
            else:
                due_date = latest_review.next_review_date
                rec.next_review_due = due_date
                
                if not due_date:
                    rec.review_status = 'pending'
                    rec.review_status_indicator = 'pending'
                    rec.review_due = False
                elif today > due_date:
                    rec.review_status = 'expired'
                    rec.review_status_indicator = 'expired'
                    rec.review_due = True
                elif today >= (due_date - relativedelta(days=7)):
                    rec.review_status = 'soon'
                    rec.review_status_indicator = 'soon'
                    rec.review_due = False
                else:
                    rec.review_status = 'ok'
                    rec.review_status_indicator = 'ok'
                    rec.review_due = False


    # ---------------- REVIEW FREQUENCY TRACKING ----------------
    review_start_date = fields.Date(
        string="Review Cycle Start",
        help="Date when current review cycle started. Auto-set on vendor creation."
    )
    
    last_review_date = fields.Date(
        string="Last Review Date",
        readonly=True,
        help="The date of the last submitted performance review"
    )
    
    review_status = fields.Selection(
        [
            ('pending', 'Pending'),
            ('ok', 'OK'),
            ('soon', 'Due Soon'),
            ('expired', 'Expired')
        ],
        string="Review Status",
        compute="_compute_review_status",
        store=True,
        help="Overall status of the vendor review cycle"
    )

    next_review_due = fields.Date(
        string="Next Review Due",
        compute="_compute_review_status",
        store=True,
        help="Next scheduled review date pulling from the latest submitted review"
    )

    review_status_indicator = fields.Selection([
        ('ok', 'OK'),
        ('soon', 'Due Soon'),
        ('expired', 'Expired'),
        ('pending', 'Pending'),
    ], string="Review Status Indicator", compute="_compute_review_status", store=True)

    review_due = fields.Boolean(
        string="Review Due",
        compute="_compute_review_status",
        store=True,
        help="Flag indicating review is due or overdue"
    )

    # ---------------- WORKFLOW ACTIONS ----------------

    def action_submit_for_review(self):
        """Vendor Submits."""
        self.ensure_one()
        # Internal users can also submit as per Task 10
        if not self.document_ids:
            # We allow submission if placeholders are there but not uploaded? 
            # Requirement says "Mandatory ONLY during approval" for upload.
            # But here we check for docs. 
            pass
        
        old_state = self.vendor_state
        self.vendor_state = 'submitted'
        self._log_audit('state_change', old_value=old_state, new_value='submitted', action=_('Submitted'))

    def action_mark_reviewed(self):
        """Manager reviews."""
        self._check_group('vendor_management.group_vendor_manager')
        old_state = self.vendor_state
        self.vendor_state = 'manager_review'
        self._log_audit('state_change', old_value=old_state, new_value='manager_review', action=_('Marked Reviewed by Manager'))

    def action_approve_activate(self):
        """MD Approval & Activation."""
        self._check_group('vendor_management.group_vendor_md')
        if self.compliance_status != 'ok':
            raise UserError(_("Vendor cannot be activated until all mandatory documents are approved."))
        
        old_state = self.vendor_state
        self.vendor_state = 'active'
        self._log_audit('approval', old_value=old_state, new_value='active', action=_('Approved & Activated by MD'))
        
        # Auto-create first performance review (Draft)
        for rec in self:
            if rec.is_vendor:
                self.env['vendor.performance'].create({
                    'vendor_id': rec.id,
                    'review_date': fields.Date.today(),
                    'state': 'draft'
                })
        
        # Send Approval Email
        template = self.env.ref('vendor_management.mail_template_vendor_approved', raise_if_not_found=False)
        if template:
            md_group = self.env.ref('vendor_management.group_vendor_md')
            email_cc = group_member_emails(md_group)
            for rec in self:
                if rec.email:
                    template.send_mail(rec.id, force_send=True, email_values={'email_cc': email_cc})

    def action_block_vendor(self):
        self._check_group('vendor_management.group_vendor_md')
        old_state = self.vendor_state
        self.vendor_state = 'blocked'
        self._log_audit('state_change', old_value=old_state, new_value='blocked', action=_('Vendor Blocked'))

    def action_unblock_vendor(self):
        self._check_group('vendor_management.group_vendor_md')
        old_state = self.vendor_state
        self.vendor_state = 'active'
        self._log_audit('state_change', old_value=old_state, new_value='active', action=_('Vendor Unblocked'))

    def action_reject(self):
        """Generic Reject to Draft."""
        self._check_group('vendor_management.group_vendor_manager')
        old_state = self.vendor_state
        self.vendor_state = 'draft'
        self._log_audit('state_change', old_value=old_state, new_value='draft', action=_('Rejected to Draft'))


    def _log_audit(self, event_type, action=None, old_value=None, new_value=None, note=None):
        """Global Audit Log (Part 7 - Lightweight)"""
        for rec in self:
            self.env['vendor.audit.log'].sudo().create({
                'vendor_id': rec.id,
                'event_type': event_type,
                'action': action,
                'old_value': str(old_value) if old_value else False,
                'new_value': str(new_value) if new_value else False,
                'note': note,
            })


    def action_open_document_management(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Document Management: %s' % self.name,
            'res_model': 'res.partner',
            'res_id': self.id,
            'view_mode': 'form',
            'view_id': self.env.ref('vendor_management.view_partner_form_vendor_enterprise').id,
            'target': 'current',
        }

    # Vendor Statistics Actions


    def action_view_periodic_reviews(self):
        """View all periodic performance reviews for this vendor"""
        self.ensure_one()
        return {
            'name': _('Periodic Reviews: %s', self.name),
            'type': 'ir.actions.act_window',
            'res_model': 'vendor.performance',
            'view_mode': 'list,form',
            'domain': [('vendor_id', '=', self.id)],
            'context': {'default_vendor_id': self.id},
        }

    def action_open_review_wizard(self):
        """Open performance review wizard"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Performance Review',
            'res_model': 'vendor.performance.review.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_vendor_id': self.id}
        }

    # ---------------- SECURITY HELPER ----------------
    def _check_group(self, group_xmlid):
        if not self.env.user.has_group(group_xmlid) and not self.env.user.has_group('vendor_management.group_vendor_md'):
            raise UserError(_("You are not allowed to perform this action."))

    # ---------------- PORTAL ADDITIVE FIELDS ----------------
    total_purchase_orders = fields.Integer(
        string="Total Purchase Orders",
        compute="_compute_vendor_snapshot",
        store=False
    )
    total_invoices = fields.Integer(
        string="Total Invoices",
        compute="_compute_vendor_snapshot",
        store=False
    )
    total_paid_amount = fields.Monetary(
        string="Total Paid Amount",
        compute="_compute_vendor_snapshot",
        currency_field='currency_id',
        store=False
    )
    total_outstanding_amount = fields.Monetary(
        string="Outstanding Amount",
        compute="_compute_vendor_snapshot",
        currency_field='currency_id',
        store=False
    )

    @api.depends()
    def _compute_vendor_snapshot(self):
        for partner in self:
            if not partner.id:
                partner.total_purchase_orders = 0
                partner.total_invoices = 0
                partner.total_paid_amount = 0
                partner.total_outstanding_amount = 0
                continue

            po_count = self.env['purchase.order'].sudo().search_count([
                ('partner_id', '=', partner.id),
                ('state', 'in', ['purchase', 'done'])
            ])

            invoices = self.env['account.move'].sudo().search([
                ('partner_id', '=', partner.id),
                ('move_type', '=', 'in_invoice'),
                ('state', '=', 'posted')
            ])

            total_paid = sum(
                invoices.filtered(lambda inv: inv.payment_state == 'paid').mapped('amount_total')
            )

            total_outstanding = sum(
                invoices.filtered(lambda inv: inv.payment_state != 'paid').mapped('amount_residual')
            )

            partner.total_purchase_orders = po_count
            partner.total_invoices = len(invoices)
            partner.total_paid_amount = total_paid
            partner.total_outstanding_amount = total_outstanding
