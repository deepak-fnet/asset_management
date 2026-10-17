from odoo import models, fields, api


class SSHRejectWizard(models.TransientModel):
    _name = 'ssh.reject.wizard'
    _description = 'SSH Access Reject Wizard'

    request_id = fields.Many2one('ssh.access.request', required=True)
    reason = fields.Text(string='Rejection Reason', required=True)

    def action_reject(self):
        """Reject the access request"""
        self.ensure_one()
        self.request_id.write({
            'state': 'rejected',
            'rejection_reason': self.reason,
            'approver_id': self.env.user.id,
            'approval_date': fields.Datetime.now(),
        })

        # Notify user
        template = self.env.ref('ssh_terminal.email_template_access_rejected', raise_if_not_found=False)
        if template:
            template.send_mail(self.request_id.id, force_send=True)

        return {'type': 'ir.actions.act_window_close'}