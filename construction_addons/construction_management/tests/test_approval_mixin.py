# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ConstructionTestCommon


@tagged('post_install', '-at_install')
class TestApprovalMixin(ConstructionTestCommon):
    """cm.dpr is the one document currently wired onto cm.approval.mixin - these tests exercise
    the mixin's segregation-of-duty logic through it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group_manager = cls.env.ref('construction_management.group_cm_manager')
        cls.site_engineer = cls.env['res.users'].create({
            'name': 'Test Site Engineer', 'login': 'test_site_engineer',
            'group_ids': [(6, 0, [group_manager.id])],
        })
        cls.pm = cls.env['res.users'].create({
            'name': 'Test Project Manager', 'login': 'test_pm',
            'group_ids': [(6, 0, [group_manager.id])],
        })
        cls.director = cls.env['res.users'].create({
            'name': 'Test Project Director', 'login': 'test_director',
            'group_ids': [(6, 0, [group_manager.id])],
        })

    def test_full_happy_path_with_three_different_people(self):
        dpr = self.env['cm.dpr'].with_user(self.site_engineer).create({
            'project_id': self.project.id, 'narrative': 'Test.',
        })
        dpr.action_submit()
        dpr.with_user(self.pm).action_verify()
        self.assertEqual(dpr.approval_state, 'verified')
        self.assertEqual(dpr.verified_by, self.pm)
        dpr.with_user(self.director).action_approve()
        self.assertEqual(dpr.approval_state, 'approved')
        self.assertEqual(dpr.approved_by, self.director)

    def test_creator_cannot_verify_own_record(self):
        dpr = self.env['cm.dpr'].with_user(self.site_engineer).create({
            'project_id': self.project.id, 'narrative': 'Test.',
        })
        dpr.action_submit()
        with self.assertRaises(UserError):
            dpr.with_user(self.site_engineer).action_verify()

    def test_verifier_cannot_approve_own_verification(self):
        dpr = self.env['cm.dpr'].with_user(self.site_engineer).create({
            'project_id': self.project.id, 'narrative': 'Test.',
        })
        dpr.action_submit()
        dpr.with_user(self.pm).action_verify()
        with self.assertRaises(UserError):
            dpr.with_user(self.pm).action_approve()

    def test_reject_requires_reason(self):
        dpr = self.env['cm.dpr'].with_user(self.site_engineer).create({
            'project_id': self.project.id, 'narrative': 'Test.',
        })
        dpr.action_submit()
        with self.assertRaises(UserError):
            dpr.with_user(self.pm).action_reject()
        dpr.rejection_reason = "Numbers don't match attendance log."
        dpr.with_user(self.pm).action_reject()
        self.assertEqual(dpr.approval_state, 'rejected')

    def test_approved_record_cannot_be_reset_to_draft(self):
        dpr = self.env['cm.dpr'].with_user(self.site_engineer).create({
            'project_id': self.project.id, 'narrative': 'Test.',
        })
        dpr.action_submit()
        dpr.with_user(self.pm).action_verify()
        dpr.with_user(self.director).action_approve()
        with self.assertRaises(UserError):
            dpr.action_reset_to_draft()

    def test_full_admin_has_system_group_admin_can_self_verify(self):
        admin = self.env.ref('base.user_admin')
        self.assertTrue(admin.has_group('base.group_system'))
        dpr = self.env['cm.dpr'].with_user(admin).create({
            'project_id': self.project.id, 'narrative': 'Admin bypass test.',
        })
        dpr.action_submit()
        dpr.with_user(admin).action_verify()
        self.assertEqual(dpr.approval_state, 'verified')
        dpr.with_user(admin).action_approve()
        self.assertEqual(dpr.approval_state, 'approved')
