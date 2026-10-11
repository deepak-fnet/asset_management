"""Issue Type became configurable (repair.issue.type): point existing
repairs at the configured type matching their old fixed value."""


def migrate(cr, version):
    cr.execute("""
        UPDATE repair_management r
           SET issue_type_id = t.id
          FROM repair_issue_type t
         WHERE r.issue_type_id IS NULL AND t.behaviour = r.issue_type
           AND t.id = (SELECT min(id) FROM repair_issue_type WHERE behaviour = r.issue_type)
    """)
