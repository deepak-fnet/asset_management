# -*- coding: utf-8 -*-
from odoo import api, models


class ConstructionDashboard(models.AbstractModel):
    _name = 'cm.dashboard'
    _description = 'Construction Management Dashboard Data Provider'

    @api.model
    def get_dashboard_data(self, partner_id=False, master_id=False, project_id=False):
        MasterProject = self.env['cm.master.project']
        Project = self.env['project.project']

        all_masters = MasterProject.search([])
        customers = {}
        for m in all_masters:
            if m.partner_id:
                customers[m.partner_id.id] = m.partner_id.name

        masters_domain = [('partner_id', '=', int(partner_id))] if partner_id else []
        masters = MasterProject.search(masters_domain)

        subprojects = Project.browse()
        if master_id:
            subprojects = Project.search([('master_project_id', '=', int(master_id))])

        result = {
            'customers': [{'id': cid, 'name': name} for cid, name in sorted(customers.items(), key=lambda kv: kv[1])],
            'masters': [{'id': m.id, 'name': m.name} for m in masters],
            'subprojects': [{'id': p.id, 'name': p.name} for p in subprojects],
            'summary': {
                'master_count': len(all_masters),
                'subproject_count': Project.search_count([('master_project_id', '!=', False)]),
            },
        }

        if project_id:
            project = Project.browse(int(project_id)).exists()
            if project:
                result['selected_project'] = self._project_payload(project)
                result['stages'] = [self._stage_payload(s) for s in project.cm_stage_ids.sorted('sequence')]
        elif master_id:
            master = MasterProject.browse(int(master_id)).exists()
            if master:
                result['selected_master'] = self._master_payload(master)
                result['subprojects_detail'] = [self._project_payload(p) for p in master.subproject_ids]
        elif partner_id:
            result['masters_detail'] = [self._master_payload(m) for m in masters]
        else:
            result['masters_detail'] = [self._master_payload(m) for m in all_masters]

        return result

    def _master_payload(self, master):
        return {
            'id': master.id,
            'name': master.name,
            'partner': master.partner_id.name,
            'total_contract_value': master.total_contract_value,
            'advance_received': master.advance_received,
            'overall_completion': round(master.overall_completion, 1),
            'state': master.state,
            'subproject_count': master.subproject_count,
        }

    def _project_payload(self, project):
        return {
            'id': project.id,
            'name': project.name,
            'project_no': project.project_no,
            'cm_budget': project.cm_budget,
            'cm_status': project.cm_status,
            'completion_percent': round(project.completion_percent, 1),
            'deadline': project.date.isoformat() if project.date else False,
            'stage_count': project.cm_stage_count,
        }

    def _stage_payload(self, stage):
        return {
            'id': stage.id,
            'name': stage.name,
            'state': stage.state,
            'budget_allocation': stage.budget_allocation,
            'boq_total': stage.boq_total,
            'purchased_amount': stage.purchased_amount,
            'used_amount': stage.used_amount,
            'percent_complete': round(stage.percent_complete, 1),
            'target_date': stage.target_date.isoformat() if stage.target_date else False,
            'task_count': stage.task_count,
            'task_done_count': stage.task_done_count,
            'subtask_count': stage.subtask_count,
            'subtask_done_count': stage.subtask_done_count,
        }

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
            {'id': p.id, 'name': p.name, 'cm_status': p.cm_status} for p in master.subproject_ids
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
