"""asset.list is removed: a request no longer waits in 'PO Done' for serial
numbers to be typed on an Asset List - it closes once its POs are received.

* Requests already parked in 'po_done' had everything received, so they are
  closed here before the 'po_done' state disappears from the selection.
* A serial number typed on an Asset List row only lived on that row - the
  asset itself kept the "NOSN-..." placeholder (or nothing). It is copied
  onto the asset, unless that would duplicate another asset's serial.
* The asset form extension that showed the Asset List link is deleted up
  front: otherwise reloading the asset form validates it together with that
  extension, which still names the removed asset_list_id field.
"""


def migrate(cr, version):
    cr.execute("UPDATE asset_request SET state = 'done' WHERE state = 'po_done'")
    cr.execute("SELECT to_regclass('asset_list')")
    if cr.fetchone()[0]:
        cr.execute("""
            WITH src AS (
                SELECT DISTINCT ON (btrim(l.serial_no)) l.asset_id, btrim(l.serial_no) AS serial
                  FROM asset_list l
                  JOIN asset_asset a ON a.id = l.asset_id
                 WHERE coalesce(btrim(l.serial_no), '') != ''
                   AND (a.serial_number IS NULL OR a.serial_number = ''
                        OR a.serial_number LIKE 'NOSN-%%')
                 ORDER BY btrim(l.serial_no), l.id
            )
            UPDATE asset_asset a
               SET serial_number = src.serial
              FROM src
             WHERE a.id = src.asset_id
               AND NOT EXISTS (SELECT 1 FROM asset_asset o
                                WHERE o.serial_number = src.serial AND o.id != a.id)
        """)
    cr.execute("""
        DELETE FROM ir_ui_view
         WHERE id IN (SELECT res_id FROM ir_model_data
                       WHERE module = 'asset_management' AND model = 'ir.ui.view'
                         AND name = 'view_asset_asset_form_inherit_asset_list')
    """)
    cr.execute("""
        DELETE FROM ir_model_data
         WHERE module = 'asset_management' AND model = 'ir.ui.view'
           AND name = 'view_asset_asset_form_inherit_asset_list'
    """)
