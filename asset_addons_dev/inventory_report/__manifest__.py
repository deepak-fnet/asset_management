{
    "name": "Inventory Report",
    "version": "19.0.1.0.0",
    "category": "Inventory/Inventory",
    "summary": "Date-range inventory movement report (Receipts / Internal Transfers / Deliveries) in XLSX or PDF",
    "description": """
Adds Inventory > Reporting > Inventory Report.

A wizard asks for:
    * From Date / To Date
    * Operation Type: Receipt, Internal Transfer or Delivery
    * (optional) Warehouse, Product and Status filters
    * Output format: XLSX or PDF

and produces a downloadable report of the matching stock moves.

Standalone: depends only on 'stock'.
""",
    "author": "",
    "license": "LGPL-3",
    "depends": ["stock"],
    "data": [
        "security/ir.model.access.csv",
        "report/inventory_report_templates.xml",
        "report/inventory_report_reports.xml",
        "wizard/inventory_report_wizard_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
