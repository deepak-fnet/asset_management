from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class AssetList(models.Model):
    _name = "asset.list"
    _description = "Asset List (per-unit serial inventory)"
    _order = "id desc"
    _rec_name = "serial_no"

    # ----------------------------------------------------------------
    # Core fields (per spec)
    # ----------------------------------------------------------------
    serial_no = fields.Char(
        string="Serial No",
        tracking=True,
    )
    asset_id = fields.Many2one(
        "asset.asset",
        string="Asset",
        help="Optional link to the actual asset record once it has been created.",
    )

    category_id = fields.Many2one('asset.category')

    # ----------------------------------------------------------------
    # Traceability (auto-populated when generated from a PO)
    # ----------------------------------------------------------------
    request_id = fields.Many2one(
        "asset.request",
        string="Asset Request",
        ondelete="set null",
        index=True,
    )
    po_id = fields.Many2one(
        "purchase.order",
        string="Purchase Order",
        ondelete="set null",
        index=True,
    )
    po_line_id = fields.Many2one(
        "purchase.order.line",
        string="PO Line",
        ondelete="set null",
    )
    joining_requirement_id = fields.Many2one(
        "asset.joining.requirement",
        string="Source Requirement",
        ondelete="set null",
        copy=False,
        help="Carried over from the Asset Request line this row was "
             "generated from, if that line was itself raised for a "
             "shortfall on a joining process. Drives auto-assignment on "
             "Confirm.",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Product",
    )
    lot_id = fields.Many2one('stock.quant','Asset Product')
    is_done = fields.Boolean()

    # ----------------------------------------------------------------
    # Constraints
    # ----------------------------------------------------------------
    _sql_constraints = [
        (
            "serial_no_unique",
            "unique(serial_no)",
            "Serial Number must be unique across all asset list entries.",
        ),
        (
            "asset_id_unique",
            "unique(asset_id)",
            "Asset ID must be unique across all asset list entries.",
        ),
    ]

    @api.constrains("serial_no")
    def _check_serial_no(self):
        for rec in self:
            if rec.serial_no:
                serial = rec.serial_no.strip()
                if not serial:
                    raise ValidationError(_("Serial Number cannot be blank or whitespace only."))

    def write(self, vals):
        result = super().write(vals)
        if self.asset_id:
            self.asset_id.asset_list_id = self.id
        if not self.asset_id:
            self.asset_id.asset_list_id = False
        if self.po_line_id:
            self.asset_id.purchase_cost = self.po_line_id.price_subtotal
        if self.po_id:
            self.asset_id.purchase_date = self.po_id.date_order
            self.asset_id.procurement_vendor_id = self.po_id.partner_id.id
            self.asset_id.acquisition_type = 'purchased'

        return result

    def action_confirm(self):
        self.is_done = True
        if not self.asset_id:
            raise ValidationError("Please Map Asset Record")
        if self.asset_id:
            self.asset_id.asset_list_id = self.id
        if not self.asset_id:
            self.asset_id.asset_list_id = False
        if self.po_line_id:
            self.asset_id.purchase_cost = self.po_line_id.price_subtotal
        if self.po_id:
            self.asset_id.purchase_date = self.po_id.date_order
            self.asset_id.procurement_vendor_id = self.po_id.partner_id.id
            self.asset_id.acquisition_type = 'purchased'
        self._route_to_joining_requirement()

    def _route_to_joining_requirement(self):
        """Once the newly received unit is confirmed here, hand it straight
        to the joining process that was short of it - instead of leaving it
        as generic draft stock for someone to notice and pick manually.

        Assigns it to the employee as soon as its own line's quantity is
        met; only closes the whole joining process (action_assign_assets)
        once every other line on it is satisfied too, so e.g. an employee
        needing Laptop + Mouse isn't marked done for a mouse alone.
        """
        for rec in self:
            requirement = rec.joining_requirement_id
            if not requirement or not rec.asset_id:
                continue
            if requirement.selected_count >= requirement.quantity:
                continue
            requirement.asset_ids = [(4, rec.asset_id.id)]
            joining = requirement.joining_id
            joining.message_post(body=_(
                "%(asset)s received and routed to the %(category)s "
                "requirement for %(employee)s."
            ) % {
                'asset': rec.asset_id.asset_name,
                'category': requirement.category_id.name,
                'employee': joining.employee_id.name,
            })
            if joining.state in ('approved', 'validate'):
                # action_assign_assets() only assigns lines that are fully
                # picked and only closes the process once every line is -
                # safe to call as soon as this one line is complete, even if
                # siblings (e.g. a still-unordered Chair) are not.
                joining.action_assign_assets()

    @api.onchange('asset_id')
    def _onchange_asset_id(self):
        for rec in self:

            if not rec.asset_id or not rec.asset_id.serial_number:
                continue

            serial_no = rec.asset_id.serial_number

            # Search serial number in stock.lot
            lot = self.env['stock.lot'].search([
                ('name', '=', serial_no)
            ], limit=1,order="id desc")
            print(lot,"lot>>>>>>>>>>>>")
            print(lot.name,"rec.asset_id.serial_number>>>>>>>>>>>>")
            print(serial_no,"serial_no>>>>>>>>>>>>")

            # stock.lot(3, )
            # lot >> >> >> >> >> >>
            # PF1VN2YN
            # rec.asset_id.serial_number >> >> >> >> >> >>
            # PF1VN2YN
            # serial_no >> >> >> >> >> >>
            if not lot:
                continue
            quant = self.env['stock.quant'].search([
                ('lot_id', '=', lot.id),
                ('location_id.usage', '=', 'internal'),
                ('quantity', '>', 0),
            ], limit=1)

            rec.lot_id = quant.id
            rec.product_id = lot.product_id.id

            # Find incoming receipt move line
            move_line = self.env['stock.move.line'].search([
                ('lot_id', '=', lot.id),
                ('picking_id.picking_type_id.code', '=', 'incoming'),
                ('state', '=', 'done'),
            ], order='id desc', limit=1)

            if move_line and move_line.move_id.purchase_line_id:
                rec.po_line_id = move_line.move_id.purchase_line_id.id
                rec.po_id = move_line.move_id.purchase_line_id.order_id.id


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    is_asset = fields.Boolean(
        string='Is IT Asset',
        help='Enable this for products that should be managed as IT assets.'
    )


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    is_asset = fields.Boolean(
        string='Is IT Asset',
        store=True,
        readonly=True
    )