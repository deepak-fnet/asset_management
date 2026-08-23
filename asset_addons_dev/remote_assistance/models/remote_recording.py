# -*- coding: utf-8 -*-
from odoo import fields, models, _


class RemoteAssistanceRecording(models.Model):
    _name = "remote.assistance.recording"
    _description = "Remote Assistance Recording"
    _order = "create_date desc"

    name = fields.Char(required=True)
    session_id = fields.Many2one(
        "remote.assistance.session", string="Session", required=True,
        ondelete="cascade", index=True)
    partner_id = fields.Many2one(
        related="session_id.partner_id", store=True, string="Customer")
    attachment_id = fields.Many2one(
        "ir.attachment", string="Video", required=True, ondelete="cascade")
    file_size = fields.Integer(
        related="attachment_id.file_size", string="Size (bytes)", store=True)
    duration = fields.Float(string="Duration (s)")
    recorded_by = fields.Many2one(
        "res.users", string="Recorded By",
        default=lambda self: self.env.user)
    company_id = fields.Many2one(
        related="session_id.company_id", store=True)

    def action_view(self):
        """Open the video inline in a new browser tab (WebM plays natively)."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=false" % self.attachment_id.id,
            "target": "new",
        }

    def action_download(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % self.attachment_id.id,
            "target": "self",
        }

    def unlink(self):
        # remove the underlying attachment (filestore) along with the record
        attachments = self.mapped("attachment_id")
        res = super().unlink()
        attachments.unlink()
        return res
