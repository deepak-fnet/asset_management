# Approval history now shows the level as its approval role ("Level - 1",
# "CFO") instead of the raw sequence number - fill it in for existing events.
def migrate(cr, version):
    cr.execute("""
        UPDATE purchase_approval_event e
           SET level_name = r.name
          FROM purchase_order o
          JOIN purchase_approval_config_line l ON l.config_id = o.approval_config_id
          JOIN purchase_approval_role r ON r.id = l.role_id
         WHERE e.order_id = o.id AND e.level = l.sequence AND e.level_name IS NULL
    """)
