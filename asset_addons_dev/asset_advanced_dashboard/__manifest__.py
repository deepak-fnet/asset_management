{
    'name': 'Asset Advanced Dashboard',
    'version': '19.0.1.0.0',
    'summary': 'Professional White-Theme Asset Analytics Dashboard with KPIs, Charts, and Insights',
    'description': """
Asset Advanced Dashboard Module
===============================

A comprehensive analytics dashboard for asset management featuring:

- High-Level KPI Summary Cards
- Asset Status & Lifecycle Analytics
- Department-wise & Location-wise Distribution Charts
- Asset Age Analysis Dashboard
- Physical Verification Support Widgets
- Risk & Health Indicators
- Export to PDF, Excel, CSV
- Mobile Responsive Design
""",
    'author': 'Futurenet Technologies',
    'category': 'Accounting/Assets',
    'depends': ['asset', 'web', 'hr'],
    'data': [
        'security/ir.model.access.csv',
        'views/dashboard_action.xml',
        'views/menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'asset_advanced_dashboard/static/src/css/dashboard.css',
            'asset_advanced_dashboard/static/src/js/dashboard_component.js',
            'asset_advanced_dashboard/static/src/xml/dashboard_template.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
