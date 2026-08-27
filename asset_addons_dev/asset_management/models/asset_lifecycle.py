from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from markupsafe import Markup

# ─────────────────────────────────────────────────────────────────────────────
#  JOINING PROCESS
#  One record per employee onboarding.  State: draft → confirmed → done
# ─────────────────────────────────────────────────────────────────────────────
class AssetJoiningProcess(models.Model):
    _name = 'asset.joining.process'
    _description = 'Asset Joining Process'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'asset.button.access.mixin']
    _order = 'id desc'
    _rec_name = 'name'

    name = fields.Char(
        string='Reference', required=True, copy=False,
        readonly=True, default=lambda self: _('New'),
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Employee', required=True,
        tracking=True,
        readonly=False,
        # NOTE: 'states' field arg removed in Odoo 17. Use readonly="..." in view if needed.
    )
    department_id = fields.Many2one(
        related='employee_id.department_id', string='Department',
        store=True, readonly=True,
    )
    joining_date = fields.Date(
        string='Joining Date', default=fields.Date.context_today,
        tracking=True,
    )
    notes = fields.Text(string='Notes / Requirements')

    # Requirement lines
    requirement_ids = fields.One2many(
        'asset.joining.requirement', 'joining_id',
        string='Asset Requirements',
    )

    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed / Pending Approval'),
        ('validate', 'Validated'),
        ('approved', 'Approved'),
        ('done', 'Done'),
        ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True, string='Status')

    # The assets assigned at the end of this flow. A Many2many, populated
    # programmatically by action_assign_assets() as the union of every
    # requirement line's own asset_ids once assignment completes - this is a
    # read-only result/rollup, not something picked directly on the process
    # itself (picking now happens per line, since lines can span different
    # categories with different quantities).
    assigned_asset_ids = fields.Many2many(
        'asset.asset', string='Assigned Assets', tracking=True,
    )
    asset_request_id = fields.Many2one('asset.request')

    can_show_action_approve = fields.Boolean(
        compute='_compute_can_show_action_approve')

    def _compute_can_show_action_approve(self):
        # Original restriction, before this button was wired to
        # asset.button.access: groups="asset_management.group_asset_manager".
        # Passed as the fallback here so deleting the access-control rule
        # restores THAT, instead of opening the button to everyone.
        for rec in self:
            rec.can_show_action_approve = rec._is_button_visible(
                'action_approve',
                default=rec.env.user.has_group(
                    'asset_management.group_asset_manager'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = (
                    self.env['ir.sequence'].next_by_code('asset.joining.process')
                    or _('New')
                )
        return super().create(vals_list)

    # ── workflow buttons ──────────────────────────────────────────────────────

    def action_confirm(self):
        # NOTE: this method was defined twice, identically. Python keeps the
        # last definition, so the first was dead code - harmless here only
        # because both bodies matched. Collapsed to one.
        for rec in self:
            if not rec.employee_id:
                raise UserError(_("Please select an employee."))
            if not rec.requirement_ids:
                raise UserError(_("Please add at least one asset requirement."))
            rec.state = 'confirmed'
        self._run_button_access_action('action_confirm')

    def action_validate(self):
        """Open the availability wizard instead of silently pass/failing.

        The previous version raised a UserError or moved on, showing nothing.
        The approver could not see WHICH units were available or how many -
        only that the check had passed - so a partial shortfall stayed
        invisible until the Assign step, by which point the request had
        already been validated on a false assumption.

        The wizard lists real units per category and lets the approver decide:
        validate and assign what exists, or raise an Asset Request for the
        gap. The state change now happens in the wizard's action_confirm().
        """
        self.ensure_one()
        if self.state != 'approved':
            raise UserError(_(
                "Approve the request before checking availability."))
        if not self.requirement_ids:
            raise UserError(_("Add at least one asset requirement first."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Check Availability"),
            "res_model": "joining.availability.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"active_id": self.id, "active_model": self._name},
        }

    def action_approve(self):
        """Manager approval. Comes BEFORE the availability check now.

        Flow is: submit -> approve -> validate (or raise an Asset Request)
        -> assign.

        Approving first is the right order: whether the request is justified
        is a management decision that does not depend on what happens to be
        in stock today. Checking stock first meant a legitimate request could
        be blocked at validation before anyone had even agreed the employee
        should get the asset.
        """
        for rec in self:
            if rec.state not in ('confirmed', 'draft'):
                raise UserError(_(
                    "Only a submitted request can be approved."))
            rec.state = 'approved'
        self._run_button_access_action('action_approve')

    def action_cancel(self):
        for rec in self:
            rec.state = 'cancelled'

    def action_reset_to_draft(self):
        for rec in self:
            rec.state = 'draft'

    def action_assign_assets(self):
        """Assign every asset picked across the requirement lines to the
        employee, and close the joining process.

        Each line must have exactly as many assets picked as its Quantity -
        picking is per line (Laptop x1, Mouse x1, each matched against its
        own category) rather than one flat list for the whole process.
        """
        for rec in self:
            # 'validate' is the state assigning actually happens from now -
            # the flow is submit -> approve -> validate -> assign, so by the
            # time anyone assigns, availability has been confirmed. 'approved'
            # is still accepted so a request that skipped the availability
            # check (everything obviously in stock) is not blocked.
            if rec.state not in ('approved', 'validate'):
                raise UserError(_(
                    "Approve the joining process before assigning assets."))
            if not rec.requirement_ids:
                raise UserError(_("This joining process has no asset requirements."))

            mismatched = rec.requirement_ids.filtered(
                lambda l: l.selected_count != l.quantity)
            if mismatched:
                raise UserError(_(
                    "The number of assets selected must match the requested "
                    "quantity on every line:\n%s"
                ) % "\n".join(
                    _("- %(category)s: requested %(qty)d, selected %(picked)d") % {
                        'category': line.category_id.name,
                        'qty': line.quantity,
                        'picked': line.selected_count,
                    }
                    for line in mismatched
                ))

            all_assets = rec.requirement_ids.mapped('asset_ids')
            total_picked = sum(rec.requirement_ids.mapped('selected_count'))
            if len(all_assets) != total_picked:
                # mapped() silently deduplicates, so a shortfall here means
                # the same physical asset was picked on more than one line -
                # the per-line quantity check above cannot catch that on its
                # own, since each line looks individually satisfied.
                raise UserError(_(
                    "The same asset cannot be selected on more than one "
                    "requirement line."))

            unavailable = all_assets.filtered(lambda a: a.state != 'draft')
            if unavailable:
                raise UserError(_(
                    "These assets are no longer available (already "
                    "assigned, in maintenance, or scrapped): %s"
                ) % ", ".join(unavailable.mapped('asset_name')))

            all_assets.write({
                'assigned_employee_id': rec.employee_id.id,
                'assignment_date': fields.Date.context_today(rec),
                'state': 'assigned',
            })
            rec.assigned_asset_ids = [(6, 0, all_assets.ids)]
            rec.state = 'done'
            rec.message_post(body=_(
                "Assets assigned to <b>%(employee)s</b>: %(assets)s"
            ) % {
                'employee': rec.employee_id.name,
                'assets': ", ".join(all_assets.mapped('asset_name')),
            })
        return True

    def action_raise_asset_request(self):
        if not self.asset_request_id:
            # Only categories with an actual shortfall belong on the request -
            # a line where enough units were already picked has nothing to
            # procure. The quantity raised is the GAP (requested - selected),
            # not the full requirement, since already-picked units are not
            # being requested again.
            short_lines = self.requirement_ids.filtered(
                lambda l: l.selected_count < l.quantity)
            if not short_lines:
                raise UserError(_(
                    "Every requirement line already has enough assets "
                    "selected - there is no shortage to raise a request for."))

            record = self.env['asset.request'].create({
                'requested_by': self.env.uid,
            })

            self.asset_request_id = record.id

            for line in short_lines:
                self.asset_request_id.line_ids.create({
                    'request_id': self.asset_request_id.id,
                    'asset_category_id': line.category_id.id,
                    'quantity': line.quantity - line.selected_count,
                    'description': _(
                        "Shortfall for %(employee)s's joining process %(ref)s"
                    ) % {'employee': self.employee_id.name, 'ref': self.name},
                })

        return {
            'name': 'Asset Request',
            'type': 'ir.actions.act_window',
            'res_model': 'asset.request',
            'res_id': self.asset_request_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

class AssetJoiningRequirement(models.Model):
    _name = 'asset.joining.requirement'
    _description = 'Asset Joining Requirement Line'

    joining_id = fields.Many2one(
        'asset.joining.process', string='Joining Process',
        required=True, ondelete='cascade',
    )
    category_id = fields.Many2one(
        'asset.category', string='Asset Category', required=True,
    )
    quantity = fields.Integer(string='Quantity', default=1)
    notes = fields.Char(string='Notes')

    # Picked per line rather than once on the whole process, so a request for
    # "Laptop x1, Mouse x1" lets each line be matched against its own
    # category - a single flat list would mix laptops and mice together with
    # no way to tell which selection was meant for which line.
    asset_ids = fields.Many2many(
        'asset.asset', string='Assets',
        help="Pick exactly as many assets as Quantity, matching this line's "
             "category. Only unassigned, procurement-tracked units show up "
             "(state = Draft and linked to an Asset List row).",
    )
    selected_count = fields.Integer(
        compute='_compute_assets_match', string='Selected')
    assets_match = fields.Boolean(
        compute='_compute_assets_match', string='Qty Matches',
        help="True once the number of assets picked equals Quantity.")

    @api.depends('asset_ids', 'quantity')
    def _compute_assets_match(self):
        for rec in self:
            rec.selected_count = len(rec.asset_ids)
            rec.assets_match = rec.selected_count == rec.quantity

    @api.constrains('asset_ids', 'category_id')
    def _check_asset_category(self):
        for rec in self:
            wrong = rec.asset_ids.filtered(
                lambda a: a.category_id != rec.category_id)
            if wrong:
                raise ValidationError(_(
                    "%s does not belong to category %s on this line."
                ) % (wrong[0].asset_name, rec.category_id.name))


# ─────────────────────────────────────────────────────────────────────────────
#  ASSIGN PROCESS
#  Used when a laptop is under repair and we need to assign a replacement.
#  Also tracks the original owner so the replacement auto-returns on repair done.
# ─────────────────────────────────────────────────────────────────────────────
class AssetAssignProcess(models.Model):
    _name = 'asset.assign.process'
    _description = 'Asset Assign / Replace Process'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'
    _rec_name = 'name'

    name = fields.Char(
        string='Reference', required=True, copy=False,
        readonly=True, default=lambda self: _('New'),
    )

    # ── ticket link (asset under repair) ─────────────────────────────────────
    repair_ticket_id = fields.Many2one(
        'repair.management', string='Repair Ticket',
        tracking=True,
        domain="[('state', 'not in', ['done', 'not_repairable'])]",
    )

    # Auto-filled from the repair ticket
    asset_id = fields.Many2one(
        'asset.asset', string='Asset Under Repair',
        tracking=True, readonly=True,
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Original Employee',
        tracking=True, readonly=True,
    )

    # Replacement asset selected via wizard
    replacement_asset_id = fields.Many2one(
        'asset.asset', string='Replacement Laptop',
        tracking=True, readonly=True,
    )

    state = fields.Selection([
        ('draft', 'Draft'),
        ('replaced', 'Replacement Assigned'),
        ('returned', 'Replacement Returned'),
        ('done', 'Done'),
    ], default='draft', tracking=True, string='Status')

    notes = fields.Text(string='Notes')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = (
                    self.env['ir.sequence'].next_by_code('asset.assign.process')
                    or _('New')
                )
        return super().create(vals_list)

    @api.onchange('repair_ticket_id')
    def _onchange_repair_ticket_id(self):
        """Auto-fill asset and employee from the repair ticket."""
        if self.repair_ticket_id:
            ticket = self.repair_ticket_id
            self.asset_id = ticket.asset_id
            self.employee_id = ticket.asset_id.assigned_employee_id

    def action_open_replace_wizard(self):
        """Opens wizard to pick a replacement laptop."""
        self.ensure_one()
        if not self.repair_ticket_id:
            raise UserError(_("Please select a repair ticket first."))
        return {
            'name': _('Assign Replacement Laptop'),
            'type': 'ir.actions.act_window',
            'res_model': 'asset.replace.laptop.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_assign_process_id': self.id,
                'default_employee_id': self.employee_id.id,
            },
        }

    def action_return_replacement(self):
        """
        Called when the repair ticket is done and the repaired laptop is returned.
        - replacement asset → available (draft)
        - repaired asset → re-assigned to original employee
        """
        for rec in self:
            if rec.state != 'replaced':
                raise UserError(_("Nothing to return – no replacement assigned."))
            if not rec.employee_id:
                raise UserError(_("Original employee is not set."))

            # Return replacement laptop to available state
            if rec.replacement_asset_id:
                rec.replacement_asset_id.write({
                    'assigned_employee_id': False,
                    'assignment_date': False,
                    'state': 'draft',
                })
            # Re-assign repaired laptop back to original employee
            if rec.asset_id:
                rec.asset_id.write({
                    'assigned_employee_id': rec.employee_id.id,
                    'assignment_date': fields.Date.today(),
                    'state': 'assigned',
                })
            rec.state = 'returned'

    def action_mark_done(self):
        for rec in self:
            rec.state = 'done'


# ─────────────────────────────────────────────────────────────────────────────
#  EXIT PROCESS
#  Recover assets from an employee when they leave.
# ─────────────────────────────────────────────────────────────────────────────
class AssetExitProcess(models.Model):
    _name = 'asset.exit.process'
    _description = 'Asset Exit Process'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'asset.button.access.mixin']
    _order = 'id desc'
    _rec_name = 'name'

    name = fields.Char(
        string='Reference', required=True, copy=False,
        readonly=True, default=lambda self: _('New'),
    )
    employee_id = fields.Many2one(
        'hr.employee', string='Employee', required=True,
        tracking=True,
    )
    exit_date = fields.Date(
        string='Exit Date', default=fields.Date.context_today,
        tracking=True,
    )
    notes = fields.Text(string='Notes')

    # Lines – one per asset to be recovered
    recovery_line_ids = fields.One2many(
        'asset.exit.recovery.line', 'exit_id',
        string='Assets to Recover',
    )

    state = fields.Selection([
        ('draft', 'Draft'),
        ('in_progress', 'In Progress'),
        ('done', 'Done'),
        ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True, string='Status')

    can_show_action_start = fields.Boolean(
        compute='_compute_can_show_action_start')

    def _compute_can_show_action_start(self):
        for rec in self:
            rec.can_show_action_start = rec._is_button_visible('action_start')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = (
                    self.env['ir.sequence'].next_by_code('asset.exit.process')
                    or _('New')
                )
        return super().create(vals_list)

    @api.onchange('employee_id')
    def _onchange_employee_id(self):
        """Auto-populate recovery lines with all assets assigned to this employee."""
        if not self.employee_id:
            return
        assets = self.env['asset.asset'].search([
            ('assigned_employee_id', '=', self.employee_id.id),
            ('is_general_asset', '=', True),
            ('state', '=', 'assigned'),
        ])
        lines = [(5, 0, 0)]
        for asset in assets:
            lines.append((0, 0, {
                'asset_id': asset.id,
                'recovered': False,
            }))
        self.recovery_line_ids = lines

    def action_start(self):
        for rec in self:
            if not rec.employee_id:
                raise UserError(_("Please select an employee."))
            if not rec.recovery_line_ids:
                raise UserError(_("No assets found to recover. Please add recovery lines."))
            rec.state = 'in_progress'
        self._run_button_access_action('action_start')

    def action_complete(self):
        """Recover all checked assets – set them to draft/available and unlink employee."""
        for rec in self:
            if rec.state != 'in_progress':
                raise UserError(_("Process must be in progress before completing."))
            all_recovered = all(line.recovered for line in rec.recovery_line_ids)
            if not all_recovered:
                raise UserError(_(
                    "Please mark all assets as recovered before completing the exit process."
                ))
            for line in rec.recovery_line_ids:
                if line.asset_id and line.recovered:
                    line.asset_id.write({
                        'assigned_employee_id': False,
                        'assignment_date': False,
                        'state': 'draft',
                    })
            rec.state = 'done'

    def action_cancel(self):
        for rec in self:
            rec.state = 'cancelled'

    def action_reset_to_draft(self):
        for rec in self:
            rec.state = 'draft'


class AssetExitRecoveryLine(models.Model):
    _name = 'asset.exit.recovery.line'
    _description = 'Asset Exit Recovery Line'

    exit_id = fields.Many2one(
        'asset.exit.process', string='Exit Process',
        required=True, ondelete='cascade',
    )
    asset_id = fields.Many2one(
        'asset.asset', string='Asset', required=True,
    )
    asset_category_id = fields.Many2one(
        related='asset_id.category_id', string='Category',
        readonly=True,
    )
    serial_number = fields.Char(
        related='asset_id.serial_number', string='Serial Number',
        readonly=True,
    )
    recovered = fields.Boolean(string='Recovered', default=False)
    recovery_notes = fields.Char(string='Notes')