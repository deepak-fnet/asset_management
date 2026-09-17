from odoo import models, fields, api

class VendorActivityLog(models.Model):
    _name = 'vendor.activity.log'
    _description = 'Vendor Detailed Activity Log'
    _order = 'create_date desc'

    vendor_id = fields.Many2one('res.partner', string="Vendor", ondelete='cascade', index=True)
    user_id = fields.Many2one('res.users', string="User", default=lambda self: self.env.user)
    action = fields.Char(string="Action")
    description = fields.Text(string="Description")
    res_model = fields.Char(string="Related Model")
    res_id = fields.Integer(string="Related ID")
    
    # Track state changes specifically
    old_state = fields.Char(string="Old State")
    new_state = fields.Char(string="New State")
