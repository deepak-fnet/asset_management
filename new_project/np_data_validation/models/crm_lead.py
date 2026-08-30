from odoo import api, models
from odoo.exceptions import ValidationError

from .np_mixin import check_not_past


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    @api.constrains('date_deadline')
    def _np_check_deadline(self):
        for lead in self:
            if lead.active and not lead.date_closed:
                check_not_past(lead, 'date_deadline', "Expected Closing")

    @api.constrains('expected_revenue')
    def _np_check_amounts(self):
        # The 0..100 range of `probability` is already enforced by a SQL check
        # constraint in `crm`, so it is not repeated here.
        for lead in self:
            if lead.expected_revenue < 0:
                raise ValidationError("Expected revenue cannot be negative.")

    @api.constrains('email_from', 'phone', 'partner_id', 'type')
    def _np_check_contact(self):
        for lead in self:
            if lead.type != 'opportunity':
                continue
            if not (lead.email_from or lead.phone or lead.partner_id):
                raise ValidationError(
                    "An opportunity needs at least a customer, an email or a "
                    "phone number: '%s'." % lead.name)

    def unlink(self):
        for lead in self:
            if lead.stage_id.is_won:
                raise ValidationError(
                    "Won opportunity '%s' cannot be deleted." % lead.name)
        return super().unlink()
