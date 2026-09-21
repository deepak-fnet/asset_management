# -*- coding: utf-8 -*-
{
    'name': 'Construction Project Management',
    'version': '19.0.1.0.0',
    'category': 'Services/Project',
    'summary': 'CRM lead to project closure: master project, sub-projects, stage-wise BOQ, procurement, store, progress, certification and payments.',
    'description': """
Construction Project Management
================================
End-to-end construction project flow on top of CRM, Project and Purchase:

* CRM lead -> Estimation BOQ -> Sale Costing (cost + margin per item) -> Quotation
* Confirming the Quotation creates the Master Project and one Sub-Project per
  quotation line (project.project), each with its own budget
* Sub-Project split into configurable Stages (Basement, Walls, Roofing, ...)
* Stage-wise Bill of Quantities (BOQ), fetched from the lead's estimation BOQ
* Site raises a material/labour request; purchase team plans procurement, RFQs multiple
  vendors and confirms Purchase Orders (native purchase.requisition)
* Real stock receipt into WH/Stock and partial Material Issue to site (internal transfers)
* Vendor bills and registered payments (account.move/account.payment) rolled up per stage,
  sub-project and lead: Purchased, Billed, Pending Bill, Payments Made, Profit/Loss vs Budget
* Sub-project closure checklist; Master Project closes once all sub-projects are closed
""",
    'author': 'Kevin-Nelthropp',
    'license': 'LGPL-3',
    'depends': [
        'crm',
        'sale_crm',
        'project',
        'project_account',
        'project_stock',
        'account',
        'purchase',
        'project_purchase',
        'purchase_stock',
        'purchase_requisition',
        'vendor_management',
        'vendor_comparison',
    ],
    'data': [
        'security/construction_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'views/cm_master_project_views.xml',
        'views/cm_costing_views.xml',
        'views/cm_stage_views.xml',
        'views/project_project_views.xml',
        'views/crm_lead_views.xml',
        'views/purchase_order_views.xml',
        'views/cm_ncr_views.xml',
        'views/cm_safety_incident_views.xml',
        'views/cm_labour_views.xml',
        'views/cm_dpr_views.xml',
        'views/cm_measurement_views.xml',
        'views/cm_budget_views.xml',
        'views/sale_order_views.xml',
        'views/purchase_requisition_views.xml',
        'views/stock_picking_views.xml',
        'views/account_move_views.xml',
        'views/menus.xml',
        'views/cm_dashboard_action.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'construction_management/static/src/dashboard/*',
            'construction_management/static/src/flowchart/*',
        ],
    },
    'application': True,
    'installable': True,
}
