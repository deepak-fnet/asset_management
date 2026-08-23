from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from datetime import timedelta, date
import re
from .vendor_compat_utils import group_member_emails, has_field


class VendorDocument(models.Model):
    _name = 'vendor.document'
    _description = 'Vendor Document'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'expiry_date asc'
    _rec_name = 'display_name'

    vendor_id = fields.Many2one(
        'res.partner',
        string="Vendor",
        domain="[('is_vendor','=',True)]",
        required=True,
        tracking=True,
        ondelete='cascade'
    )

    vendor_name = fields.Char(related='vendor_id.name', store=True, index=True)

    doc_type_id = fields.Many2one(
        'vendor.document.type',
        string="Document Type",
        required=True,
        tracking=True,
        index=True
    )

    document_number = fields.Char(tracking=True, index=True)
    expiry_date = fields.Date(tracking=True, index=True)
    
    @api.onchange('document_number')
    def _onchange_uppercase_document_number(self):
        """Feature 5: Auto-uppercase document numbers (PAN, GST, etc.)"""
        if self.document_number:
            self.document_number = self.document_number.upper()

    # Computed field for UI visibility/requirement of expiry based on category config
    requires_expiry_for_vendor = fields.Boolean(
        compute='_compute_requires_expiry_for_vendor',
        string="Requires Expiry (Computed)",
        store=False,
        help="Whether this document requires expiry based on vendor's category configuration"
    )

    @api.depends('vendor_id.vendor_category_ids.mandatory_document_line_ids', 'doc_type_id')
    def _compute_requires_expiry_for_vendor(self):
        """Compute whether expiry is required based on vendor's category configuration."""
        for rec in self:
            rec.requires_expiry_for_vendor = rec._get_requires_expiry()

    def _get_requires_expiry(self):
        """Get requires_expiry setting for this document from vendor's category."""
        self.ensure_one()
        if not self.vendor_id or not self.doc_type_id:
            return False  # Default to NOT requiring expiry if vendor/doc type not set
        
        # Find the category-document configuration
        for category in self.vendor_id.vendor_category_ids:
            config = category.mandatory_document_line_ids.filtered(
                lambda l: l.document_type_id == self.doc_type_id
            )
            if config:
                return config[0].requires_expiry
        
        # If not found in category config, default to False (not required)
        return False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('vendor_id'):
                raise ValidationError(_("Vendor is required for documents"))
            # Auto-submit if attachment is provided during creation
            if vals.get('attachment') and vals.get('state', 'draft') == 'draft':
                vals['state'] = 'submitted'
        res = super().create(vals_list)
        for rec in res:
            rec.vendor_id._compute_compliance_status()
            rec.vendor_id._compute_risk_score()
            # Only sync if partner has a real database ID (avoid NewId issues)
            if rec.vendor_id and isinstance(rec.vendor_id.id, int):
                rec._sync_partner_document_fields()
        return res

    def write(self, vals):
        # Auto-submit if attachment is uploaded
        if vals.get('attachment') and vals.get('state', 'draft') == 'draft':
            vals['state'] = 'submitted'

        res = super().write(vals)
        
        # Trigger sync and business logic
        for rec in self:
            rec._sync_partner_document_fields()
            if any(f in vals for f in ['state', 'expiry_date', 'document_number', 'doc_type_id', 'attachment']):
                rec.vendor_id._compute_compliance_status()
                rec.vendor_id._compute_risk_score()
        
        return res

    def unlink(self):
        vendors = self.mapped('vendor_id')
        res = super().unlink()
        for vendor in vendors:
            vendor._compute_compliance_status()
            vendor._compute_risk_score()
            # Post-delete sync: check remaining documents
            self._sync_after_delete(vendor)
        return res

    def _sync_partner_document_fields(self):
        """Production-safe sync from documents to partner fields (GST/PAN)."""
        import logging
        _logger = logging.getLogger(__name__)
        
        for record in self:
            partner = record.vendor_id
            
            # CRITICAL: Skip if partner not yet saved (NewId)
            if not partner or not isinstance(partner.id, int):
                continue

            doc_code = record.doc_type_id.code
            if doc_code not in ('GST', 'PAN'):
                continue
            
            _logger.info("SYNC CALLED for partner %s (Doc: %s)", partner.id, doc_code)

            # Find latest valid document of same type for the partner
            valid_docs = self.env['vendor.document'].search([
                ('vendor_id', '=', partner.id),
                ('doc_type_id.code', '=', doc_code),
                ('document_number', '!=', False),
                ('state', '=', 'approved')
            ], order='create_date desc, id desc', limit=1)

            new_value = valid_docs.document_number if valid_docs else False

            # Professional safeguard: only write if changed
            if doc_code == 'GST':
                if partner.vat != new_value:
                    partner.sudo().write({'vat': new_value})
                    _logger.info("SYNC SUCCESS: Partner %s GST updated", partner.id)
            elif doc_code == 'PAN':
                # l10n_in_pan only exists when the l10n_in (India) localization
                # module is installed. Guard so this module works without it.
                if has_field(partner, 'l10n_in_pan') and partner.l10n_in_pan != new_value:
                    partner.sudo().write({'l10n_in_pan': new_value})
                    _logger.info("SYNC SUCCESS: Partner %s PAN updated", partner.id)

    def _sync_after_delete(self, partner):
        """Trigger sync for a partner after a document is deleted."""
        # CRITICAL: Skip if partner not yet saved (NewId)
        if not partner or not isinstance(partner.id, int):
            return
        
        # Call the sync logic for this partner using an empty recordset
        self.browse()._sync_partner_document_sync_for_all_types(partner)

    def _sync_partner_document_sync_for_all_types(self, partner):
        """Helper to sync both GST and PAN for a partner."""
        # Sync GST
        gst_docs = self.env['vendor.document'].search([
            ('vendor_id', '=', partner.id),
            ('doc_type_id.code', '=', 'GST'),
            ('document_number', '!=', False),
            ('state', '=', 'approved')
        ], order='create_date desc, id desc', limit=1)
        new_gst = gst_docs.document_number if gst_docs else False
        if partner.vat != new_gst:
            partner.sudo().write({'vat': new_gst})

        # Sync PAN (Using l10n_in_pan, only if l10n_in is installed)
        pan_docs = self.env['vendor.document'].search([
            ('vendor_id', '=', partner.id),
            ('doc_type_id.code', '=', 'PAN'),
            ('document_number', '!=', False),
            ('state', '=', 'approved')
        ], order='create_date desc, id desc', limit=1)
        new_pan = pan_docs.document_number if pan_docs else False
        if has_field(partner, 'l10n_in_pan') and partner.l10n_in_pan != new_pan:
            partner.sudo().write({'l10n_in_pan': new_pan})

    is_expired = fields.Boolean(compute='_compute_is_expired', store=True, index=True)
    is_expiring_soon = fields.Boolean(compute='_compute_is_expiring_soon', store=True, index=True)

    attachment = fields.Binary(string="Document File", attachment=True)
    filename = fields.Char(string="File Name", tracking=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('manager_review', 'Manager Review'),
        ('md_approval', 'MD Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('expired', 'Expired'),
    ], default='draft', tracking=True, index=True)

    display_name = fields.Char(compute='_compute_display_name', store=True, index=True)

    @api.constrains('document_number', 'state')
    def _check_doc_number(self):
        """Document number is mandatory only when not in draft state"""
        for r in self:
            # Allow empty document_number for draft documents (placeholders)
            if r.state != 'draft' and not r.document_number:
                raise ValidationError("Document number is mandatory before submission.")


    @api.constrains('document_number', 'doc_type_id')
    def _check_number_validation(self):
        for rec in self:
            if rec.document_number and rec.doc_type_id.validation_regex:
                if not re.match(rec.doc_type_id.validation_regex, rec.document_number):
                    raise ValidationError(_("Invalid %s format. Please check the document number.", rec.doc_type_id.name))

    @api.constrains('expiry_date', 'doc_type_id', 'vendor_id', 'state')
    def _check_expiry_required(self):
        """Check expiry based on vendor category configuration - only for non-draft documents"""
        for rec in self:
            # Skip validation for draft documents (placeholders during vendor creation)
            if rec.state == 'draft':
                continue
            if rec._get_requires_expiry() and not rec.expiry_date:
                raise ValidationError(
                    _("Expiry date is required for %s in this vendor category.", 
                      rec.doc_type_id.name)
                )
    
    @api.constrains('expiry_date', 'doc_type_id', 'vendor_id', 'state')
    def _check_expired_document(self):
        """Block expired document upload only if expiry is required - skip draft documents"""
        for rec in self:
            # Skip validation for draft documents (placeholders during vendor creation)
            if rec.state == 'draft':
                continue
            # Only check expiry if this document type requires it in vendor's category
            if rec._get_requires_expiry() and rec.expiry_date:
                if rec.expiry_date < fields.Date.today():
                    raise ValidationError(
                        _("Cannot upload document with expired date. Document %s expired on %s.") 
                        % (rec.doc_type_id.name, rec.expiry_date)
                    )

    @api.depends('vendor_name', 'doc_type_id.name', 'document_number')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = " - ".join(filter(None, [
                rec.vendor_name,
                rec.doc_type_id.name if rec.doc_type_id else None,
                rec.document_number
            ]))

    @api.model
    def _update_expired_documents(self):
        """Called by cron to refresh expired flags - optimized for scalability."""
        today = fields.Date.today()
        
        # Batch 1: Find documents with expiry dates that need checking
        # We process only documents with expiry_date set and state=approved
        docs_with_expiry = self.search([
            ('expiry_date', '!=', False),
            ('state', '=', 'approved')
        ])
        
        # Filter to only those that require expiry in their category
        # This is unavoidable Python filtering due to relational nature
        docs_requiring_expiry = docs_with_expiry.filtered(lambda d: d._get_requires_expiry())
        
        # Batch process: compute flags
        docs_requiring_expiry._compute_is_expired()
        docs_requiring_expiry._compute_is_expiring_soon()
        
        # Batch 2: Find and mark expired documents
        expired_docs = self.search([
            ('id', 'in', docs_requiring_expiry.ids),
            ('is_expired', '=', True),
            ('state', '=', 'approved')
        ])
        
        if expired_docs:
            expired_docs.action_mark_expired()

    def action_mark_expired(self):
        self.write({'state': 'expired'})
        # Trigger compliance recompute on vendors
        self.mapped('vendor_id')._compute_compliance_status()
        
        # Send email to Vendor and MD
        template = self.env.ref('vendor_management.mail_template_doc_expired', raise_if_not_found=False)
        if template:
            md_group = self.env.ref('vendor_management.group_vendor_md')
            email_cc = group_member_emails(md_group)
            
            for rec in self:
                if rec.vendor_id.email:
                    template.send_mail(rec.id, force_send=True, email_values={'email_cc': email_cc})

    def action_send_expiry_reminder(self):
        """Send email reminders for documents expiring soon."""
        template = self.env.ref(
            'vendor_management.mail_template_doc_expiry_reminder', 
            raise_if_not_found=False
        )
        if not template:
            return
            
        # Filter to documents that require expiry and are expiring soon
        expiring_docs = self.search([
            ('is_expiring_soon', '=', True),
            ('state', '=', 'approved'),
            ('vendor_id.email', '!=', False)
        ]).filtered(lambda d: d._get_requires_expiry())
        
        for doc in expiring_docs:
            template.send_mail(doc.id, force_send=True)

    @api.depends('expiry_date', 'doc_type_id', 'vendor_id.vendor_category_ids')
    def _compute_is_expired(self):
        today = date.today()
        for rec in self:
            # Only compute expiry if required for this vendor's category
            if rec._get_requires_expiry() and rec.expiry_date:
                rec.is_expired = rec.expiry_date < today
            else:
                rec.is_expired = False

    @api.depends('expiry_date', 'doc_type_id', 'vendor_id.vendor_category_ids')
    def _compute_is_expiring_soon(self):
        today = date.today()
        soon = today + timedelta(days=7)
        for rec in self:
            # Only compute expiring soon if required for this vendor's category
            if rec._get_requires_expiry() and rec.expiry_date:
                rec.is_expiring_soon = today <= rec.expiry_date <= soon
            else:
                rec.is_expiring_soon = False

    def action_submit(self):
        self._check_mandatory_fields()
        self.state = 'submitted'

    def _check_mandatory_fields(self):
        for rec in self:
            if not rec.document_number or not rec.attachment:
                raise UserError(_("Document number and attachment are mandatory before submission."))
            
            # Only check expiry if required for this vendor's category
            if rec._get_requires_expiry() and not rec.expiry_date:
                raise UserError(_("Expiry date is required for %s in this vendor category.", rec.doc_type_id.name))

    def action_manager_review(self):
        """Vendor User/Manager moves to review."""
        self._check_group('vendor_management.group_vendor_user')
        self._check_mandatory_fields()
        self.state = 'manager_review'

    def action_md_approval(self):
        """Manager moves to MD approval."""
        self._check_group('vendor_management.group_vendor_manager')
        self.state = 'md_approval'

    def action_approve(self):
        """MD final approval."""
        self._check_group('vendor_management.group_vendor_md')
        self.write({'state': 'approved'})

    def action_reject(self):
        """Manager/MD can reject."""
        self._check_group('vendor_management.group_vendor_manager')
        self.write({'state': 'rejected'})

    def _check_group(self, group_xmlid):
        if not self.env.user.has_group(group_xmlid) and not self.env.user.has_group('vendor_management.group_vendor_manager') and not self.env.user.has_group('vendor_management.group_vendor_md'):
            raise UserError(_("You are not allowed to perform this action."))
