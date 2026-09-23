# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class ConstructionDashboard(models.AbstractModel):
    _name = 'cm.dashboard'
    _description = 'Construction Management Dashboard Data Provider'

    @api.model
    def get_mis_data(self):
        """Company-wide MIS: one view across every project - portfolio profitability,
        cash position (money in from clients vs money out to vendors) and what needs
        attention right now. Deliberately unfiltered: this is the executive roll-up the
        per-project drill-down below could never answer."""
        MasterProject = self.env['cm.master.project']
        today = fields.Date.context_today(self)

        masters = MasterProject.search([])
        totals = dict.fromkeys(
            ('contract', 'cost', 'purchased', 'pending_bill', 'vendor_paid', 'labour',
             'client_billed', 'client_received', 'outstanding', 'margin'), 0.0)
        status_counts = {}
        rows = []

        for master in masters:
            subs = master.subproject_ids
            cost = sum(subs.mapped('billed_amount'))
            contract = master.total_contract_value
            invoices = master.sale_order_id.milestone_bill_ids.invoice_id.filtered(
                lambda inv: inv.move_type == 'out_invoice' and inv.state == 'posted')
            client_billed = sum(invoices.mapped('amount_total'))
            outstanding = sum(invoices.mapped('amount_residual'))
            margin = contract - cost

            stages = subs.cm_stage_ids
            open_stages = stages.filtered(lambda s: s.state not in ('completed', 'certified'))
            overdue_stages = open_stages.filtered(lambda s: s.target_date and s.target_date < today)
            upcoming = open_stages.filtered(lambda s: s.target_date and s.target_date >= today)
            next_deadline = min(upcoming.mapped('target_date')) if upcoming else False

            row = {
                'id': master.id,
                'name': master.name,
                'lead_name': master.lead_id.name or '',
                'partner': master.partner_id.name or '',
                'state': master.state,
                'completion': round(master.overall_completion, 1),
                'subproject_count': master.subproject_count,
                'overdue_stages': len(overdue_stages),
                'next_deadline': next_deadline.isoformat() if next_deadline else False,
                'days_to_deadline': (next_deadline - today).days if next_deadline else False,
                'contract': contract,
                'cost': cost,
                'purchased': sum(subs.mapped('purchased_amount')),
                'pending_bill': sum(subs.mapped('pending_bill_amount')),
                'vendor_paid': sum(subs.mapped('payment_total')),
                'labour': sum(subs.mapped('labour_wage_total')),
                'client_billed': client_billed,
                'client_received': client_billed - outstanding,
                'outstanding': outstanding,
                'margin': margin,
                'margin_pct': round(margin / contract * 100.0, 1) if contract else 0.0,
                'billed_pct': round(client_billed / contract * 100.0, 1) if contract else 0.0,
                'over_budget': cost > contract and contract > 0,
            }
            rows.append(row)
            for key in totals:
                totals[key] += row[key]
            status_counts[master.state] = status_counts.get(master.state, 0) + 1

        totals['margin_pct'] = round(
            totals['margin'] / totals['contract'] * 100.0, 1) if totals['contract'] else 0.0
        totals['billed_pct'] = round(
            totals['client_billed'] / totals['contract'] * 100.0, 1) if totals['contract'] else 0.0
        totals['net_cash'] = totals['client_received'] - totals['vendor_paid']

        rows.sort(key=lambda r: r['margin'])

        subprojects = masters.subproject_ids
        counts = {
            'master_total': len(masters),
            'master_closed': sum(1 for m in masters if m.state == 'closed'),
            'master_active': sum(1 for m in masters if m.state == 'active'),
            'subproject_total': len(subprojects),
            'subproject_completed': len(subprojects.filtered(lambda p: p.cm_status in ('completed', 'closed'))),
            'po_count': self.env['purchase.order'].search_count([
                ('project_id', 'in', subprojects.ids), ('state', 'in', ('purchase', 'done'))]),
            'vendor_bill_count': self.env['account.move'].search_count([
                ('project_id', 'in', subprojects.ids), ('move_type', '=', 'in_invoice'), ('state', '=', 'posted')]),
            'client_invoice_count': self.env['account.move'].search_count([
                ('milestone_bill_id.sale_order_id.master_project_id', 'in', masters.ids),
                ('move_type', '=', 'out_invoice'), ('state', '=', 'posted')]),
        }

        return {
            'currency_symbol': self.env.company.currency_id.symbol or '',
            'totals': totals,
            'counts': counts,
            'projects': rows,
            'status_counts': [
                {'state': state, 'count': count} for state, count in sorted(status_counts.items())
            ],
            'alerts': self._mis_alerts(masters, rows, today),
            'top_margin': sorted(rows, key=lambda r: r['margin'], reverse=True)[:5],
            'charts': {
                'projects': {
                    'labels': [r['lead_name'] or r['name'] for r in rows],
                    'contract': [r['contract'] for r in rows],
                    'cost': [r['cost'] for r in rows],
                    'billed': [r['client_billed'] for r in rows],
                },
                'timeline': self._timeline(subprojects, masters),
                'pnl_by_stage': self._pnl_by_stage_position(masters),
                'cost_heads': self._cost_heads(subprojects),
            },
            'loss': self._loss_analysis(subprojects),
            'win': self._win_analysis(subprojects),
            'schedule': self._schedule_health(subprojects, today),
        }

    def _timeline(self, subprojects, masters):
        """Company-wide cumulative cost vs revenue, bucketed by month of the real
        bill/invoice date - the shape of the business over time, not a snapshot."""
        bills = self.env['account.move'].search([
            ('project_id', 'in', subprojects.ids), ('move_type', '=', 'in_invoice'), ('state', '=', 'posted'),
        ])
        invoices = self.env['account.move'].search([
            ('milestone_bill_id.sale_order_id.master_project_id', 'in', masters.ids),
            ('move_type', '=', 'out_invoice'), ('state', '=', 'posted'),
        ])
        buckets = {}
        for move, key in [(b, 'cost') for b in bills] + [(i, 'revenue') for i in invoices]:
            d = move.invoice_date or move.date
            month = d.strftime('%Y-%m')
            buckets.setdefault(month, {'cost': 0.0, 'revenue': 0.0})
            buckets[month][key] += move.amount_total

        if not buckets:
            return {'labels': [], 'cost': [], 'revenue': [], 'margin': []}

        labels = sorted(buckets)
        cum_c = cum_r = 0.0
        cost, revenue, margin = [], [], []
        for m in labels:
            cum_c += buckets[m]['cost']
            cum_r += buckets[m]['revenue']
            cost.append(round(cum_c, 2))
            revenue.append(round(cum_r, 2))
            margin.append(round(cum_r - cum_c, 2))
        return {'labels': labels, 'cost': cost, 'revenue': revenue, 'margin': margin}

    def _pnl_by_stage_position(self, masters, max_stages=30):
        """One line per project, showing cumulative budget variance walked stage by stage -
        aligned by STAGE POSITION (1st stage, 2nd stage, ...) rather than calendar date, so
        it works from day one even when every bill so far happens to share a date.

        Expressed as % of that project's total budget (a fixed denominator), not raw rupees:
        a ₹31 Cr project and a ₹11 L project plotted in absolute rupees would make the small
        one an invisible flat line under the big one's scale - the percentage puts every
        project's trajectory on a comparable axis regardless of size."""
        series = []
        max_len = 0
        for master in masters:
            stages = master.subproject_ids.cm_stage_ids.sorted(
                key=lambda s: (s.project_id.id, s.sequence, s.id))[:max_stages]
            if not stages:
                continue
            # Normalise against the project's FULL budget (a fixed denominator), not the
            # cumulative-so-far budget - dividing by a partial, still-tiny sum after just
            # one or two early low-budget stages caused wild spikes (a single ₹100-budget
            # stage with ₹5,000 already billed swings to -4900% on its own).
            total_budget = sum(stages.mapped('budget_allocation'))
            cum_budget = cum_actual = 0.0
            data = []
            for s in stages:
                cum_budget += s.budget_allocation
                cum_actual += s.billed_amount
                pct = (cum_budget - cum_actual) / total_budget * 100.0 if total_budget else 0.0
                data.append(round(pct, 1))
            max_len = max(max_len, len(data))
            series.append({
                'id': master.id,
                'name': master.lead_id.name or master.name,
                'data': data,
                'final': data[-1] if data else 0.0,
            })
        series.sort(key=lambda s: s['final'])
        return {
            'labels': [_('Stage %s') % (i + 1) for i in range(max_len)],
            'series': series,
        }

    def _cost_heads(self, subprojects):
        """Planned vs actual by cost head (Material / Labour / ...) across every project."""
        lines = self.env['cm.budget.line'].search([('project_id', 'in', subprojects.ids)])
        head_labels = dict(self.env['cm.budget.line']._fields['cost_head'].selection)
        agg = {}
        for line in lines:
            entry = agg.setdefault(line.cost_head, {'planned': 0.0, 'actual': 0.0})
            entry['planned'] += line.planned_amount
            entry['actual'] += line.actual_amount
        keys = sorted(agg, key=lambda k: agg[k]['planned'], reverse=True)
        return {
            'labels': [head_labels.get(k, k) for k in keys],
            'planned': [round(agg[k]['planned'], 2) for k in keys],
            'actual': [round(agg[k]['actual'], 2) for k in keys],
        }

    def _loss_analysis(self, subprojects, limit=12):
        """Where the money is actually leaking: stages whose real cost has passed the budget
        envelope allocated to them, worst overrun first."""
        rows = []
        for stage in subprojects.cm_stage_ids:
            budget = stage.budget_allocation
            actual = stage.billed_amount
            if budget <= 0 or actual <= budget:
                continue
            overrun = actual - budget
            rows.append({
                'id': stage.id,
                'stage': stage.name,
                'project': stage.project_id.name,
                'budget': budget,
                'actual': actual,
                'overrun': overrun,
                'overrun_pct': round(overrun / budget * 100.0, 1),
                'state': stage.state,
            })
        rows.sort(key=lambda r: r['overrun'], reverse=True)
        return {
            'rows': rows[:limit],
            'total_overrun': round(sum(r['overrun'] for r in rows), 2),
            'count': len(rows),
        }

    def _win_analysis(self, subprojects, limit=12):
        """The other half of the story: finished stages that came in under their budget.
        Only completed/certified work counts - a stage nobody has started yet is not a
        saving, it is just work that has not happened."""
        rows = []
        for stage in subprojects.cm_stage_ids:
            budget = stage.budget_allocation
            actual = stage.billed_amount
            if budget <= 0 or stage.state not in ('completed', 'certified') or actual >= budget:
                continue
            saving = budget - actual
            rows.append({
                'id': stage.id,
                'stage': stage.name,
                'project': stage.project_id.name,
                'budget': budget,
                'actual': actual,
                'saving': saving,
                'saving_pct': round(saving / budget * 100.0, 1),
                'state': stage.state,
            })
        rows.sort(key=lambda r: r['saving'], reverse=True)
        return {
            'rows': rows[:limit],
            'total_saving': round(sum(r['saving'] for r in rows), 2),
            'count': len(rows),
        }

    def _schedule_health(self, subprojects, today, soon_days=30):
        """Deadline picture across every site: what has slipped, what is about to - and,
        for work already finished, whether it actually landed on time."""
        all_stages = subprojects.cm_stage_ids
        overdue, due_soon, on_track, late_delivery = [], [], [], []
        done_count = on_time_count = 0

        for s in all_stages:
            finished = s.state in ('completed', 'certified')
            entry = {
                'id': s.id,
                'stage': s.name,
                'project': s.project_id.name,
                'target_date': s.target_date.isoformat() if s.target_date else False,
                'state': s.state,
                'percent_complete': round(s.percent_complete, 1),
            }
            if finished:
                done_count += 1
                delivered = s.certified_date.date() if s.certified_date else False
                if s.target_date and delivered:
                    slip = (delivered - s.target_date).days
                    if slip > 0:
                        late_delivery.append(dict(entry, days=-slip, delivered=delivered.isoformat()))
                    else:
                        on_time_count += 1
                continue

            if not s.target_date:
                continue
            days = (s.target_date - today).days
            entry['days'] = days
            if days < 0:
                overdue.append(entry)
            elif days <= soon_days:
                due_soon.append(entry)
            else:
                on_track.append(entry)

        overdue.sort(key=lambda e: e['days'])
        due_soon.sort(key=lambda e: e['days'])
        late_delivery.sort(key=lambda e: e['days'])
        return {
            'overdue': overdue[:10],
            'due_soon': due_soon[:10],
            'late_delivery': late_delivery[:10],
            'counts': {
                'overdue': len(overdue),
                'due_soon': len(due_soon),
                'on_track': len(on_track),
                'done': done_count,
                'delivered_late': len(late_delivery),
                'delivered_on_time': on_time_count,
            },
        }

    @api.model
    def search_projects(self, query, limit=12):
        """Autocomplete: match the opportunity/lead name (what people actually call the job),
        the master project number, or the client name."""
        domain = []
        if query:
            domain = ['|', '|',
                      ('lead_id.name', 'ilike', query),
                      ('name', 'ilike', query),
                      ('partner_id.name', 'ilike', query)]
        masters = self.env['cm.master.project'].search(domain, limit=limit)
        return [{
            'id': m.id,
            'name': m.name,
            'lead_name': m.lead_id.name or '',
            'partner': m.partner_id.name or '',
            'state': m.state,
        } for m in masters]

    @api.model
    def get_project_detail(self, master_id):
        """Everything about one master project: contract/sales info, every sub-project with
        its stages nested inline, P&L summary, and a P&L trend line built from the real dates
        of posted vendor bills (cost) and posted client invoices (revenue) - a construction
        S-curve, not a guess."""
        master = self.env['cm.master.project'].browse(int(master_id)).exists()
        if not master:
            return False

        order = master.sale_order_id
        subs = master.subproject_ids
        cost = sum(subs.mapped('billed_amount'))
        contract = master.total_contract_value
        client_invoices = order.milestone_bill_ids.invoice_id.filtered(
            lambda inv: inv.move_type == 'out_invoice' and inv.state == 'posted')
        client_billed = sum(client_invoices.mapped('amount_total'))
        outstanding = sum(client_invoices.mapped('amount_residual'))
        margin = contract - cost

        sales = False
        if order:
            sales = {
                'id': order.id,
                'name': order.name,
                'state': order.state,
                'amount_untaxed': order.amount_untaxed,
                'amount_total': order.amount_total,
                'invoice_status': order.invoice_status,
                'invoice_count': order.invoice_count,
                'milestone_bill_count': order.milestone_bill_count,
                'date_order': order.date_order.date().isoformat() if order.date_order else False,
            }

        subprojects = []
        for p in subs:
            subprojects.append({
                'id': p.id,
                'name': p.name,
                'project_no': p.project_no,
                'cm_status': p.cm_status,
                'completion_percent': round(p.completion_percent, 1),
                'cm_budget': p.cm_budget,
                'purchased_amount': p.purchased_amount,
                'billed_amount': p.billed_amount,
                'pending_bill_amount': p.pending_bill_amount,
                'profit_amount': p.profit_amount,
                'stages': [
                    {
                        'id': s.id,
                        'name': s.name,
                        'state': s.state,
                        'budget_allocation': s.budget_allocation,
                        'boq_total': s.boq_total,
                        'purchased_amount': s.purchased_amount,
                        'billed_amount': s.billed_amount,
                        'payment_total': s.payment_total,
                        'percent_complete': round(s.percent_complete, 1),
                        'target_date': s.target_date.isoformat() if s.target_date else False,
                    }
                    for s in p.cm_stage_ids.sorted('sequence')
                ],
            })

        today = fields.Date.context_today(self)
        all_stages = subs.cm_stage_ids

        return {
            'currency_symbol': self.env.company.currency_id.symbol or '',
            'master': {
                'id': master.id,
                'name': master.name,
                'lead_name': master.lead_id.name or '',
                'partner': master.partner_id.name or '',
                'state': master.state,
                'start_date': master.start_date.isoformat() if master.start_date else False,
                'overall_completion': round(master.overall_completion, 1),
                'advance_received': master.advance_received,
            },
            'stage_chart': {
                'labels': [s.name for s in all_stages],
                'budget': [s.budget_allocation for s in all_stages],
                'purchased': [s.purchased_amount for s in all_stages],
                'billed': [s.billed_amount for s in all_stages],
                # parallel array so the client can scope the chart to one sub-project
                # without another round trip - projects can run to hundreds of stages.
                'subproject': [s.project_id.name for s in all_stages],
                'subproject_ids': [s.project_id.id for s in all_stages],
            },
            'subproject_options': [{'id': p.id, 'name': p.name} for p in subs],
            'cost_heads': self._cost_heads(subs),
            'loss': self._loss_analysis(subs),
            'win': self._win_analysis(subs),
            'schedule': self._schedule_health(subs, today),
            'pnl': {
                'contract': contract,
                'cost': cost,
                'client_billed': client_billed,
                'client_received': client_billed - outstanding,
                'outstanding': outstanding,
                'margin': margin,
                'margin_pct': round(margin / contract * 100.0, 1) if contract else 0.0,
                'billed_pct': round(client_billed / contract * 100.0, 1) if contract else 0.0,
                'purchased': sum(subs.mapped('purchased_amount')),
                'pending_bill': sum(subs.mapped('pending_bill_amount')),
                'vendor_paid': sum(subs.mapped('payment_total')),
                'labour': sum(subs.mapped('labour_wage_total')),
            },
            'sales': sales,
            'subprojects': subprojects,
            'trend': self._project_pnl_trend(subs, client_invoices),
        }

    def _project_pnl_trend(self, subprojects, client_invoices):
        """Cumulative cost vs cumulative revenue, walked day by day across every posted
        vendor bill and client invoice touching this project - a real S-curve, not synthetic
        sample points."""
        bills = self.env['account.move'].search([
            ('project_id', 'in', subprojects.ids), ('move_type', '=', 'in_invoice'), ('state', '=', 'posted'),
        ])
        points = {}
        for b in bills:
            d = (b.invoice_date or b.date).isoformat()
            points.setdefault(d, {'cost': 0.0, 'revenue': 0.0})
            points[d]['cost'] += b.amount_total
        for inv in client_invoices:
            d = (inv.invoice_date or inv.date).isoformat()
            points.setdefault(d, {'cost': 0.0, 'revenue': 0.0})
            points[d]['revenue'] += inv.amount_total

        if not points:
            return {'labels': [], 'cost': [], 'revenue': [], 'margin': []}

        labels = sorted(points.keys())
        cum_cost = cum_revenue = 0.0
        cost_series, revenue_series, margin_series = [], [], []
        for d in labels:
            cum_cost += points[d]['cost']
            cum_revenue += points[d]['revenue']
            cost_series.append(round(cum_cost, 2))
            revenue_series.append(round(cum_revenue, 2))
            margin_series.append(round(cum_revenue - cum_cost, 2))

        return {'labels': labels, 'cost': cost_series, 'revenue': revenue_series, 'margin': margin_series}

    def _mis_alerts(self, masters, rows, today):
        """Only things a manager would actually act on today."""
        subprojects = masters.subproject_ids
        alerts = []

        over_budget = [r for r in rows if r['over_budget']]
        if over_budget:
            alerts.append({
                'key': 'over_budget',
                'icon': 'fa-exclamation-triangle',
                'level': 'danger',
                'title': 'Projects over budget',
                'count': len(over_budget),
                'detail': ', '.join(r['name'] for r in over_budget[:3]),
            })

        open_snags = self.env['cm.snag.item'].search([
            ('project_id', 'in', subprojects.ids), ('state', '!=', 'verified'),
        ])
        if open_snags:
            alerts.append({
                'key': 'snags',
                'icon': 'fa-wrench',
                'level': 'warning',
                'title': 'Open defects (blocking retention release)',
                'count': len(open_snags),
                'detail': ', '.join(open_snags.mapped('name')[:3]),
            })

        overdue_stages = self.env['cm.stage'].search([
            ('project_id', 'in', subprojects.ids),
            ('target_date', '<', today),
            ('state', 'not in', ('completed', 'certified')),
        ])
        if overdue_stages:
            alerts.append({
                'key': 'overdue',
                'icon': 'fa-clock-o',
                'level': 'danger',
                'title': 'Stages past their target date',
                'count': len(overdue_stages),
                'detail': ', '.join(overdue_stages.mapped('name')[:3]),
            })

        pending_bill_total = sum(r['pending_bill'] for r in rows)
        if pending_bill_total > 0:
            alerts.append({
                'key': 'pending_bill',
                'icon': 'fa-file-text-o',
                'level': 'info',
                'title': 'Ordered but not yet invoiced by vendors',
                'count': round(pending_bill_total),
                'is_amount': True,
                'detail': 'Purchase Orders confirmed with no vendor bill yet',
            })

        outstanding_total = sum(r['outstanding'] for r in rows)
        if outstanding_total > 0:
            alerts.append({
                'key': 'receivable',
                'icon': 'fa-inr',
                'level': 'warning',
                'title': 'Outstanding from clients',
                'count': round(outstanding_total),
                'is_amount': True,
                'detail': 'Invoices raised but not yet paid',
            })

        draft_measurements = self.env['cm.measurement'].search_count([
            ('project_id', 'in', subprojects.ids), ('state', '=', 'draft'),
        ])
        if draft_measurements:
            alerts.append({
                'key': 'measurements',
                'icon': 'fa-balance-scale',
                'level': 'info',
                'title': 'Measurements awaiting certification',
                'count': draft_measurements,
                'detail': 'Site work measured but not yet certified or rejected',
            })

        return alerts

    @api.model
    def get_flowchart_data(self, master_id=False, project_id=False, stage_id=False):
        MasterProject = self.env['cm.master.project']
        Project = self.env['project.project']
        Stage = self.env['cm.stage']

        result = {
            'masters': [{'id': m.id, 'name': m.name} for m in MasterProject.search([])],
            'master': False,
            'subprojects': [],
            'selected_project': False,
            'stages': [],
            'selected_stage': False,
            'tasks': [],
        }
        if not master_id:
            return result

        master = MasterProject.browse(int(master_id)).exists()
        if not master:
            return result
        subprojects = master.subproject_ids
        billed = sum(subprojects.mapped('billed_amount'))
        result['master'] = {
            'id': master.id, 'name': master.name, 'partner': master.partner_id.name,
            'currency_symbol': master.currency_id.symbol,
            'budget': master.total_contract_value,
            'purchased': sum(subprojects.mapped('purchased_amount')),
            'billed': billed,
            'pending_bill': sum(subprojects.mapped('pending_bill_amount')),
            'payments': sum(subprojects.mapped('payment_total')),
            'profit': master.total_contract_value - billed,
        }
        result['subprojects'] = [
            {
                'id': p.id, 'name': p.name, 'cm_status': p.cm_status,
                'budget': p.cm_budget, 'billed': p.billed_amount, 'profit': p.profit_amount,
            }
            for p in master.subproject_ids
        ]

        project = Project.browse(int(project_id)).exists() if project_id else Project.browse()
        if project and project.master_project_id.id == master.id:
            result['selected_project'] = {
                'id': project.id, 'name': project.name,
                'budget': project.cm_budget,
                'purchased': project.purchased_amount,
                'billed': project.billed_amount,
                'pending_bill': project.pending_bill_amount,
                'payments': project.payment_total,
                'profit': project.profit_amount,
            }
            result['stages'] = [
                {'id': s.id, 'name': s.name, 'state': s.state}
                for s in project.cm_stage_ids.sorted('sequence')
            ]

        stage = Stage.browse(int(stage_id)).exists() if stage_id else Stage.browse()
        if stage and project and stage.project_id.id == project.id:
            result['selected_stage'] = {
                'id': stage.id, 'name': stage.name, 'state': stage.state,
                'budget': stage.budget_allocation,
                'purchased': stage.purchased_amount,
                'billed': stage.billed_amount,
                'pending_bill': stage.pending_bill_amount,
                'payments': stage.payment_total,
                'profit': stage.profit_amount,
            }
            top_tasks = stage.task_ids.filtered(lambda t: not t.parent_id)
            state_labels = dict(self.env['project.task']._fields['state'].selection)
            result['tasks'] = [
                {
                    'id': task.id,
                    'name': task.name,
                    'is_closed': task.is_closed,
                    'assignees': task.user_ids.mapped('name'),
                    'deadline': task.date_deadline.date().isoformat() if task.date_deadline else False,
                    'status_label': state_labels.get(task.state, task.state),
                } for task in top_tasks
            ]
        return result
