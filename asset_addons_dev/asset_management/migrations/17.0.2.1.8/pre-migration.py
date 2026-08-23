import logging
_logger = logging.getLogger(__name__)

def migrate(cr, version):
    cr.execute("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='asset_software_deployment')")
    if not cr.fetchone()[0]:
        return
    cr.execute("ALTER TABLE asset_software_deployment DROP COLUMN IF EXISTS device_id")
    _logger.info('pre-migration 19.0.2.1.8: dropped device_id column for rebuild')
