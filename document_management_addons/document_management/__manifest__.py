# -*- coding: utf-8 -*-
{
    'name': 'Document Management',
    'version': '19.0.1.0.0',
    'category': 'Productivity/Documents',
    'summary': 'Create, upload, control access, submit, release and revise documents with an approved, audit-trailed revision workflow.',
    'description': """
Document Management
====================
A document lifecycle module built around one flow:

    Create Document -> Upload -> Grant User Access -> Submit -> Release -> Revision

- Create a document record and upload one or more files against it.
- Grant per-user View / Upload / Download access on each document.
- Submit, then Release the document to make it the live/official version
  (locked against direct file edits from that point on).
- Raise a Revision Request on a released document to add a new file,
  replace an existing one, or delete one - staged as a change set with a
  cutoff date, reviewed by a dedicated Approver, and only applied to the
  live document automatically once that cutoff date arrives.
- Every applied revision is logged (who, when, why) in a full audit
  trail, with superseded files archived rather than deleted.
- Files are opened through a secure, watermarked, screenshot-guarded
  viewer instead of a raw download link.
""",
    'author': 'Axles India',
    'website': 'https://www.axlesindia.com',
    'license': 'LGPL-3',
    'depends': ['base', 'mail'],
    'data': [
        'security/document_security.xml',
        'security/ir.model.access.csv',
        'data/sequence_data.xml',
        'data/document_revision_cron.xml',
        'views/document_management_line_views.xml',
        'views/document_management_revision_request_views.xml',
        'views/document_management_views.xml',
        'views/document_menus.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
