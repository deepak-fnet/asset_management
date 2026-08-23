from odoo import http
from odoo.http import request
from datetime import date, timedelta
from collections import defaultdict

class AssetDashboardController(http.Controller):

    @http.route(['/asset/dashboard/data'], type='json', auth='user')
    def dashboard_data(self):
        env = request.env
        Asset = env['asset.addition'].sudo()
        Physical = env['physical.verification'].sudo()
        Transfer = env['asset.internal.transfer'].sudo()

        # KPIs
        total_assets = Asset.search_count([])
        total_amc = Asset.search_count([('is_amc','=',True)])
        amc_expiring = Asset.search_count([('is_amc','=',True), ('amc_end_date','<=', date.today() + timedelta(days=30))])
        verification_due = Physical.search_count([('state','=','draft')])
        pending_approvals = Asset.search_count([('state','in',['submit','dept_approved','incharge_approved','cfo_approved'])])
        warranty_expiring = Asset.search_count([('is_warranty','=',True), ('warranty_end_date','<=', date.today() + timedelta(days=30))])
        licence_expiring = Asset.search_count([('is_licence','=',True), ('licence_end_date','<=', date.today() + timedelta(days=30))])
        open_transfers = Transfer.search_count([('state','!=','done')])

        # Chart 1: Assets by Department (bar)
        dept_group = Asset.read_group([ ], ['department_id'], ['department_id'])
        dept_labels = [x['department_id'][1] if x.get('department_id') else 'Undefined' for x in dept_group]
        dept_values = [x['department_id_count'] if x.get('department_id_count') else x['__count'] for x in dept_group]

        # Chart 2: Asset Types (donut) -> using history.asset type counts or fields: we'll infer from history or flags
        # Simpler: count by asset history types
        type_group = env['history.asset'].read_group([], ['asset_type'], ['asset_type'])
        type_labels = [x['asset_type'] for x in type_group]
        type_values = [x['__count'] for x in type_group]
        # fallback if empty - compute by flags
        if not type_values:
            types = [('amc','AMC'),('licence','Licence'),('warranty','Warranty')]
            type_labels = []
            type_values = []
            for code, label in types:
                count = 0
                if code == 'amc':
                    count = Asset.search_count([('is_amc','=',True)])
                elif code == 'licence':
                    count = Asset.search_count([('is_licence','=',True)])
                elif code == 'warranty':
                    count = Asset.search_count([('is_warranty','=',True)])
                type_labels.append(label)
                type_values.append(count)

        # Chart 3: Additions by Month (line)
        # last 12 months
        from_date = date.today().replace(day=1) - timedelta(days=365)
        additions = Asset.read_group([('acquisition_date','>=', from_date)], ['acquisition_date'], ['acquisition_date:month'])
        # normalize months
        months = []
        month_counts_map = {}
        for entry in additions:
            key = entry.get('acquisition_date:month')  # format YYYY-MM
            month_counts_map[key] = entry.get('__count', 0)
        # build last 12 months labels
        import calendar
        months_list = []
        from datetime import datetime
        today = date.today()
        for i in range(11, -1, -1):
            m = (today.replace(day=1) - timedelta(days=30*i)).strftime('%Y-%m')
            # Try to compute more reliably:
        # simpler: last 12 months using month number
        labels = []
        values = []
        for i in range(11, -1, -1):
            dt = (today.replace(day=1) - timedelta(days=30*i))
            label = dt.strftime('%b %Y')
            ym = dt.strftime('%Y-%m')
            labels.append(label)
            values.append(month_counts_map.get(ym, 0))

        # Chart 4: Assets by Plant (stacked) -> group by plant & department to make stacked bars
        grp = Asset.read_group([], ['plant_id', 'department_id'], ['plant_id','department_id'])
        # Build nested dict plant -> dept -> count
        plants = []
        plant_map = {}
        dept_set = set()
        for e in grp:
            plant = e.get('plant_id')[1] if e.get('plant_id') else 'Undefined'
            dept = e.get('department_id')[1] if e.get('department_id') else 'Undefined'
            cnt = e.get('__count', 0)
            dept_set.add(dept)
            if plant not in plant_map:
                plant_map[plant] = {}
            plant_map[plant][dept] = cnt
        plants = list(plant_map.keys())
        dept_list = list(dept_set)
        # datasets per dept
        plant_datasets = []
        for dept in dept_list:
            data = [plant_map.get(p, {}).get(dept, 0) for p in plants]
            plant_datasets.append({'label': dept, 'data': data})

        # Chart 5: Pending Approvals progress -> compute completed vs pending
        completed = Asset.search_count([('state','=','done')])
        pending = total_assets - completed
        progress_pct = int((completed / total_assets * 100) if total_assets else 0)

        return {
            'kpis': {
                'total_assets': total_assets,
                'total_amc': total_amc,
                'amc_expiring': amc_expiring,
                'verification_due': verification_due,
                'pending_approvals': pending_approvals,
                'warranty_expiring': warranty_expiring,
                'licence_expiring': licence_expiring,
                'open_transfers': open_transfers,
            },
            'charts': {
                'dept': {
                    'labels': dept_labels,
                    'values': dept_values,
                },
                'types': {
                    'labels': type_labels,
                    'values': type_values,
                },
                'additions': {
                    'labels': labels,
                    'values': values,
                },
                'plants': {
                    'plants': plants,
                    'dept_list': dept_list,
                    'datasets': plant_datasets,
                },
                'progress': {
                    'completed': completed,
                    'pending': pending,
                    'progress_pct': progress_pct,
                }
            }
        }
