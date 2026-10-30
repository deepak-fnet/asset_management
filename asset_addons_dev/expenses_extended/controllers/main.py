from odoo import http
from odoo.http import request
import json


class ExpenseDashboardController(http.Controller):

    @http.route('/hr_expense_dashboard/data', type='jsonrpc', auth='user')
    def get_dashboard_data(self, date_range='month'):
        """API endpoint to get dashboard data"""
        dashboard = request.env['hr.expense.dashboard']
        data = dashboard.get_dashboard_data(date_range)
        return data