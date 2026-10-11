# -*- coding: utf-8 -*-
"""Mail the requester when a temporary asset is lent and taken back, so
both show up in the ticket conversation."""
from markupsafe import Markup

from odoo import _, models


def _tickets_for_repair(repair):
    return repair.helpdesk_ticket_ids.filtered(
        lambda t: t.state not in ('solved', 'replaced', 'cancelled'))


class AssetReplaceLaptopWizard(models.TransientModel):
    _inherit = 'asset.replace.laptop.wizard'

    def action_replace(self):
        res = super().action_replace()
        process = self.assign_process_id
        for ticket in _tickets_for_repair(process.repair_ticket_id):
            ticket._notify_requester(
                _("Temporary asset issued: %s", ticket.ticket_number),
                Markup('<p>Dear %s,</p><p>While <b>%s</b> is under repair, a temporary asset '
                       '<b>%s</b> has been issued to you (from %s).</p>'
                       '<p>Please return it when your own asset is back.</p>') % (
                    ticket._requester_name(), process.asset_id.display_name,
                    self.replacement_asset_id.display_name, self.assignment_date))
        return res


class RepairManagementStandbyMail(models.Model):
    _inherit = 'repair.management'

    def action_return_standby(self):
        lent = {rec.id: self.env['asset.assign.process'].search([
            ('repair_ticket_id', '=', rec.id), ('state', '=', 'replaced')]).replacement_asset_id
            for rec in self}
        res = super().action_return_standby()
        for rec in self:
            for ticket in _tickets_for_repair(rec):
                ticket._notify_requester(
                    _("Temporary asset returned: %s", ticket.ticket_number),
                    Markup('<p>Dear %s,</p><p>We have received back the temporary asset <b>%s</b>. '
                           'Thank you.</p><p>Your asset <b>%s</b> will be handed over once the '
                           'repair is complete.</p>') % (
                        ticket._requester_name(),
                        ", ".join(lent.get(rec.id).mapped('display_name')), rec.asset_id.display_name))
        return res
