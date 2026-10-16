from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class DmsDisposal(models.Model):
    """Slide 12: archived files are discarded only after approval."""
    _name = 'dms.disposal'
    _description = 'DMS Disposal Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(default=lambda s: _("New"), readonly=True, copy=False)
    folder_id = fields.Many2one('dms.folder', "Department Folder", required=True,
                                domain=[('parent_id', '=', False), ('is_archive', '=', False)])
    revision_ids = fields.Many2many('dms.revision', string="Archived Files",
                                    domain="[('state', '=', 'archived')]")
    reason = fields.Text()
    requested_by_id = fields.Many2one('res.users', default=lambda s: s.env.user, readonly=True)
    state = fields.Selection([('draft', 'Draft'), ('submitted', 'Waiting Approval'),
                              ('done', 'Discarded'), ('refused', 'Refused')],
                             default='draft', tracking=True)
    controller_approved_id = fields.Many2one('res.users', "Controller Approval", readonly=True)
    head_approved_id = fields.Many2one('res.users', "Department Head Approval", readonly=True)
    dept_head_id = fields.Many2one(related='folder_id.dept_head_id')
    refuse_reason = fields.Text(readonly=True)
    file_log = fields.Text("Disposal Log", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for v in vals_list:
            if v.get('name', _("New")) == _("New"):
                v['name'] = self.env['ir.sequence'].next_by_code('dms.disposal') or _("New")
        return super().create(vals_list)

    def _check_controller(self):
        if not self.env.user.has_group('dms.group_dms_controller'):
            raise AccessError(_("Only the Document Controller can do this."))

    def action_submit(self):
        self._check_controller()
        today = fields.Date.context_today(self)
        for d in self:
            if not d.revision_ids:
                raise UserError(_("Add the archived files to discard."))
            bad = d.revision_ids.filtered(lambda r: r.state != 'archived' or (r.retention_end and r.retention_end > today))
            if bad:
                raise UserError(_("Only archived files whose retention has ended can be discarded:\n%s",
                                  "\n".join(bad.mapped('display_name'))))
            if not d.dept_head_id:
                raise UserError(_("Folder %s has no Department Head set.", d.folder_id.complete_name))
            d.state = 'submitted'
            d.activity_schedule('mail.mail_activity_data_todo', user_id=d.dept_head_id.id,
                                summary=_("DMS disposal approval: %s", d.name))
            self.env['dms.log'].log('disposal_request', folder=d.folder_id,
                                    details=f"{d.name}: {len(d.revision_ids)} file(s)")

    def action_approve(self):
        user = self.env.user
        for d in self:
            if d.state != 'submitted':
                raise UserError(_("Not waiting for approval."))
            done = False
            if user.has_group('dms.group_dms_controller') and not d.controller_approved_id:
                d.controller_approved_id = user
                done = True
            if user == d.dept_head_id and not d.head_approved_id:
                d.head_approved_id = user
                done = True
            if not done:
                raise AccessError(_("You have nothing to approve on %s.", d.name))
            self.env['dms.log'].log('disposal_approve', folder=d.folder_id, details=d.name)
            if d.controller_approved_id and d.head_approved_id:
                d._discard()

    def action_refuse(self, reason=None):
        user = self.env.user
        for d in self:
            if not (user.has_group('dms.group_dms_controller') or user == d.dept_head_id):
                raise AccessError(_("Only the Controller or the Department Head can refuse."))
            d.write({'state': 'refused', 'refuse_reason': reason})
            d.activity_ids.unlink()
            self.env['dms.log'].log('disposal_refuse', folder=d.folder_id, details=f"{d.name}: {reason or ''}")

    def _discard(self):
        storage = self.env['dms.storage']
        for d in self.sudo():
            lines = []
            for r in d.revision_ids:
                storage.delete_file(r.storage_path)
                lines.append(f"{fields.Datetime.now()} · {r.display_name} · {r.storage_path}")
                self.env['dms.log'].log('discard', revision=r, details=d.name)
                r.write({'state': 'disposed', 'storage_path': False})
                doc = r.document_id
                if all(x.state in ('disposed', 'rejected', 'dropped') for x in doc.revision_ids):
                    doc.state = 'discarded'
            d.write({'state': 'done', 'file_log': "\n".join(lines)})
            d.activity_ids.unlink()
