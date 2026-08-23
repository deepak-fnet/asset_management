# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    oneit_ad_simulate = fields.Boolean(
        string='Simulation Mode',
        config_parameter='oneit_ad.simulate',
        default=True,
        help="When enabled, no LDAP calls are made: every AD operation is "
             "written to the server log and reported as successful. Use this "
             "to test the approval workflow before pointing at a real domain "
             "controller.")
    oneit_ad_server_uri = fields.Char(
        string='LDAP Server URI',
        config_parameter='oneit_ad.server_uri',
        help="e.g. ldaps://10.4.10.10:636")
    oneit_ad_use_ssl = fields.Boolean(
        string='Use SSL (LDAPS)',
        config_parameter='oneit_ad.use_ssl', default=True)
    oneit_ad_bind_dn = fields.Char(
        string='Bind DN',
        config_parameter='oneit_ad.bind_dn',
        help="Service account DN used to bind, e.g. "
             "CN=svc-odoo,OU=Service,DC=example,DC=local")
    oneit_ad_bind_password = fields.Char(
        string='Bind Password',
        config_parameter='oneit_ad.bind_password')
    oneit_ad_base_dn = fields.Char(
        string='Base DN',
        config_parameter='oneit_ad.base_dn',
        help="e.g. DC=example,DC=local")
    oneit_ad_domain = fields.Char(
        string='AD Domain',
        config_parameter='oneit_ad.domain',
        help="Used to build the userPrincipalName, e.g. example.local")
    oneit_ad_default_password = fields.Char(
        string='Default New-User Password',
        config_parameter='oneit_ad.default_password',
        default='Welcome@1234',
        help="Initial password set on newly created AD accounts.")
    oneit_ad_retention_days = fields.Integer(
        string='Deletion Delay (days)',
        config_parameter='oneit_ad.retention_days', default=30,
        help="Days after the last working day before the disabled AD account "
             "is deleted.")
    oneit_ad_max_daily_onboarding = fields.Integer(
        string='Max Onboarding Per Day',
        config_parameter='oneit_ad.max_daily_onboarding', default=10,
        help="Set to 0 to disable the cap.")

    def action_oneit_test_ad_connection(self):
        self.ensure_one()
        return self.env['oneit.ad.connector'].test_connection()
