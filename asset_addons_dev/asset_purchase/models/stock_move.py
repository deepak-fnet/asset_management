# -*- coding: utf-8 -*-

import logging

from markupsafe import Markup

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class StockMove(models.Model):
    _inherit = "stock.move"

    is_fixed_asset = fields.Boolean(
        string="Is Fixed Asset",
        compute='_compute_is_fixed_asset', store=True, readonly=False,
        help="On validation, one fixed asset record is created per unit received "
             "on this line.")
    asset_count = fields.Integer(string="Assets", compute='_compute_asset_count')

    @api.depends('product_id')
    def _compute_is_fixed_asset(self):
        """Default from the product category, so receipts without a purchase
        order also get the flag. The purchase order line overrides this via
        ``_prepare_stock_move_vals``."""
        Mapping = self.env['asset.category.mapping']
        for move in self:
            move.is_fixed_asset = Mapping._is_asset_product(move.product_id)

    def _compute_asset_count(self):
        grouped = self.env['asset.asset']._read_group(
            [('stock_move_id', 'in', self.ids)], ['stock_move_id'], ['__count'])
        counts = {move.id: count for move, count in grouped}
        for move in self:
            move.asset_count = counts.get(move.id, 0)

    @api.model
    def _prepare_merge_moves_distinct_fields(self):
        """Never merge an asset move into a non-asset one."""
        return super()._prepare_merge_moves_distinct_fields() + ['is_fixed_asset']

    def _prepare_move_split_vals(self, qty):
        """Keep the flag on backorder splits."""
        vals = super()._prepare_move_split_vals(qty)
        vals['is_fixed_asset'] = self.is_fixed_asset
        return vals

    # ------------------------------------------------------------------
    # Asset creation
    # ------------------------------------------------------------------

    def _action_done(self, cancel_backorder=False):
        moves_todo = super()._action_done(cancel_backorder=cancel_backorder)
        moves_todo._create_fixed_assets()
        return moves_todo

    def _resolve_asset_category(self, mapping):
        """Return the asset category for this move, ALWAYS flagged general.

        Three things go wrong here if this is left loose, and all three end
        the same way - an asset that exists but appears in no menu, which
        looks exactly like the receipt silently created nothing:

        1. No category at all. The General Assets action filters on
           category_id.is_general, so a blank category means the asset is
           invisible there. Never return an empty category.
        2. A category found by name that is NOT flagged general. Same
           outcome. If a matching category exists but is not general, it is
           flagged now rather than silently reused.
        3. The mapping's own category not flagged general. The mapping form
           restricts the domain, but a category can be un-flagged afterwards.

        Falls back to a fixed "Uncategorised Assets" category rather than
        returning empty, so a product category with a blank name still
        produces something reachable.
        """
        self.ensure_one()
        Category = self.env['asset.category']

        # Product-level asset_category_id wins over the product-category
        # mapping - the product is the more specific statement of intent, and
        # it is what the buyer set on the product form. The mapping remains
        # the fallback so mapping-only setups keep working.
        category = self.product_id.asset_category_id or mapping.category_id
        if not category:
            name = self.product_id.categ_id.name or 'Uncategorised Assets'
            category = Category.search([('name', '=', name)], limit=1)
            if not category:
                category = Category.create({'name': name, 'is_general': True})

        # Whatever we ended up with, it has to be general or the asset is
        # invisible in the only menu that lists these.
        if category and not category.is_general:
            category.sudo().write({'is_general': True})
            _logger.info(
                "Flagged asset category %s as general: assets created from "
                "receipts must be visible under General Assets.", category.name)
        return category

    def _asset_base_vals(self, mapping):
        """Values shared by every asset created from this move.

        Maps onto asset.asset (general_asset's extension of it), not the old
        asset.addition. A few fields deliberately do not map one-to-one:

        * physical_location_id -> location_id. general_asset uses
          asset_management's hierarchical asset.location for every location
          role rather than a separate flat physical.location model.
        * department_id -> procurement_department_id. asset.asset's own
          department_id is related to the assigned employee and is readonly,
          and at receipt time nobody holds the asset yet. The department it
          was bought FOR is a different fact, so it gets its own field.
        * vendor_id -> procurement_vendor_id. asset.asset.vendor_id points at
          asset.vendor (an AMC provider), not res.partner - writing the
          purchase partner there would be wrong.
        """
        self.ensure_one()
        picking = self.picking_id
        order = self.purchase_line_id.order_id
        partner = order.partner_id
        date_done = picking.date_done or fields.Datetime.now()
        receipt_date = fields.Datetime.context_timestamp(self, date_done).date()
        return {
            'asset_name': self.product_id.display_name,
            'category_id': self._resolve_asset_category(mapping).id,
            # Mark it as a FIXED asset explicitly.
            #
            # is_general_asset used to be a related field off the category,
            # so it filled itself in. It is now a plain stored Boolean, which
            # means anything that does not set it leaves it False - and a
            # False fixed asset is invisible to the joining process assign
            # dropdown and to the availability wizard, both of which filter
            # on it. Goods received would have created assets that then
            # appeared nowhere assignable.
            'is_general_asset': True,
            'sub_category_id': mapping.sub_category_id.id or False,
            'asset_value': self.price_unit,
            'acquisition_date': receipt_date,
            'plant_id': mapping.plant_id.id or False,
            'location_id': mapping.location_id.id or False,
            'procurement_department_id': mapping.department_id.id or False,
            'amc_vendor_id': partner.id or False,
            'purchase_date': order.date_order and order.date_order.date() or False,
            'receipt_date': receipt_date,
            'invoice_ref': getattr(order, 'bill_reference', '') or '',
            'procurement_vendor_id': partner.id or False,
            'purchase_cost': self.price_unit,
            'acquisition_type': 'purchased',
            'stock_move_id': self.id,
            'picking_id': picking.id or False,
            'purchase_order_id': order.id or False,
            'product_id': self.product_id.id,
        }

    def _next_unique_asset_name(self, base_name):
        """Disambiguate the display name for one auto-created asset unit.

        A qty>1 receipt line (e.g. 3x "Thinkpad") would otherwise create
        several asset.asset records with the identical asset_name, which is
        the model's _rec_name - confusing in every dropdown and list. This
        just appends a running, globally-unique suffix; asset_code remains
        the real unique identifier, and the user is free to rename the asset
        afterwards.
        """
        suffix = self.env['ir.sequence'].sudo().next_by_code('asset.asset.unit.suffix')
        return '%s - %s' % (base_name, suffix) if suffix else base_name

    def _create_fixed_assets(self):
        """Create one ``asset.asset`` per unit received on flagged moves."""
        Asset = self.env['asset.asset']
        Mapping = self.env['asset.category.mapping']

        candidates = self.filtered(lambda m: (
            m.is_fixed_asset
            and m.state == 'done'
            and m.picking_id.picking_type_code == 'incoming'
        ))
        if not candidates:
            return Asset

        # Re-run guard: one query for the whole batch. Assets already linked to a
        # move mean that move has been processed.
        done_moves = set(Asset.search(
            [('stock_move_id', 'in', candidates.ids)]).mapped('stock_move_id').ids)

        vals_list = []
        notes = {}

        def note(picking, message):
            notes.setdefault(picking, []).append(message)

        for move in candidates:
            if move.id in done_moves:
                continue

            mapping = Mapping._resolve_for_product(move.product_id)
            base = move._asset_base_vals(mapping)

            if move.product_id.tracking == 'serial':
                # _action_done guarantees one move line per unit, each with
                # quantity 1.0 and its own stock.lot.
                for line in move.move_line_ids:
                    unit = dict(
                        base,
                        asset_name=move._next_unique_asset_name(base['asset_name']),
                        stock_move_line_id=line.id,
                        lot_id=line.lot_id.id or False,
                    )
                    # Only send serial_number when there IS one. Passing an
                    # empty string is not the same as omitting the key: it
                    # counts as "already supplied", so the model's own unique
                    # placeholder fallback never runs and every untracked unit
                    # ends up with serial '' - which then fails
                    # asset.asset._check_serial_number_unique on the second row.
                    serial = line.lot_id.name or line.lot_name
                    if serial:
                        unit['serial_number'] = serial
                    vals_list.append(unit)
            else:
                qty = sum(move.move_line_ids.mapped('quantity_product_uom'))
                count = int(round(qty))
                if count <= 0:
                    continue
                if abs(qty - count) > 0.01:
                    note(move.picking_id, _(
                        "%(product)s: received quantity %(qty)s is not a whole "
                        "number, created %(count)s asset(s).",
                        product=move.product_id.display_name, qty=qty, count=count))
                # 'lot' tracking: every unit shares the same lot.
                lot = move.move_line_ids.lot_id[:1]
                for _index in range(count):
                    unit = dict(
                        base,
                        asset_name=move._next_unique_asset_name(base['asset_name']),
                        lot_id=lot.id or False,
                    )
                    # Same reasoning as above. Note that with 'lot' tracking
                    # every unit shares ONE lot name, so even a real lot name
                    # cannot be used as a per-unit serial - it would collide
                    # for any quantity above 1. Leave it out and let the model
                    # generate a unique placeholder per unit; the lot itself is
                    # still recorded in lot_id.
                    if count == 1 and lot.name:
                        unit['serial_number'] = lot.name
                    vals_list.append(unit)

            if not mapping:
                note(move.picking_id, _(
                    "%(product)s: product category \"%(category)s\" has no asset "
                    "mapping. The asset category was created from the product "
                    "category name; plant, location and department were left blank.",
                    product=move.product_id.display_name,
                    category=move.product_id.categ_id.display_name))

        if not vals_list:
            self._post_asset_creation_note(Asset, notes)
            return Asset

        # One batched create. The savepoint keeps the cursor usable if the
        # create fails, so a
        # configuration problem in the asset module never blocks a receipt.
        assets = Asset
        try:
            with self.env.cr.savepoint():
                assets = Asset.create(vals_list)
        except Exception as error:
            _logger.exception(
                "Could not create fixed assets for moves %s", candidates.ids)
            # Put the ACTUAL reason in the chatter, not just "check the logs".
            # Without it the only visible symptom is that no assets appeared,
            # and diagnosing that means SSH access to the server - which the
            # person validating a receipt generally does not have.
            for picking in candidates.picking_id:
                picking.message_post(body=_(
                    "Fixed assets could not be created automatically for this "
                    "receipt.\n\nReason: %(reason)s\n\n"
                    "Check that the product category has an asset mapping, and "
                    "that the mapped asset category has 'Is General Asset' "
                    "ticked. Assets can also be created manually."
                ) % {"reason": error})
            return Asset

        self._link_to_asset_list(assets)
        self._notify_requesters(assets)
        self._post_asset_creation_note(assets, notes)
        return assets

    def _notify_requesters(self, assets):
        """Tell whoever raised the asset request that the goods have landed.

        Without this the requester has no signal at all: the request sits in
        po_done and the only way to find out the assets exist is to keep
        checking. The message is posted on the REQUEST (not emailed directly)
        so it reaches them through whatever notification setting they already
        use, and stays on the record as history.
        """
        by_request = {}
        for asset in assets:
            request = asset.purchase_order_id.asset_request_id
            if request:
                by_request.setdefault(request, self.env['asset.asset'])
                by_request[request] |= asset

        for request, request_assets in by_request.items():
            recipient = request.requested_by
            if not recipient:
                continue
            body = _(
                "<p>Goods received - <b>%(count)s asset(s)</b> have been "
                "created for request %(req)s and are ready to be mapped to "
                "the Asset List.</p><ul>%(rows)s</ul>"
            ) % {
                "count": len(request_assets),
                "req": request.name,
                "rows": "".join(
                    "<li>%s - %s</li>" % (a.asset_code or "", a.asset_name or "")
                    for a in request_assets),
            }
            request.message_post(
                body=Markup(body),
                subject=_("Assets received for %s") % request.name,
                partner_ids=recipient.partner_id.ids,
                message_type="notification",
                subtype_xmlid="mail.mt_comment",
            )

    def _link_to_asset_list(self, assets):
        """Fill the request's blank asset.list rows with the created assets.

        asset.request generates one empty asset.list row per ordered unit when
        its POs complete, expecting someone to type each serial by hand. But
        the receipt has just created the real asset records - so the rows can
        be filled in automatically, matched by PO line so the right asset
        lands on the right row.

        Without this the two halves never meet: assets exist under General
        Assets, the request still shows empty rows, and it cannot be closed
        because action_done() requires every row to have a serial.

        Only blank rows are touched. A row someone has already filled in by
        hand is left exactly as it is.
        """
        if 'asset.list' not in self.env:
            return
        AssetList = self.env['asset.list'].sudo()

        for asset in assets:
            po_line = asset.stock_move_id.purchase_line_id
            if not po_line:
                continue
            # Already linked (re-run, or someone did it manually)?
            if AssetList.search_count([('asset_id', '=', asset.id)]):
                continue
            row = AssetList.search([
                ('po_line_id', '=', po_line.id),
                ('asset_id', '=', False),
                '|', ('serial_no', '=', False), ('serial_no', '=', ''),
            ], limit=1)
            if not row:
                continue
            try:
                with self.env.cr.savepoint():
                    row.write({
                        'asset_id': asset.id,
                        'serial_no': asset.serial_number,
                        'product_id': asset.product_id.id,
                    })
            except Exception:
                # asset.list enforces unique serial_no and unique asset_id.
                # A clash here should not undo an otherwise good receipt -
                # the row can still be filled in by hand.
                _logger.warning(
                    "Could not link asset %s to its asset.list row",
                    asset.asset_code, exc_info=True)

    def _post_asset_creation_note(self, assets, notes):
        """Log the created assets in the chatter of each receipt."""
        for picking in assets.picking_id | self.env['stock.picking'].union(*notes.keys()):
            picking_assets = assets.filtered(lambda a: a.picking_id == picking)
            lines = []
            if picking_assets:
                lines.append(_("Created %s fixed asset(s):", len(picking_assets)))
                for product in picking_assets.product_id:
                    refs = picking_assets.filtered(lambda a: a.product_id == product)
                    # asset.asset has no 'name' field - that was
                    # asset.addition's. The equivalent identifier here is
                    # asset_code (the generated tag, e.g. FN-MS-00001);
                    # asset_name is the descriptive label and would just
                    # repeat the product name already printed on the left.
                    lines.append("%s — %s" % (
                        product.display_name,
                        ", ".join(refs.mapped('asset_code')),
                    ))
            lines.extend(notes.get(picking, []))
            if lines:
                # Markup.join escapes each item and keeps the separator as HTML.
                picking.message_post(body=Markup("<br/>").join(lines))