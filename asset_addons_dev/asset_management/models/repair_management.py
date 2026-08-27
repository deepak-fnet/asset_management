from odoo import models, fields, api, _
from odoo.exceptions import UserError


class RepairManagement(models.Model):
    _name = 'repair.management'
    _description = 'Repair Management'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(
        string='Reference', required=True, copy=False,
        readonly=True, default=lambda self: _('New'),
    )
    asset_id = fields.Many2one(
        'asset.asset', string='Asset', required=True,
        tracking=True, ondelete='cascade',
    )
    asset_name = fields.Char(related='asset_id.asset_name', string='Asset Name', store=True)

    # Issue Classification
    issue_type = fields.Selection([
        ('software', 'Software'),
        ('hardware', 'Hardware'),
        ('general', 'General / Non-IT'),
    ], string='Issue Type', required=True, tracking=True)

    # ── Repair mode / vendor / cost ─────────────────────────────────────
    repair_mode = fields.Selection([
        ('in_house', 'In-House'),
        ('vendor', 'Vendor'),
    ], string='Repair Mode', default='in_house', required=True, tracking=True)
    vendor_id = fields.Many2one(
        'asset.vendor', string='Repair Vendor', tracking=True,
    )
    repair_cost = fields.Monetary(
        string='Repair Cost', currency_field='currency_id', tracking=True,
    )
    bill_file = fields.Binary(string='Bill / Invoice')
    bill_filename = fields.Char()

    # Support Mode — visible for software issues
    support_mode = fields.Selection([
        ('direct_visit', 'Direct Visit'),
        ('phone_call', 'Phone Call'),
        ('google_meet', 'Google Meet'),
    ], string='Support Mode', tracking=True)

    # Handover Mode — visible when support_mode or issue_type requires direct visit
    handover_mode = fields.Selection([
        ('courier', 'Courier'),
        ('office_handover', 'Handover at Office'),
    ], string='Handover Mode', tracking=True)

    # Engineer
    engineer_id = fields.Many2one(
        'hr.employee', string='Assigned Engineer', tracking=True,
    )

    request_date = fields.Date(
        string='Request Date', default=fields.Date.context_today,
    )
    resolution_date = fields.Date(string='Resolution Date', tracking=True)
    description = fields.Text(string='Issue Description')
    resolution_notes = fields.Text(string='Resolution Notes')

    state = fields.Selection([
        ('new', 'New'),
        ('in_progress', 'In Progress'),
        ('done', 'Done'),
        ('not_repairable', 'Not Repairable'),
    ], string='Status', default='new', tracking=True)

    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
    )
    spare_part_ids = fields.One2many(
        'repair.spare.part', 'repair_id', string='Spare Parts'
    )


    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('repair.management') or _('New')
        records = super().create(vals_list)
        for rec in records:
            if rec.asset_id and rec.asset_id.state == 'assigned':
                rec.asset_id.write({'state': 'maintenance'})
        return records

    @api.onchange('issue_type')
    def _onchange_issue_type(self):
        if self.issue_type == 'hardware':
            self.support_mode = 'direct_visit'
            self.handover_mode = False
        else:
            self.support_mode = False
            self.handover_mode = False

    @api.onchange('support_mode')
    def _onchange_support_mode(self):
        if self.support_mode != 'direct_visit':
            self.handover_mode = False

    @api.onchange('repair_mode')
    def _onchange_repair_mode(self):
        if self.repair_mode != 'vendor':
            self.vendor_id = False
            self.bill_file = False
            self.bill_filename = False

    def action_start(self):
        for rec in self:
            if not rec.engineer_id:
                raise UserError("Please assign an engineer before starting.")
            if rec.issue_type == 'hardware' and not rec.handover_mode:
                raise UserError("Handover mode is required for hardware issues.")
            if rec.support_mode == 'direct_visit' and not rec.handover_mode:
                raise UserError("Handover mode is required for direct visit support.")
            rec.state = 'in_progress'

    def _effective_cost(self):
        """The real cost of a repair, whichever flow produced it.

        General (non-IT) repairs and any vendor repair have no spare-parts
        flow, so repair_cost IS the cost. Hardware/software repairs done
        in-house track cost per part instead (spare_part_ids.cost) - there
        is nothing to sum from repair_cost there, since that field is
        hidden for that combination (see the form view).
        """
        self.ensure_one()
        return self.repair_cost or sum(self.spare_part_ids.mapped('cost'))

    def _check_repair_cost_and_bill(self):
        """Cost is required to close a repair whenever it is not tracked via
        spare parts instead - i.e. general (non-IT) repairs, and any repair
        sent to a vendor (hardware/software included - a vendor repair has
        no spare-parts flow either). A bill is only required for vendor
        repairs - in-house work has no third-party invoice to attach.
        """
        for rec in self:
            if (rec.issue_type == 'general' or rec.repair_mode == 'vendor') \
                    and not rec.repair_cost:
                raise UserError(_(
                    "Enter the repair cost before marking this repair Done."))
            if rec.repair_mode == 'vendor' and not (rec.vendor_id and rec.bill_file):
                raise UserError(_(
                    "Select the vendor and attach the bill before marking "
                    "a vendor repair Done."))

    def _sync_maintenance_history(self):
        """Mirror this repair onto asset.asset's maintenance history.

        asset.maintenance is the existing generic per-asset maintenance/cost
        ledger (asset.asset.maintenance_ids) - reused here rather than a new
        model so this history is visible wherever that already is, and so
        general_asset's own history-mirroring (_UPDATE_HISTORY_MAP) keeps
        working on it unchanged.

        Keyed on repair_id so resetting a repair to New and redoing it
        updates the same history row instead of piling up duplicates.
        """
        Maintenance = self.env['asset.maintenance'].sudo()
        for rec in self:
            if not rec.asset_id:
                continue
            vals = {
                'asset_id': rec.asset_id.id,
                'maintenance_type': 'corrective',
                'request_date': rec.request_date,
                'maintenance_date': rec.resolution_date,
                'cost': rec._effective_cost(),
                'notes': rec.resolution_notes,
                'state': 'completed',
                'repair_id': rec.id,
                'repair_mode': rec.repair_mode,
                'vendor_id': rec.vendor_id.id,
                'bill_file': rec.bill_file,
                'bill_filename': rec.bill_filename,
            }
            existing = Maintenance.search([('repair_id', '=', rec.id)], limit=1)
            if existing:
                existing.write(vals)
            else:
                Maintenance.create(vals)

    def action_done(self):
        self._check_repair_cost_and_bill()
        for rec in self:
            rec.write({
                'state': 'done',
                'resolution_date': fields.Date.today(),
            })
            if rec.asset_id:
                rec.asset_id.write({
                    'state': 'assigned',
                    'last_maintenance_date': fields.Date.today(),
                })

            # ── Auto-return replacement if an assign process references this ticket ──
            assign_processes = self.env['asset.assign.process'].search([
                ('repair_ticket_id', '=', rec.id),
                ('state', '=', 'replaced'),
            ])
            for ap in assign_processes:
                ap.action_return_replacement()
        self._sync_maintenance_history()

    def action_not_repairable(self):
        for rec in self:
            # A repair abandoned at zero cost (nothing was attempted) needs
            # no bill; one where work/evaluation was already paid for on a
            # vendor repair still needs the same proof a completed repair
            # would - the asset ending up scrapped doesn't waive that.
            if rec.repair_cost and rec.repair_mode == 'vendor' and not (
                    rec.vendor_id and rec.bill_file):
                raise UserError(_(
                    "Select the vendor and attach the bill before closing "
                    "a vendor repair as Not Repairable."))
            rec.write({
                'state': 'not_repairable',
                'resolution_date': fields.Date.today(),
            })
            if rec.asset_id:
                rec.asset_id.write({'state': 'scrapped'})
        self._sync_maintenance_history()

    def action_reset_to_new(self):
        for rec in self:
            rec.state = 'new'

    def action_open_assign_process(self):
        """Open or create an assign/replace process for this repair ticket."""
        self.ensure_one()
        existing = self.env['asset.assign.process'].search([
            ('repair_ticket_id', '=', self.id),
        ], limit=1)
        if existing:
            return {
                'name': _('Assign / Replace Process'),
                'type': 'ir.actions.act_window',
                'res_model': 'asset.assign.process',
                'view_mode': 'form',
                'res_id': existing.id,
                'target': 'current',
            }
        return {
            'name': _('New Assign / Replace Process'),
            'type': 'ir.actions.act_window',
            'res_model': 'asset.assign.process',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_repair_ticket_id': self.id,
                'default_asset_id': self.asset_id.id,
                'default_employee_id': self.asset_id.assigned_employee_id.id,
            },
        }
