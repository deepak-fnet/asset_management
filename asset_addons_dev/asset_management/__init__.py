from . import models
from . import controllers
from . import wizard


def _cleanup_removed_fields(env, logger):
    """
    Post-load cleanup for removed Asset Governance fields.
    The pre-migration script already handled database column removal.
    This just logs the successful migration.
    """
    logger.info("✅ Asset Governance fields cleanup completed successfully")


def post_init_hook(env):
    """
    Post-installation hook to conditionally load Enterprise features.
    This runs AFTER the core module is installed successfully.
    """
    import logging
    _logger = logging.getLogger(__name__)

    _logger.info("=" * 70)
    _logger.info("🚀 Asset Management: Running post-installation hook")
    _logger.info("=" * 70)

    _cleanup_removed_fields(env, _logger)

    # Auto-create network discovery config and run first scan
    try:
        env['network.discovery.service']._auto_setup_on_startup()
        _logger.info("✅ Network discovery auto-setup completed")
    except Exception as e:
        _logger.warning(f"⚠️ Network discovery setup skipped: {e}")

    _logger.info("✅ Asset Management module loaded successfully")

    _logger.info("=" * 70)
    _logger.info("✅ Asset Management: Post-installation completed successfully")
    _logger.info("=" * 70)