# -*- coding: utf-8 -*-
"""Shell-callable entry point for re-running the menu wiring.

post_init_hook fires on install only. When another module (general_asset,
asset_helpdesk, ...) is installed AFTER asset_workspace, its menus never get
wired, and the only documented remedy would be reinstalling this module - not
acceptable on a live database. This exposes the same routine as a method that
can be called from the shell or a server action.
"""

from odoo import models


class IrModuleModule(models.Model):
    _inherit = "ir.module.module"

    def _workspace_rewire_menus(self):
        from ..hooks import rewire_menus
        return rewire_menus(self.env)
