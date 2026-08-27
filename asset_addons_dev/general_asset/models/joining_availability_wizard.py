# -*- coding: utf-8 -*-

from odoo import models


class JoiningAvailabilityWizardGeneralAsset(models.TransientModel):
    """Tighten the availability domain to genuinely fixed assets.

    asset_management's own _available_domain() can only filter on
    monitoring_protocol - is_general_asset lives here, in general_asset, so
    asset_management cannot reference it without breaking wherever this
    module is absent.

    Overriding here rather than editing the base method keeps this module's
    field private to itself: asset_management stays correct on its own, and
    this makes the SAME check the joining-process view already applies (see
    asset_joining_domain_inherit.xml) apply to the wizard too. If these two
    domains ever drifted apart, the wizard would promise units as available
    that the assign step then refuses to offer.
    """
    _inherit = "joining.availability.wizard"

    def _available_domain(self, category):
        domain = super()._available_domain(category)
        return domain + [("is_general_asset", "=", True)]
