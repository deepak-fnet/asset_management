from odoo import models, fields, api
from odoo.exceptions import ValidationError

class AntivirusKscConfig(models.Model):
    """
    Singleton model for Kaspersky Security Center (KSC) configuration.
    """
    _name = "antivirus.ksc.config"
    _description = "Kaspersky Security Center Configuration"

    name = fields.Char(string="Name", default="KSC Configuration", readonly=True)
    ksc_url = fields.Char(string="KSC Server URL", required=True, help="e.g. http://192.168.105.145:5000")
    username = fields.Char(string="Username", required=True)
    password = fields.Char(string="Password", required=True)
    package_name = fields.Char(string="Deployment Package Name", default="Kaspersky Endpoint Security 12")
    verify_ssl = fields.Boolean(string="Verify SSL Certificate", default=False)

    @api.model
    def get_config(self):
        """Retrieve the singleton configuration record, creating it if necessary."""
        config = self.search([], limit=1)
        if not config:
            config = self.create({
                'ksc_url': 'http://192.168.105.145:5000',
                'username': 'admin',
                'password': 'admin',
            })
        return config

    @api.constrains('ksc_url')
    def _check_ksc_url(self):
        for record in self:
            if record.ksc_url and not record.ksc_url.startswith(('http://', 'https://')):
                raise ValidationError("KSC URL must start with http:// or https://")

    def action_test_connection(self):
        """Test connection to the configured KSC API dynamically."""
        self.ensure_one()
        from .ksc_service import KasperskySecurityCenterService
        
        if not self.ksc_url:
             raise ValidationError("Please provide a KSC Server URL.")

        svc = KasperskySecurityCenterService(
            base_url=self.ksc_url,
            username=self.username,
            password=self.password,
            verify_ssl=self.verify_ssl
        )
        
        try:
            result = svc.test_connection()
            if result.get('ok'):
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'KSC Connection Test',
                        'message': '✅ Connection to Kaspersky Security Center successful!',
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'KSC Connection Test',
                        'message': f"❌ Connection failed: {result.get('message', 'No response')}",
                        'type': 'danger',
                        'sticky': True,
                    }
                }
        except Exception as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'KSC Connection Error',
                    'message': f"❌ An error occurred: {str(e)}",
                    'type': 'danger',
                    'sticky': True,
                }
            }
