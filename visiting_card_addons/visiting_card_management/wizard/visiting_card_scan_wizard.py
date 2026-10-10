from odoo import fields, models
from odoo.exceptions import UserError


class VisitingCardScanWizard(models.TransientModel):
    _name = 'visiting.card.scan.wizard'
    _description = 'Scan Visiting Card'

    image_front = fields.Binary(string='Front Side', required=True)
    image_back = fields.Binary(string='Back Side')
    meeting_date = fields.Date(string='Meeting Date', default=fields.Date.context_today)
    notes = fields.Text(string='Meeting Notes')

    def action_scan(self):
        """Save the card and extract its details; the contact is created later from the card form."""
        self.ensure_one()
        if not self.image_front:
            raise UserError('Please attach a photo of the card before scanning.')

        card = self.env['visiting.card'].create({
            'image_front': self.image_front,
            'image_back': self.image_back,
            'meeting_date': self.meeting_date,
            'notes': self.notes,
        })

        try:
            card.action_extract_data()
            title, notif_type = 'Card Scanned', 'success'
            message = f"{card.name}: details extracted. Please review them, then click Create Contact."
        except UserError as exc:
            title, notif_type = 'Card Saved, Extraction Failed', 'warning'
            message = f"{card.name}: {exc}\nPlease fill in the details manually."

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': notif_type,
                'sticky': False,
                'next': {
                    'type': 'ir.actions.act_window',
                    'res_model': 'visiting.card',
                    'view_mode': 'form',
                    'views': [[False, 'form']],
                    'res_id': card.id,
                    'target': 'current',
                },
            },
        }
