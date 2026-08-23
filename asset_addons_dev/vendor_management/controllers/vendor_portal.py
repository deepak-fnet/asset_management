from odoo import http
from odoo.http import request
import base64


class VendorPortal(http.Controller):

    @http.route(['/my/vendor'], type='http', auth='user', website=True)
    def portal_vendor_dashboard(self, **post):
        user = request.env.user
        partner = user.partner_id

        # Only auto-mark vendor for portal users (system does it, not user)
        if user.has_group('base.group_portal') and not partner.is_vendor:
            partner.sudo().with_context(allow_vendor_auto=True).write({'is_vendor': True})

        # Handle Profile Update
        if post.get('action') == 'save_profile':
            vals = {
                'name': post.get('name'),
                'email': post.get('email'),
                'phone': post.get('phone'),
                'street': post.get('street'),
                'city': post.get('city'),
                'website': post.get('website'),
                'vat': post.get('vat'),
                'function': post.get('function'),
                'zip': post.get('zip'),
            }

            # Odoo 19 removed res.partner.mobile - only send it if the
            # field still exists on this database (e.g. restored by a
            # community module or an older core patch level).
            if 'mobile' in partner._fields and post.get('mobile') is not None:
                vals['mobile'] = post.get('mobile')

            # Odoo 19 removed the res.partner.title field (and the
            # res.partner.title model it pointed to). Only write it if
            # it still exists.
            if 'title' in partner._fields and post.get('title'):
                vals['title'] = int(post.get('title'))

            # l10n_in_pan only exists when the l10n_in (India) localization
            # module is installed.
            if 'l10n_in_pan' in partner._fields and post.get('l10n_in_pan') is not None:
                vals['l10n_in_pan'] = post.get('l10n_in_pan')

            # Handle Many2one fields
            if post.get('state_id'):
                vals['state_id'] = int(post.get('state_id'))
            if post.get('country_id'):
                vals['country_id'] = int(post.get('country_id'))
                
            partner.sudo().write(vals)
            return request.redirect('/my/vendor?success=profile_saved')

        # Handle Workflow Submission
        if post.get('action') == 'submit_workflow':
            try:
                partner.sudo().action_submit_for_review()
                return request.redirect('/my/vendor?success=submitted')
            except Exception as e:
                return request.redirect('/my/vendor?error=%s' % str(e))

        # Get Required Documents Checklist
        documents = request.env['vendor.document'].sudo().search([
            ('vendor_id', '=', partner.id)
        ])

        # Get own Purchase Orders (RFQs and Orders)
        rfqs = request.env['purchase.order'].sudo().search([
            '|',
            ('partner_id', '=', partner.id),
            ('vendor_ids', 'in', [partner.id]),
        ])

        # Get Bills (Part 4) - Strict Ledger Alignment
        # Parity Rule: Use account.move, in_invoice only, posted only, no refunds.
        invoices = request.env['account.move'].sudo().search([
            ('partner_id', '=', partner.id),
            ('move_type', '=', 'in_invoice'),
            ('state', '=', 'posted'),
        ])

        # Multi-currency totals aggregation
        # Odoo 19: classic read_group() is deprecated; use _read_group()
        # with groupby + aggregates. currency_id is returned already
        # browsed as a res.currency recordset (or False).
        po_groups = request.env['purchase.order'].sudo()._read_group(
            [('partner_id', '=', partner.id), ('state', 'in', ['purchase', 'done'])],
            groupby=['currency_id'],
            aggregates=['amount_total:sum'],
        )
        
        # New formatted amount vs raw count
        total_purchase_count = request.env['purchase.order'].sudo().search_count([
            ('partner_id', '=', partner.id), ('state', 'in', ['purchase', 'done'])
        ])
        
        formatted_total_purchased = " | ".join([
            f"{curr.symbol} {amount:,.2f}"
            for curr, amount in po_groups if curr
        ]) or "0.00"

        # Calculate Paid and Outstanding by grouping strict invoices
        total_invoice_count = len(invoices)
        
        paid_totals = {}
        outstanding_totals = {}
        for inv in invoices:
            total = abs(inv.amount_total_signed)
            residual = abs(inv.amount_residual_signed)
            paid_amount = total - residual
            
            if paid_amount >= 0:
                curr = inv.currency_id
                paid_totals.setdefault(curr, 0.0)
                outstanding_totals.setdefault(curr, 0.0)
                
                paid_totals[curr] += paid_amount
                outstanding_totals[curr] += residual

        formatted_total_paid = " | ".join([
            f"{c.symbol} {a:,.2f}" for c, a in paid_totals.items() if a > 0
        ]) or "0.00"
        
        formatted_total_outstanding = " | ".join([
            f"{c.symbol} {a:,.2f}" for c, a in outstanding_totals.items() if a > 0
        ]) or "0.00"

        return request.render('vendor_management.portal_vendor_dashboard', {
            'vendor': partner,
            'documents': documents,
            'rfqs': rfqs,
            'bills': invoices,
            'total_purchase_count': total_purchase_count,
            'total_invoice_count': total_invoice_count,
            'formatted_total_purchased': formatted_total_purchased,
            'formatted_total_paid': formatted_total_paid,
            'formatted_total_outstanding': formatted_total_outstanding,
            'success': post.get('success'),
            'error': post.get('error'),
        })

    @http.route(['/my/vendor/documents/upload'], type='http', auth='user', website=True, methods=['POST'])
    def portal_vendor_document_upload(self, **post):
        partner = request.env.user.partner_id
        doc_id = int(post.get('doc_id'))
        file = post.get('attachment')
        
        if file and doc_id:
            doc = request.env['vendor.document'].sudo().browse(doc_id)
            if doc.vendor_id != partner:
                return request.redirect('/my/vendor?error=unauthorized')

            doc.sudo().write({
                'attachment': base64.b64encode(file.read()),
                'filename': file.filename,
                'document_number': post.get('document_number'),
                'expiry_date': post.get('expiry_date'),
                'state': 'submitted'
            })
            return request.redirect('/my/vendor?success=doc_uploaded')
        return request.redirect('/my/vendor?error=no_file')

    @http.route(['/my/vendor/documents/delete/<int:doc_id>'], type='http', auth='user', website=True)
    def portal_vendor_document_delete(self, doc_id, **kw):
        partner = request.env.user.partner_id
        doc = request.env['vendor.document'].sudo().browse(doc_id)
        if doc.vendor_id == partner:
            # Clear the attachment to keep the checklist item
            doc.sudo().write({
                'attachment': False,
                'filename': False,
                'state': 'draft'
            })
        return request.redirect('/my/vendor')

    @http.route(
        ['/my/vendor/request/new'],
        type='http',
        auth='user',
        website=True,
        methods=['GET', 'POST'],
        csrf=True
    )
    def portal_vendor_request_new(self, **post):
        partner = request.env.user.partner_id

        # Safety: only vendors can access
        if not partner.is_vendor:
            return request.redirect('/my/vendor')

        if request.httprequest.method == 'POST':
            if post.get('doc_type_id') and post.get('description'):
                request.env['vendor.document.request'].sudo().create({
                    'vendor_id': partner.id,
                    'doc_type_id': int(post.get('doc_type_id')),
                    'description': post.get('description'),
                    'requested_by_portal': True,
                })
            return request.redirect('/my/vendor')

        doc_types = request.env['vendor.document.type'].sudo().search([])

        return request.render('vendor_management.portal_vendor_requests', {
            'vendor': partner,
            'doc_types': doc_types,
        })

    @http.route(['/my/vendor/rfq/<int:rfq_id>'], type='http', auth='user', website=True)
    def portal_vendor_rfq_details(self, rfq_id, **post):
        partner = request.env.user.partner_id
        order = request.env['purchase.order'].sudo().browse(rfq_id)
        
        # Security: check if vendor is in the selected list
        if partner not in order.vendor_ids and order.partner_id != partner:
             return request.redirect('/my/vendor')

        quote = request.env['vendor.quote'].sudo().search([
            ('rfq_id', '=', rfq_id),
            ('vendor_id', '=', partner.id)
        ], limit=1)

        # Create quote if it doesn't exist but vendor is invited
        if not quote and partner in order.vendor_ids:
             quote = request.env['vendor.quote'].sudo().create({
                'rfq_id': order.id,
                'vendor_id': partner.id,
                'state': 'draft',
            })
             # Create lines
             for line in order.order_line:
                 request.env['vendor.quote.line'].sudo().create({
                     'quote_id': quote.id,
                     'product_id': line.product_id.id,
                     'product_qty': line.product_qty,
                     'uom_id': line.product_uom_id.id,
                 })

        can_edit_quote = not order.is_rfq_expired and order.state in ['draft', 'sent'] and (quote.state == 'draft' or quote.submitted_revision < order.rfq_revision)

        if request.httprequest.method == 'POST' and can_edit_quote:
            # Update Quote Lines
            for line in order.order_line:
                quote_line = quote.line_ids.filtered(lambda ql: ql.po_line_id == line)
                if not quote_line:
                    # Create missing quote line if a new PO line was added after RFQ sent
                    quote_line = request.env['vendor.quote.line'].sudo().create({
                        'quote_id': quote.id,
                        'po_line_id': line.id,
                        'internal_price': line.price_unit,
                    })
                
                price = post.get('vendor_price_%s' % line.id)
                del_date = post.get('delivery_%s' % line.id)
                b_qty = post.get('bulk_qty_%s' % line.id)
                b_price = post.get('bulk_price_%s' % line.id)
                v_note = post.get('note_%s' % line.id)
                
                quote_line.sudo().write({
                    'vendor_price': float(price) if price else 0.0,
                    'delivery_date': del_date or False,
                    'bulk_qty': float(b_qty) if b_qty else 0.0,
                    'bulk_price': float(b_price) if b_price else 0.0,
                    'vendor_notes': v_note or False,
                })
            
            # Update Quote Header
            quote.sudo().write({
                'note': post.get('note'),
                'state': 'submitted'
            })
            
            # Notify PO
            order.sudo().message_post(body="Vendor %s has submitted a quotation." % partner.name)
            
            # If all vendors submitted, move PO comparison state? (Optional)
            
            return request.redirect('/my/vendor/rfq/%s?success=1' % rfq_id)

        return request.render('vendor_management.portal_vendor_rfq_edit', {
            'order': order,
            'quote': quote,
            'can_edit_quote': can_edit_quote,
            'success': post.get('success'),
        })
