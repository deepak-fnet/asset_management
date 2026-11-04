from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ProductCategory(models.Model):
    _inherit = 'product.category'

    account_asset_category_id = fields.Many2one(
        'account.asset.category', string='Accounting Asset Type',
        domain="[('type', '=', 'purchase')]",
        help="Accounts and journal used for the accounting asset created when depreciation is "
             "calculated on an asset of this category. Created automatically the first time.")

    def _bridge_asset_type(self):
        """Accounting asset type for this category, created on first use."""
        self.ensure_one()
        if self.account_asset_category_id:
            return self.account_asset_category_id
        Acc = self.env['account.account'].sudo()
        company = self.env.company

        def acc(code, name, atype):
            a = Acc.search([('code', '=', code)], limit=1)
            return a or Acc.create({'code': code, 'name': name, 'account_type': atype})

        name = (self.name or '').lower()
        asset_acc = Acc.search([('account_type', '=', 'asset_fixed'), (
            'name', 'ilike', 'computer' if any(k in name for k in ('laptop', 'desktop', 'computer')) else
            'furniture' if any(k in name for k in ('chair', 'table', 'furniture')) else
            'vehicle' if 'vehicle' in name else 'equipment')], limit=1) \
            or Acc.search([('account_type', '=', 'asset_fixed')], limit=1)
        journal = self.env['account.journal'].sudo().search(
            [('type', '=', 'general'), ('code', '=', 'MISC'), ('company_id', '=', company.id)], limit=1) \
            or self.env['account.journal'].sudo().search(
                [('type', '=', 'general'), ('company_id', '=', company.id)], limit=1)
        if not (asset_acc and journal):
            raise UserError(_("Set up a fixed asset account and a miscellaneous journal first."))
        atype = self.env['account.asset.category'].sudo().create({
            'name': self.complete_name,
            'type': 'purchase',
            'price': 0.0,
            'account_asset_id': asset_acc.id,
            'account_depreciation_id': acc('101900', 'Accumulated Depreciation', 'asset_fixed').id,
            'account_depreciation_expense_id': acc('620000', 'Depreciation Expense',
                                                   'expense_depreciation').id,
            'journal_id': journal.id,
            'method_time': 'number',
            'method_period': 12,
            'prorata': True,
        })
        self.sudo().account_asset_category_id = atype
        return atype


class AssetAsset(models.Model):
    _inherit = 'asset.asset'

    accounting_asset_id = fields.Many2one('account.asset.asset', string='Accounting Asset',
                                          copy=False, readonly=True)
    accounting_asset_state = fields.Selection(related='accounting_asset_id.state',
                                              string='Accounting Asset Status')

    def action_calculate_depreciation(self):
        res = super().action_calculate_depreciation()
        for rec in self:
            rec._sync_accounting_asset()
        return res

    def _accounting_asset_vals(self, atype):
        self.ensure_one()
        vals = {
            'name': self.display_name,
            'code': (self.asset_code or '')[:32],
            'value': self.purchase_cost,
            'salvage_value': self.salvage_value,
            'date': self.purchase_date,
            'partner_id': self.vendor_id.id if 'vendor_id' in self._fields else False,
            'category_id': atype.id,
            'type': 'purchase',
            'account_asset_id': atype.account_asset_id.id,
            'account_depreciation_id': atype.account_depreciation_id.id,
            'account_depreciation_expense_id': atype.account_depreciation_expense_id.id,
            'journal_id': atype.journal_id.id,
            'account_analytic_id': atype.account_analytic_id.id,
            'method_time': 'number',
            'method_period': 12,          # yearly, like the asset's own schedule
            'prorata': True,              # first year from the purchase date
            'source_asset_id': self.id,
            'note': _("Created from asset %s (Calculate Depreciation).", self.display_name),
        }
        if self.depreciation_method == 'dlm':
            vals.update({
                'method': 'degressive',
                'method_progress_factor': self.depreciation_rate / 100.0,
                # same number of years as the asset's own declining schedule
                'method_number': max(len(self.depreciation_line_ids) - 1, 1),
            })
        else:
            vals.update({'method': 'linear', 'method_number': self.useful_life_years})
        return vals

    def _sync_accounting_asset(self):
        """Create / update the accounting asset and run its Compute Depreciation."""
        self.ensure_one()
        if not self.category_id:
            raise UserError(_("Set the asset category first."))
        Acc = self.env['account.asset.asset'].sudo()
        acc_asset = self.accounting_asset_id.sudo()
        if acc_asset and acc_asset.state != 'draft':
            acc_asset.message_post(body=_(
                "Depreciation recalculated on %s, but this accounting asset is already %s and was "
                "not changed.", self.display_name, dict(acc_asset._fields['state'].selection)[acc_asset.state]))
            return acc_asset
        atype = self.category_id._bridge_asset_type()
        vals = self._accounting_asset_vals(atype)
        if acc_asset:
            # draft: throw away the old board and its draft entries, then rebuild
            moves = acc_asset.depreciation_line_ids.mapped('move_id').filtered(lambda m: m.state == 'draft')
            acc_asset.depreciation_line_ids.write({'move_id': False, 'move_check': False})
            moves.with_context(force_delete=True).unlink()
            acc_asset.depreciation_line_ids.unlink()
            acc_asset.write(vals)
        else:
            acc_asset = Acc.create(vals)
            self.accounting_asset_id = acc_asset
        acc_asset.compute_depreciation_board()
        return acc_asset

    def action_open_accounting_asset(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Accounting Asset'),
            'res_model': 'account.asset.asset', 'view_mode': 'form', 'res_id': self.accounting_asset_id.id,
        }


class AccountAssetAsset(models.Model):
    _inherit = 'account.asset.asset'

    source_asset_id = fields.Many2one('asset.asset', string='Fixed Asset', copy=False, readonly=True,
                                      help="Fixed asset this accounting asset was created from. Its depreciation "
                                           "board follows that asset's schedule (financial year to 31 March).")

    def compute_depreciation_board(self):
        """Linked to a fixed asset: one line per financial year, dated 31 March, with the same
        amounts as the fixed asset's own schedule. Otherwise the kit's standard board."""
        self.ensure_one()
        src = self.source_asset_id
        if not src or not src.depreciation_line_ids:
            return super().compute_depreciation_board()
        lines = self.depreciation_line_ids
        posted = lines.filtered(lambda l: l.move_id.state == 'posted').sorted('sequence')
        rest = lines - posted
        drafts = rest.mapped('move_id').filtered(lambda m: m.state == 'draft')
        rest.write({'move_id': False, 'move_check': False})
        drafts.with_context(force_delete=True).unlink()
        commands = [(2, l.id, False) for l in rest]
        done_years = len(posted)
        cumulative = sum(posted.mapped('amount'))
        # years still open: the fixed asset's schedule, scaled so that posted + open = value - salvage
        # (unchanged schedule -> factor 1; after Modify Depreciation the rest is re-spread)
        open_lines = src.depreciation_line_ids.sorted('id')[done_years:]
        to_depreciate = self.value - self.salvage_value - cumulative
        planned = sum(open_lines.mapped('depreciation_amount'))
        factor = to_depreciate / planned if planned else 0.0
        for seq, line in enumerate(open_lines, start=done_years + 1):
            amount = self.currency_id.round(line.depreciation_amount * factor)
            if seq == done_years + len(open_lines):
                amount = self.currency_id.round(self.value - self.salvage_value - cumulative)
            cumulative += amount
            end_year = int((line.year or '').split('-')[-1])
            commands.append((0, 0, {
                'sequence': seq,
                'name': '%s/%s' % (self.code or '', seq),
                'amount': amount,
                'remaining_value': self.currency_id.round(self.value - self.salvage_value - cumulative),
                'depreciated_value': cumulative,
                'depreciation_date': fields.Date.to_date('%s-03-31' % end_year),
            }))
        self.write({'depreciation_line_ids': commands})
        if self.depreciation_line_ids:
            self._compute_entries(date=max(self.depreciation_line_ids.mapped('depreciation_date')))
        return True


class AccountAssetDepreciationLine(models.Model):
    _inherit = 'account.asset.depreciation.line'

    move_state = fields.Selection(related='move_id.state', string='Entry Status')

    def action_post_entry(self):
        """Post this year's depreciation entry now (creates it first if missing)."""
        for line in self:
            if line.asset_id.state != 'open':
                raise UserError(_("Confirm the accounting asset first."))
            if not line.move_id:
                line.create_move(post_move=False)
            if line.move_id.state == 'draft':
                line.move_id.auto_post = 'no'
                line.move_id._post(soft=False)
        return True


class AssetModify(models.TransientModel):
    _inherit = 'asset.modify'

    def modify(self):
        """Linked to a fixed asset: a new number of years (SLM) also goes to the fixed asset,
        its schedule is recalculated and the board follows it."""
        asset = self.env['account.asset.asset'].browse(self.env.context.get('active_id'))
        src = asset.source_asset_id
        if src and src.depreciation_method == 'slm' and self.method_number != src.useful_life_years:
            src.useful_life_years = self.method_number
            src.depreciation_line_ids.unlink()
            src._calculate_slm()
        return super().modify()


class AssetAssetBoard(models.Model):
    _inherit = 'asset.asset'

    accounting_line_ids = fields.One2many(related='accounting_asset_id.depreciation_line_ids',
                                          string='Depreciation Board')
    accounting_entry_count = fields.Integer(related='accounting_asset_id.entry_count')
    accounting_account_asset_id = fields.Many2one(related='accounting_asset_id.account_asset_id',
                                                  string='Asset Account', readonly=False)
    accounting_account_depreciation_id = fields.Many2one(
        related='accounting_asset_id.account_depreciation_id', string='Accumulated Depreciation Account',
        readonly=False)
    accounting_account_expense_id = fields.Many2one(
        related='accounting_asset_id.account_depreciation_expense_id', string='Depreciation Expense Account',
        readonly=False)
    accounting_journal_id = fields.Many2one(related='accounting_asset_id.journal_id', string='Journal',
                                            readonly=False)

    def _acc(self):
        self.ensure_one()
        if not self.accounting_asset_id:
            raise UserError(_("Click Calculate Depreciation first."))
        return self.accounting_asset_id

    def action_accounting_confirm(self):
        self._acc().validate()

    def action_accounting_modify(self):
        action = self.env['ir.actions.act_window']._for_xml_id('base_accounting_kit.action_asset_modify')
        action['context'] = {'active_id': self._acc().id, 'active_model': 'account.asset.asset'}
        return action

    def action_accounting_entries(self):
        return self._acc().open_entries()

    def action_accounting_set_draft(self):
        self._acc().set_to_draft()
