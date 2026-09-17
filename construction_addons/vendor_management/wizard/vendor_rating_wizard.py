from odoo import models, fields, api, _

class VendorRatingWizard(models.TransientModel):
    _name = 'vendor.rating.wizard'
    _description = 'Vendor Transaction Rating Wizard'

    purchase_id = fields.Many2one('purchase.order', string="Purchase Order", readonly=True)
    move_id = fields.Many2one('account.move', string="Vendor Bill", readonly=True)
    vendor_id = fields.Many2one('res.partner', string="Vendor", compute='_compute_vendor_id', readonly=True, store=True)

    @api.depends('purchase_id', 'move_id')
    def _compute_vendor_id(self):
        for rec in self:
            if rec.purchase_id:
                rec.vendor_id = rec.purchase_id.partner_id
            elif rec.move_id:
                rec.vendor_id = rec.move_id.partner_id
            else:
                rec.vendor_id = False
    
    quality = fields.Selection([
        ('1', '1 - Poor'),
        ('2', '2 - Fair'),
        ('3', '3 - Good'),
        ('4', '4 - Very Good'),
        ('5', '5 - Excellent')
    ], string="Quality of Goods/Services", default='5', required=True)
    
    delivery = fields.Selection([
        ('1', '1 - Poor'),
        ('2', '2 - Fair'),
        ('3', '3 - Good'),
        ('4', '4 - Very Good'),
        ('5', '5 - Excellent')
    ], string="Delivery Timeliness", default='5', required=True)
    
    service = fields.Selection([
        ('1', '1 - Poor'),
        ('2', '2 - Fair'),
        ('3', '3 - Good'),
        ('4', '4 - Very Good'),
        ('5', '5 - Excellent')
    ], string="Service & Communication", default='5', required=True)
    
    overall = fields.Selection([
        ('1', '1 - Poor'),
        ('2', '2 - Fair'),
        ('3', '3 - Good'),
        ('4', '4 - Very Good'),
        ('5', '5 - Excellent')
    ], string="Overall Experience", default='5', required=True)

    comment = fields.Text(string="Comments")

    def action_confirm(self):
        self.ensure_one()
        self.env['vendor.performance.overview'].create({
            'vendor_id': self.vendor_id.id,
            'purchase_id': self.purchase_id.id if self.purchase_id else False,
            'move_id': self.move_id.id if self.move_id else False,
            'quality': self.quality,
            'delivery': self.delivery,
            'service': self.service,
            'overall': self.overall,
        })
        
        # Log activity
        message = _("Vendor rated: Quality=%s, Delivery=%s, Service=%s, Overall=%s. Comment: %s") % (
            self.quality, self.delivery, self.service, self.overall, self.comment or 'None'
        )
        if self.purchase_id:
            self.purchase_id.message_post(body=message)
        if self.move_id:
            self.move_id.message_post(body=message)
            
        return {'type': 'ir.actions.act_window_close'}
