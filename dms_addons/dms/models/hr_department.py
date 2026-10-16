import re

from odoo import api, models


class HrDepartment(models.Model):
    _inherit = 'hr.department'

    def _dms_folder_vals(self):
        self.ensure_one()
        base = re.sub(r'[^A-Z]', '', (self.name or '').upper())[:3] or 'DEP'
        code, n = base, 1
        Folder = self.env['dms.folder'].sudo().with_context(active_test=False)
        while Folder.search_count([('code', '=', code)]):
            n += 1
            code = f"{base}{n}"
        head = self.manager_id.user_id
        return {
            'name': self.name.upper(),
            'code': code,
            'department_id': self.id,
            'reviewer_id': head.id,
            'dept_head_id': head.id,
        }

    @api.model_create_multi
    def create(self, vals_list):
        depts = super().create(vals_list)
        if not self.env.context.get('dms_no_folder'):
            Folder = self.env['dms.folder'].sudo()
            for d in depts.filtered(lambda d: not d.parent_id):
                Folder.create(d._dms_folder_vals())
        return depts

    def write(self, vals):
        res = super().write(vals)
        if 'manager_id' in vals:
            Folder = self.env['dms.folder'].sudo()
            for d in self:
                head = d.manager_id.user_id
                for f in Folder.search([('department_id', '=', d.id)]):
                    f.write({'dept_head_id': head.id, 'reviewer_id': f.reviewer_id.id or head.id})
        return res
