from odoo import models, fields

class VendorAuditLog(models.Model):
    _name = 'vendor.audit.log'
    _description = 'Vendor Audit Log'
    _order = 'create_date desc'

    vendor_id = fields.Many2one(
        'res.partner',
        required=True,
        ondelete='cascade'
    )

    event_type = fields.Selection([
        ('created', 'Vendor Created'),
        ('approved', 'Vendor Approved'),
        ('blacklisted', 'Vendor Blacklisted'),
        ('deleted', 'Vendor Deleted'),
        ('state_change', 'State Change'),
        ('approval', 'Approval Action'),
    ], required=True)

    action = fields.Char()
    old_value = fields.Char()
    new_value = fields.Char()
    changed_by = fields.Many2one(
        'res.users',
        string="User",
        default=lambda self: self.env.user
    )
    note = fields.Text(string="Description")
