from odoo import models, fields, api, _

class VendorPerformanceReviewWizard(models.TransientModel):
    _name = 'vendor.performance.review.wizard'
    _description = 'Vendor Periodic Performance Review Wizard'

    vendor_id = fields.Many2one(
        'res.partner', 
        string="Vendor", 
        required=True, 
        readonly=True
    )
    review_date = fields.Date(
        string="Review Date", 
        default=fields.Date.today, 
        required=True
    )
    
    reliability = fields.Selection(
        [(str(i), str(i)) for i in range(1, 6)], 
        string="Reliability", 
        default='5', 
        required=True
    )
    compliance = fields.Selection(
        [(str(i), str(i)) for i in range(1, 6)], 
        string="Compliance", 
        default='5', 
        required=True
    )
    behavior = fields.Selection(
        [(str(i), str(i)) for i in range(1, 6)], 
        string="Behavior", 
        default='5', 
        required=True
    )
    partnership = fields.Selection(
        [(str(i), str(i)) for i in range(1, 6)], 
        string="Partnership", 
        default='5', 
        required=True
    )

    def action_submit_review(self):
        self.ensure_one()
        
        # Create performance record
        performance = self.env['vendor.performance'].create({
            'vendor_id': self.vendor_id.id,
            'review_date': self.review_date,
            'reliability': self.reliability,
            'compliance': self.compliance,
            'behavior': self.behavior,
            'partnership': self.partnership,
            'state': 'draft' # Explicitly draft
        })
        
        # Submit it using the model logic (calculates next_review_date, etc.)
        performance.action_submit()
        
        # Mark related activities as done
        activities = self.env['mail.activity'].search([
            ('res_model', '=', 'res.partner'),
            ('res_id', '=', self.vendor_id.id),
            ('summary', 'ilike', 'Periodic Vendor Review Due')
        ])
        activities.action_done()
        
        return {'type': 'ir.actions.act_window_close'}
