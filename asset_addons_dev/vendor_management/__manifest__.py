{
    'name': 'Vendor Management',
    'version': '19.0.1.5.1',  # Price Comparison dashboard (overview, vendor cards with trend, on-time + rating charts, price history); RFQ split: Confirm Order creates one purchase order per awarded vendor (RFQ -> 'Ordered'); child orders single-vendor with Upload Bill; bill approval Stores Approve -> Finance Confirm on the bill itself (vendor.bill + multi-vendor picking split removed); no duplicates in portal/Price Comparison/report; comparison: per-vendor Total row, Selected total, Lowest tags, order currency, refined layout; comparison top bar lists vendor names only (no checkboxes); bid stage: Vendor Bids tab + manual Enter/Submit Bid, Bid Received (manual, or automatic once every vendor bids), approval only after bids received + all products awarded; Purchase role ACLs on vendor.quote
    'category': 'Services',
    'summary': 'Vendor Management System',
    'author': 'Prasanna',
    'license': 'LGPL-3',

    'depends': [
        'base',
        'mail',
        'portal',
        'website',
        'contacts',
        'account',
        'web',
        'purchase',
        'stock',
        'purchase_stock',
        'sales_team',
        # Confirm Order's visibility is gated on approval_status/approver_ids
        # (see views/purchase_order_views.xml) - a deliberate integration
        # between the two flows, not an accidental coupling.
        'purchase_approval',
    ],

    'data': [
        # ================= SECURITY =================
        'security/vendor_groups.xml',
        'security/ir.model.access.csv',
        'security/vendor_partner_rule.xml',
        'security/vendor_document_rules.xml',
        'security/portal_attachment_rule.xml',
        'security/vendor_quote_portal_rules.xml',

        # ================= VIEWS =================
        'views/res_partner_vendor_views.xml',
        'views/res_partner_vendor_stats.xml',
        'views/vendor_category_views.xml',
        'views/vendor_category_action.xml',
        'views/vendor_contract_views.xml',
        'views/vendor_document_views.xml',
        'views/vendor_document_type_views.xml',
        'views/vendor_document_request_views.xml',
        'data/vendor_document_type_data.xml',
        'views/vendor_dashboard_views.xml',
        'views/vendor_dashboard_action.xml',
        'views/vendor_actions.xml',
        'views/vendor_audit_log_views.xml',
        'views/vendor_quote_views.xml',
        'views/purchase_order_views.xml',
        'views/account_move_views.xml',
        'views/res_config_settings_views.xml',
        'views/vendor_performance_views.xml',
        'views/res_partner_review_alert_views.xml',
        'views/vendor_performance_action.xml',
        'wizard/vendor_rating_wizard_view.xml',
        'wizard/vendor_performance_review_wizard_view.xml',

        # ================= PORTAL =================
        'views/portal/portal_vendor_requests.xml',
        'views/portal/vendor_portal_templates.xml',
        'views/portal/portal_vendor_documents.xml',


        # ================= DATA =================
        'data/vendor_category_data.xml',
        'data/vendor_dashboard_data.xml',
        'data/mail_templates.xml',
        'data/mail_template_vendor_selected.xml',
        'data/vendor_document_reminder_cron.xml',
        'data/vendor_document_cron.xml',
        'data/vendor_compliance_cron.xml',
        'data/vendor_performance_cron.xml',
        'data/vendor_document_request_sequence.xml',
        'data/purchase_order_sequence.xml',

        # ================= MENUS =================
        'views/vendor_menu.xml',
    ],

    'demo': [],

    'assets': {
        'web.assets_backend': [
            'vendor_management/static/src/scss/vendor_performance.scss',
            'vendor_management/static/src/scss/vendor_partner_form.scss',
            'vendor_management/static/src/css/vendor_management.scss',
            'vendor_management/static/src/js/vendor_dashboard.js',
            'vendor_management/static/src/xml/vendor_dashboard.xml',
            'vendor_management/static/src/js/vendor_comparison_grid.js',
            'vendor_management/static/src/xml/vendor_comparison_grid.xml',
            'vendor_management/static/src/scss/price_comparison_dashboard.scss',
            'vendor_management/static/src/js/price_comparison_dashboard.js',
            'vendor_management/static/src/xml/price_comparison_dashboard.xml',
        ],
        'web.assets_frontend': [
            'vendor_management/static/src/scss/vendor_portal.scss',
        ],
    },
}
