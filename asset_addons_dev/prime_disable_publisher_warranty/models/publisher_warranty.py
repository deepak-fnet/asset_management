"""Override publisher_warranty.contract to block all external communication."""

import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class PublisherWarrantyContract(models.AbstractModel):
    _inherit = 'publisher_warranty.contract'

    @api.model
    def _get_sys_logs(self):
        """Disabled — no data is sent to any external server."""
        _logger.info("Publisher warranty phone-home disabled by prime_disable_publisher_warranty.")
        return {"messages": []}

    def update_notification(self, cron_mode=True):
        """Disabled — no external communication or notification posting."""
        return True
