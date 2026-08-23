"""
Migration 19.0.2.1.7 - Post-migration
Reassign every asset.asset asset_code based on platform/type:
  Camera   → CCTV-XXXXX   (asset_type = 'camera')
  Windows  → WIN-XXXXX    (platform = 'windows' OR os_name ILIKE '%windows%')
  Linux    → LNX-XXXXX    (platform = 'linux'   OR os_name ILIKE any linux variant)
  macOS    → MAC-XXXXX    (platform = 'macos'   OR os_name ILIKE '%mac%')
  Others   → AST-XXXXX
"""
import logging

_logger = logging.getLogger(__name__)


def _next_code(cr, seq_code):
    """Pull next value from ir.sequence using a raw SQL UPDATE (safe, atomic)."""
    cr.execute(
        """
        UPDATE ir_sequence
           SET number_next = number_next + number_increment
         WHERE code = %s
         RETURNING
           LPAD(CAST((number_next - number_increment) AS TEXT), padding::int, '0'),
           COALESCE(prefix, ''),
           COALESCE(suffix, '')
        """,
        (seq_code,)
    )
    row = cr.fetchone()
    if row:
        padded, prefix, suffix = row
        return f"{prefix}{padded}{suffix}"
    return None


LINUX_OS_PATTERNS = (
    'ubuntu', 'debian', 'linux', 'redhat', 'centos',
    'fedora', 'mint', 'arch', 'manjaro', 'opensuse',
    'kali', 'alpine', 'suse', 'rhel', 'oracle',
)


def _classify_sql():
    """Return (sequence_code, WHERE_clause) groups in priority order."""
    linux_like = " OR ".join(
        f"LOWER(COALESCE(os_name,'')) LIKE '%{v}%'" for v in LINUX_OS_PATTERNS
    )
    return [
        (
            "asset.asset.cctv",
            "asset_type = 'camera'",
        ),
        (
            "asset.asset.windows",
            "asset_type != 'camera' AND "
            "(platform = 'windows' OR LOWER(COALESCE(os_name,'')) LIKE '%windows%')",
        ),
        (
            "asset.asset.linux",
            f"asset_type != 'camera' AND platform != 'windows' AND "
            f"LOWER(COALESCE(os_name,'')) NOT LIKE '%windows%' AND "
            f"(platform = 'linux' OR ({linux_like}))",
        ),
        (
            "asset.asset.macos",
            "asset_type != 'camera' AND platform != 'windows' AND platform != 'linux' AND "
            "LOWER(COALESCE(os_name,'')) NOT LIKE '%windows%' AND "
            f"NOT ({linux_like}) AND "
            "(platform = 'macos' OR LOWER(COALESCE(os_name,'')) LIKE '%mac%' OR LOWER(COALESCE(os_name,'')) LIKE '%darwin%')",
        ),
    ]


def migrate(cr, version):
    _logger.info("Migration 19.0.2.1.7: reassigning asset codes by type (with os_name fallback)...")

    processed_ids = []
    reassigned = 0

    for seq_code, where in _classify_sql():
        extra = ""
        params = []
        if processed_ids:
            extra = " AND id NOT IN %s"
            params = [tuple(processed_ids)]

        cr.execute(f"SELECT id FROM asset_asset WHERE ({where}){extra}", params or [])
        ids = [r[0] for r in cr.fetchall()]

        for asset_id in ids:
            new_code = _next_code(cr, seq_code)
            if new_code:
                cr.execute(
                    "UPDATE asset_asset SET asset_code = %s WHERE id = %s",
                    (new_code, asset_id)
                )
                processed_ids.append(asset_id)
                reassigned += 1

    # Fallback: everything not yet processed → AST-
    if processed_ids:
        cr.execute(
            "SELECT id FROM asset_asset WHERE id NOT IN %s",
            (tuple(processed_ids),)
        )
    else:
        cr.execute("SELECT id FROM asset_asset")

    for (asset_id,) in cr.fetchall():
        new_code = _next_code(cr, 'asset.asset.sequence')
        if new_code:
            cr.execute(
                "UPDATE asset_asset SET asset_code = %s WHERE id = %s",
                (new_code, asset_id)
            )
            reassigned += 1

    _logger.info("Migration 19.0.2.1.7: reassigned %d asset code(s).", reassigned)
