# -*- coding: utf-8 -*-
{
    'name': "Expense Extended",
    'summary': "Travel expense claims with trip lines, grade limits, advances and a dashboard",

    'description': """
Travel expense claims: trip lines with grade-based eligible amounts, employee
advances adjusted against expenses, HOD approval, accountant / paid steps,
reminder mails and an expense dashboard. Ported from Odoo 17, where the
approval ran on expense reports (hr.expense.sheet); in Odoo 19 it runs on the
expense itself.
    """,
    'author': "Futurenet",
    'category': 'Human Resources/Expenses',
    'version': '19.0.1.0.0',
    'license': 'LGPL-3',

    'depends': ['base', 'hr_expense', 'account', 'mail'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/data.xml',
        'data/expense_cron.xml',
        'wizard/advance_adjust_wizard_views.xml',
        'views/hr_expenses_views.xml',
        'views/advance_payment_views.xml',
        'views/hr_expense_summary_view.xml',
        'views/expense_grade.xml',
        'views/hr_expense_dashboard_views.xml',
        # 'views/hr_expense_assets.xml',

    ],
    'assets': {
        'web.assets_backend': [
            'expenses_extended/static/src/js/expense_dashboard.js',
            'expenses_extended/static/src/js/expense_form.js',
            'expenses_extended/static/src/xml/expense_dashboard.xml',
            # 'expenses_extended/static/src/scss/expense_dashboard.scss',
            "expenses_extended/static/src/css/expense_form.css",
        ]
    },

}
