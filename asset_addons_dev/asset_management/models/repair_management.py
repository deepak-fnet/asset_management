from odoo import models, fields, api, _
from odoo.exceptions import UserError

REPAIR_VENDOR_LOCATION_XMLID = 'stock.stock_location_suppliers'


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
        ('in_house', 'Internal Team'),
        ('vendor', 'External Team'),
    ], string='Repair Mode', default='in_house', required=True, tracking=True)
    vendor_id = fields.Many2one(
        'asset.vendor', string='Repair Vendor', tracking=True,
    )
    repair_cost = fields.Monetary(
        string='Repair Cost', currency_field='currency_id', tracking=True,
        help="Internal Team: computed automatically from the Spare Parts "
             "page (a service/labour product line included) - not manually "
             "editable. External Team: enter the vendor's charge by hand.",
    )
    bill_file = fields.Binary(string='Bill / Invoice')
    bill_filename = fields.Char()

    # ── External (vendor) repair: physical handover of the asset ────────
    # The asset itself leaves the premises and comes back - tracked as a
    # real stock transfer only when the asset has a linked product (most
    # manually-created/general assets never went through a PO receipt and
    # have no stock record to move - see action_dispatch_to_vendor()).
    dispatch_picking_id = fields.Many2one(
        'stock.picking', string='Dispatch Transfer', readonly=True, copy=False,
        help="The outgoing transfer sending this asset to the vendor.",
    )
    dispatch_date = fields.Date(string='Dispatched On', readonly=True, copy=False)
    return_picking_id = fields.Many2one(
        'stock.picking', string='Return Transfer', readonly=True, copy=False,
        help="The incoming transfer receiving this asset back from the vendor.",
    )
    return_date = fields.Date(string='Returned On', readonly=True, copy=False)

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

    # Snapshotted the moment the asset is moved to 'maintenance' (see
    # action_start()), so closing this repair can restore EXACTLY whatever
    # state the asset was actually in beforehand - not a hardcoded guess.
    # A plain Char, not a Selection matching asset.asset.state, so it can
    # never itself go stale if that selection's values ever change.
    asset_state_before = fields.Char(readonly=True, copy=False)

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
        # The asset is NOT moved to 'maintenance' here anymore - a repair
        # ticket can now exist in 'New' (queued, not yet worked on) without
        # the asset itself being marked under repair. That transition now
        # happens in action_start(), which is also where the state to
        # restore to on close is snapshotted (see asset_state_before).
        return super().create(vals_list)

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
        self._sync_cost_from_spares()

    @api.onchange('spare_part_ids')
    def _onchange_spare_part_ids(self):
        self._sync_cost_from_spares()

    def action_start(self):
        for rec in self:
            if not rec.engineer_id:
                raise UserError("Please assign an engineer before starting.")
            if rec.issue_type == 'hardware' and not rec.handover_mode:
                raise UserError("Handover mode is required for hardware issues.")
            if rec.support_mode == 'direct_visit' and not rec.handover_mode:
                raise UserError("Handover mode is required for direct visit support.")
            # Move the asset under repair NOW (not at ticket creation) -
            # remember exactly what it was, so closing this repair can put
            # it back precisely instead of assuming 'assigned'.
            if rec.asset_id and rec.asset_id.state != 'maintenance':
                rec.asset_state_before = rec.asset_id.state
                rec.asset_id.write({'state': 'maintenance'})
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

    def _sync_cost_from_spares(self):
        """Keep repair_cost mirroring the Spare Parts total for Internal
        Team repairs. Called from repair.spare.part's create/write/unlink,
        and from the form's onchange, so it stays correct whether a line is
        added interactively or through the API.

        Deliberately not a compute field: a compute would either have to
        stay read-only for BOTH modes (breaking the External Team's manual
        entry) or risk clobbering a manually-typed vendor cost the moment
        an unrelated dependency recomputes. A plain field kept in sync by
        this method gets the same "always current for Internal Team" result
        without that trap.
        """
        for rec in self:
            if rec.repair_mode == 'in_house':
                rec.repair_cost = sum(rec.spare_part_ids.mapped('cost'))

    def _get_repair_picking_type(self, code):
        """Get-or-create the dedicated 'Repair Dispatch'/'Repair Return'
        picking type for the current company, so these transfers stay
        distinguishable from ordinary sales deliveries and PO receipts in
        inventory reports instead of mixing into the warehouse's standard
        Delivery/Receipt types.
        """
        self.ensure_one()
        name = 'Repair Dispatch' if code == 'outgoing' else 'Repair Return'
        PickingType = self.env['stock.picking.type'].sudo()
        picking_type = PickingType.search([
            ('name', '=', name), ('company_id', '=', self.env.company.id),
        ], limit=1)
        if picking_type:
            return picking_type

        warehouse = self.env['stock.warehouse'].sudo().search(
            [('company_id', '=', self.env.company.id)], limit=1)
        if not warehouse:
            raise UserError(_(
                "No warehouse is configured for this company - cannot set "
                "up the %s transfer type."
            ) % name)
        vendor_location = self.env.ref(REPAIR_VENDOR_LOCATION_XMLID)

        vals = {
            'name': name,
            'code': code,
            'sequence_code': 'RPD' if code == 'outgoing' else 'RPR',
            'warehouse_id': warehouse.id,
            'company_id': self.env.company.id,
        }
        if code == 'outgoing':
            vals['default_location_src_id'] = warehouse.lot_stock_id.id
            vals['default_location_dest_id'] = vendor_location.id
        else:
            vals['default_location_src_id'] = vendor_location.id
            vals['default_location_dest_id'] = warehouse.lot_stock_id.id
        return PickingType.create(vals)

    def _repair_stock_move_vals(self, picking, lot=None):
        """One stock.move for this repair's asset, plus the lot on its move
        line when the product is tracked - so a "send to vendor" transfer
        moves the exact physical unit this asset record represents, not
        just any one unit of the same product that happens to be on hand.
        """
        self.ensure_one()
        product = self.asset_id.product_id
        move_vals = {
            'picking_id': picking.id,
            'product_id': product.id,
            'product_uom_qty': 1,
            'product_uom': product.uom_id.id,
            'location_id': picking.location_id.id,
            'location_dest_id': picking.location_dest_id.id,
            'description_picking': self.asset_id.asset_name,
        }
        return move_vals

    def _find_asset_lot(self):
        """The stock.lot matching this asset's serial number, if any - same
        lookup asset.list._onchange_asset_id() already uses, reused here so
        a repair transfer picks the same physical unit consistently.
        """
        self.ensure_one()
        if not self.asset_id.product_id or not self.asset_id.serial_number:
            return self.env['stock.lot']
        return self.env['stock.lot'].sudo().search([
            ('name', '=', self.asset_id.serial_number),
            ('product_id', '=', self.asset_id.product_id.id),
        ], limit=1)

    def _validate_repair_transfer(self, picking, lot):
        """Confirm, reserve, set the lot (when tracked) and validate in one
        shot - same one-click pattern action_confirm_use_spare() already
        uses elsewhere in this module, rather than exposing raw picking
        states to the repair-management user.
        """
        picking.action_confirm()
        picking.action_assign()
        for move in picking.move_ids:
            if lot and move.product_id.tracking != 'none':
                for move_line in move.move_line_ids:
                    move_line.lot_id = lot.id
            move.quantity = move.product_uom_qty
            move.picked = True
        picking.button_validate()

    def action_dispatch_to_vendor(self):
        """Send the physical asset out to the vendor for repair."""
        self.ensure_one()
        if self.repair_mode != 'vendor':
            raise UserError(_("Only an External Team repair is sent to a vendor."))
        if not self.vendor_id:
            raise UserError(_("Select the vendor before dispatching the asset."))
        if not self.asset_id.product_id:
            raise UserError(_(
                "This asset has no linked product/stock record, so it "
                "cannot be tracked through a stock transfer - proceed with "
                "the vendor handover without one."))
        if self.dispatch_picking_id:
            raise UserError(_("This repair has already been dispatched."))

        picking_type = self._get_repair_picking_type('outgoing')
        picking = self.env['stock.picking'].sudo().create({
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
            'origin': self.name,
        })
        lot = self._find_asset_lot()
        self.env['stock.move'].sudo().create(self._repair_stock_move_vals(picking))
        self._validate_repair_transfer(picking, lot)

        self.write({
            'dispatch_picking_id': picking.id,
            'dispatch_date': fields.Date.context_today(self),
        })
        self.message_post(body=_(
            "Asset dispatched to vendor %s (transfer %s)."
        ) % (self.vendor_id.display_name, picking.name))

    def action_receive_from_vendor(self):
        """Receive the physical asset back from the vendor after repair."""
        self.ensure_one()
        if not self.dispatch_picking_id:
            raise UserError(_("This asset has not been dispatched yet."))
        if self.return_picking_id:
            raise UserError(_("This asset has already been received back."))

        picking_type = self._get_repair_picking_type('incoming')
        picking = self.env['stock.picking'].sudo().create({
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
            'origin': self.name,
        })
        lot = self._find_asset_lot()
        self.env['stock.move'].sudo().create(self._repair_stock_move_vals(picking))
        self._validate_repair_transfer(picking, lot)

        self.write({
            'return_picking_id': picking.id,
            'return_date': fields.Date.context_today(self),
        })
        self.message_post(body=_(
            "Asset received back from vendor %s (transfer %s)."
        ) % (self.vendor_id.display_name, picking.name))

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
            # Only enforced when the asset actually has a stock record to
            # move (see action_dispatch_to_vendor()) - an asset with none
            # never got Dispatch/Receive buttons in the first place, so
            # there is nothing to have completed.
            if rec.repair_mode == 'vendor' and rec.asset_id.product_id:
                if not rec.dispatch_picking_id:
                    raise UserError(_(
                        "Dispatch this asset to the vendor before marking "
                        "the repair Done."))
                if not rec.return_picking_id:
                    raise UserError(_(
                        "Receive this asset back from the vendor before "
                        "marking the repair Done."))

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
                    # Restore exactly what it was before this repair started
                    # (see action_start()) - not a hardcoded 'assigned',
                    # which would be wrong for an asset that was e.g. still
                    # 'draft' when it was sent for repair. Old repairs
                    # created before this field existed have no snapshot;
                    # 'assigned' is kept only as that fallback.
                    'state': rec.asset_state_before or 'assigned',
                    'last_maintenance_date': fields.Date.today(),
                })
                rec.asset_state_before = False

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
            # Same restoration as action_done() - a repair abandoned back to
            # New must not leave the asset stuck in 'maintenance' forever
            # with no ticket left actively working on it.
            if rec.asset_id and rec.asset_id.state == 'maintenance':
                rec.asset_id.write({'state': rec.asset_state_before or 'assigned'})
            rec.asset_state_before = False
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
