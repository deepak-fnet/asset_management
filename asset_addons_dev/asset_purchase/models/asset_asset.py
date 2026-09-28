# -*- coding: utf-8 -*-

from odoo import fields, models


class AssetAsset(models.Model):
    _inherit = "asset.asset"

    # The real vendor bill, not just a free-text reference. invoice_ref
    # (asset_management's own Char field) already gets a best-effort value
    # at receipt time from the PO's bill_reference - this is the actual
    # linked account.move, set later by account_move.py once a real bill
    # posts against this asset's purchase order + product. Asset creation
    # happens at RECEIPT time (see stock_move.py), which normally precedes
    # billing, so this cannot be filled in at creation - it is always a
    # follow-up link.
    invoice_move_id = fields.Many2one(
        "account.move", string="Vendor Bill", readonly=True, copy=False,
        help="The posted vendor bill this asset was invoiced on, once one "
             "exists. Open it to see/download the actual invoice document.")
    invoice_move_count = fields.Integer(
        string="Vendor Bill Count", compute="_compute_invoice_move_count")

    # The invoice copy itself (PDF/scan), pulled straight from the vendor
    # bill's own attachment panel - message_main_attachment_id is the same
    # field Odoo's native bill form uses for its OCR/attachment preview, so
    # whatever the Purchase User attached on Upload Bill shows up here with
    # no extra bookkeeping on this model.
    invoice_attachment_id = fields.Many2one(
        "ir.attachment", string="Invoice Attachment",
        related="invoice_move_id.message_main_attachment_id", readonly=True)
    invoice_attachment_datas = fields.Binary(
        related="invoice_attachment_id.datas", string="Invoice Copy", readonly=True)
    invoice_attachment_name = fields.Char(
        related="invoice_attachment_id.name", string="Invoice Filename", readonly=True)

    def _compute_invoice_move_count(self):
        for asset in self:
            asset.invoice_move_count = 1 if asset.invoice_move_id else 0

    def action_view_invoice(self):
        """Open the linked vendor bill on its own standard form - the
        invoice document itself lives there (message_main_attachment_id /
        the OCR panel), not duplicated onto this record."""
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "account.action_move_in_invoice_type")
        action["res_id"] = self.invoice_move_id.id
        action["view_mode"] = "form"
        action["views"] = [(False, "form")]
        return action
