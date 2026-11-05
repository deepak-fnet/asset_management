"""Fixed asset = single source of truth.

Purchase / procurement, depreciation, warranty / AMC, employee assignment and
its history, maintenance and the audit log live on the fixed asset only. The
IT asset (the agent's record, linked from the fixed asset's it_asset_id)
carries agent data only; it SHOWS the fixed asset's business data live
(fixed_asset_card, computed, never stored) instead of keeping a copy.

So nothing is copied onto the IT asset any more: general_asset's Submit /
Update keep working for network devices and cameras but skip asset.asset.
"""
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.tools.misc import format_date, formatLang


class AssetAssetFixedSource(models.Model):
    _inherit = 'asset.asset'

    fixed_asset_id = fields.Many2one(
        'asset.asset', string='Fixed Asset', compute='_compute_fixed_asset_id',
        help="Fixed asset this IT asset is linked to (its IT Asset field). Purchase, "
             "depreciation, warranty, assignment and maintenance live there.")
    fixed_asset_card = fields.Html('Fixed Asset Summary', compute='_compute_fixed_asset_card', sanitize=False)

    # Submit / Update in general_asset: nothing goes onto the IT asset.
    _UPDATE_SYNC_MAP = {'asset.asset': {}, 'asset.network.device': {}, 'asset.camera': {}}
    _UPDATE_HISTORY_MAP = {}

    def _compute_fixed_asset_id(self):
        if 'it_asset_id' not in self._fields:
            self.fixed_asset_id = False
            return
        data = {r.it_asset_id.id: r.id for r in self.sudo().search([('it_asset_id', 'in', self.ids)])}
        for rec in self:
            rec.fixed_asset_id = data.get(rec.id, False)

    def _sync_submit_details(self, record):
        if record._name == 'asset.asset':
            return {}
        return super()._sync_submit_details(record)

    def _mirror_submit_history(self, record):
        if record._name == 'asset.asset':
            return 0
        return super()._mirror_submit_history(record)

    def action_open_fixed_asset(self):
        self.ensure_one()
        fa = self.fixed_asset_id
        if not fa:
            return False
        view = self.env.ref('general_asset.view_asset_general_form', raise_if_not_found=False)
        return {'type': 'ir.actions.act_window', 'res_model': 'asset.asset', 'res_id': fa.id,
                'view_mode': 'form', 'views': [(view.id if view else False, 'form')], 'target': 'current'}

    @api.depends_context('uid')
    def _compute_fixed_asset_card(self):
        for rec in self:
            fa = rec.fixed_asset_id.sudo()
            if not fa:
                rec.fixed_asset_card = Markup(
                    '<div class="fa-card fa-empty"><i class="fa fa-unlink"></i> %s</div>') % _(
                    "Not linked to a fixed asset yet. Open the fixed asset and select this record "
                    "in its IT Asset field - purchase, warranty, assignment and depreciation are shown from there.")
                continue
            rec.fixed_asset_card = rec._fa_card_html(fa)

    def _fa_card_html(self, fa):
        env = self.env

        def g(f):
            return fa[f] if f in fa._fields else False

        def d(v):
            return format_date(env, v) if v else '—'

        def money(v):
            cur = g('currency_id') or env.company.currency_id
            return formatLang(env, v or 0.0, currency_obj=cur)

        def sel(f):
            v = g(f)
            return dict(fa._fields[f]._description_selection(env)).get(v, v) if v else '—'

        def name(f):
            v = g(f)
            return v.display_name if v else '—'

        def item(label, value, cls=''):
            return Markup('<div class="fa-i %s"><span>%s</span><b>%s</b></div>') % (cls, label, value)

        def block(icon, title, items):
            return Markup('<div class="fa-b"><h6><i class="fa %s"></i> %s</h6>%s</div>') % (
                icon, title, Markup('').join(items))

        w = g('warranty_status')
        wcls = {'active': 'ok', 'expired': 'bad'}.get(w, '')
        # depreciation: book value from the latest posted / computed line when present
        book = None
        for f in ('book_value', 'current_value', 'net_book_value', 'value_residual'):
            if f in fa._fields and fa[f]:
                book = fa[f]
                break
        blocks = [
            block('fa-user', _('Assignment'), [
                item(_('Employee'), escape(name('assigned_employee_id'))),
                item(_('Department'), escape(name('department_id'))),
                item(_('Since'), d(g('assignment_date'))),
                item(_('Location'), escape(name('location_id'))),
            ]),
            block('fa-shopping-bag', _('Procurement'), [
                item(_('Purchased'), d(g('purchase_date'))),
                item(_('Cost'), money(g('purchase_cost'))),
                item(_('Vendor'), escape(name('procurement_vendor_id'))),
                item(_('Invoice'), escape(g('invoice_ref') or '—')),
            ] + ([item(_('Book value'), money(book))] if book is not None else [])),
            block('fa-shield', _('Warranty & AMC'), [
                item(_('Warranty'), escape(sel('warranty_status')), wcls),
                item(_('Valid till'), d(g('warranty_end_date'))),
                item(_('AMC'), escape(sel('amc_status'))),
                item(_('AMC till'), d(g('amc_expiry_date') or g('amc_end_date'))),
            ]),
            block('fa-wrench', _('Maintenance'), [
                item(_('Status'), escape(sel('state'))),
                item(_('Last'), d(g('last_maintenance_date'))),
                item(_('Next'), d(g('next_maintenance_date'))),
                item(_('Condition'), escape(sel('condition'))),
            ]),
        ]
        return Markup(
            '<div class="fa-card"><div class="fa-head"><i class="fa fa-link"></i> %s <b>%s</b>'
            '<small>%s</small></div><div class="fa-grid">%s</div></div>') % (
            _('From fixed asset'), fa.display_name,
            _('read-only here - purchase, depreciation, warranty, assignment and audit log are kept on the fixed asset'),
            Markup('').join(blocks))
