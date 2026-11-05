import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Fixed asset is the source for these on its linked IT asset (it_asset_id).
FA_TO_IT_FIELDS = [
    # employee assignment
    'assigned_employee_id', 'department_id', 'assignment_date', 'location_id',
    # procurement
    'acquisition_type', 'purchase_date', 'in_service_date', 'purchase_cost', 'procurement_vendor_id',
    'invoice_ref', 'purchase_order_id', 'receipt_date', 'invoice_move_id', 'procurement_department_id',
    # warranty
    'warranty_template_id', 'warranty_period_months', 'warranty_start_date', 'warranty_end_date',
    'warranty_type', 'warranty_provider_id', 'warranty_contract_no', 'warranty_support_contact',
    'warranty_coverage_notes',
    # AMC / maintenance
    'vendor_id', 'amc_status', 'amc_vendor_id', 'amc_start_date', 'amc_end_date', 'amc_expiry_date',
    'last_maintenance_date', 'next_maintenance_date', 'maintenance_type', 'maintenance_status', 'condition',
]


class AssetAssetFixedSync(models.Model):
    _inherit = 'asset.asset'

    fixed_asset_id = fields.Many2one('asset.asset', string='Fixed Asset', compute='_compute_fixed_asset_id',
                                     help="Fixed asset this IT asset is linked to (its IT Asset field). "
                                          "Assignment, procurement, warranty and maintenance come from it.")

    def _compute_fixed_asset_id(self):
        if 'it_asset_id' not in self._fields:
            self.fixed_asset_id = False
            return
        data = {r.it_asset_id.id: r.id for r in self.sudo().search([('it_asset_id', 'in', self.ids)])}
        for rec in self:
            rec.fixed_asset_id = data.get(rec.id, False)

    def _fa_sync_fields(self):
        return [f for f in FA_TO_IT_FIELDS if f in self._fields]

    def _fa_push_to_it(self, names=None):
        """Copy the fixed asset's values onto its IT asset."""
        names = [n for n in (names or self._fa_sync_fields()) if n in self._fields]
        for fa in self.filtered(lambda r: 'it_asset_id' in r._fields and r.it_asset_id and r.it_asset_id != r):
            it = fa.it_asset_id
            vals = {}
            for n in names:
                f = fa._fields[n]
                v, cur = fa[n], it[n]
                if f.type == 'many2one':
                    v, cur = v.id, cur.id
                if v != cur:
                    vals[n] = v
            if vals:
                it.sudo().with_context(fa_sync=True).write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs.filtered(lambda r: 'it_asset_id' in r._fields and r.it_asset_id)._fa_push_to_it()
        return recs

    def write(self, vals):
        res = super().write(vals)
        if self.env.context.get('fa_sync'):
            return res
        synced = set(self._fa_sync_fields()) & set(vals)
        if 'it_asset_id' in vals:
            self._fa_push_to_it()
        elif synced:
            self._fa_push_to_it(list(synced))
        return res

    @api.model
    def _fa_sync_all(self):
        """One-time / safety sync for every fixed asset linked to an IT asset."""
        if 'it_asset_id' not in self._fields:
            return
        fas = self.sudo().search([('it_asset_id', '!=', False)])
        fas._fa_push_to_it()
        _logger.info('[FA sync] %s fixed asset(s) pushed to their IT asset', len(fas))
