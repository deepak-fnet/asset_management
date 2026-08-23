from odoo import http
from odoo.http import request

class VendorRFQPortal(http.Controller):

    @http.route(['/my/vendor/rfq/<int:order_id>'], type='http', auth='user', website=True)
    def portal_my_vendor_rfq(self, order_id, **post):
        order = request.env['purchase.order'].sudo().browse(order_id)
        partner = request.env.user.partner_id
        
        if not order.exists() or not (order.partner_id == partner or partner.id in order.vendor_ids.ids):
            return request.redirect('/my/vendor')

        quote = request.env['vendor.quote'].sudo().search([
            ('rfq_id', '=', order.id),
            ('vendor_id', '=', partner.id)
        ], limit=1)

        if not quote and order.state in ['draft', 'sent']:
            quote = request.env['vendor.quote'].sudo().create({
                'rfq_id': order.id,
                'vendor_id': partner.id,
                'state': 'draft',
            })
            for line in order.order_line:
                request.env['vendor.quote.line'].sudo().create({
                    'quote_id': quote.id,
                    'product_id': line.product_id.id,
                    'product_qty': line.product_qty,
                    'uom_id': line.product_uom_id.id,
                    'internal_price': line.price_unit,  # Pass internal price for vendor to see
                })

        if request.httprequest.method == 'POST':
            if quote:
                for line in quote.line_ids:
                    price = post.get('vendor_price_%s' % line.id)
                    delivery = post.get('delivery_%s' % line.id)
                    bulk_qty = post.get('bulk_qty_%s' % line.id)
                    bulk_price = post.get('bulk_price_%s' % line.id)
                    note = post.get('note_%s' % line.id)
                    
                    vals = {}
                    if price:
                        vals['vendor_price'] = float(price)
                    if delivery:
                        vals['delivery_date'] = delivery
                    if bulk_qty:
                        vals['bulk_qty'] = float(bulk_qty)
                    if bulk_price:
                        vals['bulk_price'] = float(bulk_price)
                    if note:
                        vals['vendor_notes'] = note
                    
                    if vals:
                        line.write(vals)
                
                quote.write({
                    'note': post.get('note'),
                    'state': 'submitted'
                })
                if order.comparison_state == 'sent':
                    order.sudo().write({'comparison_state': 'received'})

            return request.redirect('/my/vendor/rfq/%s?success=1' % order_id)

        return request.render('vendor_management.portal_vendor_rfq_edit', {
            'order': order,
            'quote': quote,
            'success': post.get('success'),
        })
