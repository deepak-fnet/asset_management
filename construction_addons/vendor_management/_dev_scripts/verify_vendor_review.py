import os
import sys

# Add Odoo path if needed
# sys.path.append('/path/to/odoo')

from odoo import api, fields
from odoo.tests.common import TransactionCase
from dateutil.relativedelta import relativedelta

class TestVendorReview(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Partner = self.env['res.partner']
        self.Performance = self.env['vendor.performance']
        
        # Create a vendor
        self.vendor = self.Partner.create({
            'name': 'Verification Vendor',
            'is_vendor': True,
            'email': 'verify@example.com',
            'vendor_state': 'draft'
        })

    def test_01_auto_create_review(self):
        """Test that a review is auto-created on approval"""
        # Set to manager_review first to satisfy workflow (though our code just triggers on action_approve_activate)
        self.vendor.vendor_state = 'manager_review'
        self.vendor.compliance_status = 'ok'
        self.vendor.action_approve_activate()
        
        reviews = self.Performance.search([('vendor_id', '=', self.vendor.id)])
        print(f"Vendor Approved. Reviews found: {len(reviews)}")
        assert len(reviews) == 1, "Should have auto-created one review"
        assert reviews[0].state == 'draft', "Auto-created review should be in draft"

    def test_02_review_submission_logic(self):
        """Test submission logic and frequency calculation"""
        review = self.Performance.create({
            'vendor_id': self.vendor.id,
            'review_date': fields.Date.today(),
            'reliability': '4',
            'compliance': '5',
            'behavior': '3',
            'partnership': '5'
        })
        
        # Check initial status on partner
        self.vendor._compute_review_status()
        print(f"Initial Status: {self.vendor.review_status}")
        assert self.vendor.review_status == 'pending', "Status should be pending with no submitted reviews"
        
        # Submit review
        review.action_submit()
        print(f"Review Submitted. Next review date: {review.next_review_date}")
        assert review.state == 'submitted', "Review status should be submitted"
        assert review.next_review_date is not None, "Next review date should be calculated"
        
        # Check partner status update
        self.vendor._compute_review_status()
        print(f"Updated Partner Status: {self.vendor.review_status}")
        assert self.vendor.review_status == 'ok', "Partner status should be OK after submission"
        assert self.vendor.next_review_due == review.next_review_date, "Next review due should match"

    def test_03_multiple_reviews(self):
        """Test multiple reviews and latest selection"""
        # Create two reviews
        r1 = self.Performance.create({
            'vendor_id': self.vendor.id,
            'review_date': fields.Date.today() - relativedelta(months=6),
            'state': 'submitted'
        })
        r1.next_review_date = r1.review_date + relativedelta(months=6) # Simulated
        
        r2 = self.Performance.create({
            'vendor_id': self.vendor.id,
            'review_date': fields.Date.today(),
            'state': 'submitted'
        })
        r2.next_review_date = r2.review_date + relativedelta(months=6) # Simulated
        
        self.vendor._compute_review_status()
        print(f"Latest Review Due: {self.vendor.next_review_due}")
        assert self.vendor.next_review_due == r2.next_review_date, "Should pick latest review"

if __name__ == "__main__":
    print("This script is a template for manual verification via Odoo shell or test runner.")
