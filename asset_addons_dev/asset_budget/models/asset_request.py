from odoo import _, api, fields, models


class AssetRequest(models.Model):
    _inherit = 'asset.request'

    department_id = fields.Many2one(
        'hr.department', string='Department', compute='_compute_department_id', store=True, readonly=False,
        help="Budget department: the requester's department, editable.")
    budget_warning = fields.Html(compute='_compute_budget_warning')
    budget_line_ids = fields.One2many('asset.budget.line', 'request_id', string='Budget Movements')

    @api.depends('requested_by')
    def _compute_department_id(self):
        for rec in self:
            if not rec.department_id:
                rec.department_id = rec.requested_by.sudo().employee_id.department_id

    @api.depends('line_ids.budget_id', 'line_ids.estimated_amount', 'department_id')
    def _compute_budget_warning(self):
        for rec in self:
            rows = []
            per_budget = {}
            for line in rec.line_ids.filtered('product_id'):
                if not line.budget_id:
                    rows.append(_("No budget for <b>%(cat)s</b> / %(dept)s.",
                                  cat=line.asset_category_id.complete_name or '-',
                                  dept=rec.department_id.name or _('no department')))
                    continue
                per_budget[line.budget_id] = per_budget.get(line.budget_id, 0.0) + line.estimated_amount
            for budget, est in per_budget.items():
                if est > budget.balance_amount:
                    rows.append(_("<b>%(b)s</b>: estimate %(e)s exceeds the balance %(bal)s.",
                                  b=budget.name, e=round(est, 2), bal=round(budget.balance_amount, 2)))
            rec.budget_warning = '<br/>'.join(rows) if rows else False


class AssetRequestLine(models.Model):
    _inherit = 'asset.request.line'

    budget_id = fields.Many2one('asset.budget', string='Budget', compute='_compute_budget', compute_sudo=True)
    estimated_amount = fields.Monetary('Estimate', compute='_compute_budget', compute_sudo=True,
                                       currency_field='budget_currency_id',
                                       help="Product cost x quantity; the real amount is checked on the RFQ.")
    budget_balance = fields.Monetary('Budget Balance', compute='_compute_budget', compute_sudo=True,
                                     currency_field='budget_currency_id')
    budget_currency_id = fields.Many2one('res.currency', compute='_compute_budget', compute_sudo=True)

    @api.depends('product_id', 'asset_category_id', 'quantity', 'request_id.department_id',
                 'request_id.request_date')
    def _compute_budget(self):
        Budget = self.env['asset.budget']
        for line in self:
            req = line.request_id
            line.budget_currency_id = req.company_id.currency_id if 'company_id' in req._fields \
                else self.env.company.currency_id
            cat = line.asset_category_id or line.product_id.categ_id
            line.budget_id = Budget._find(cat, req.department_id, req.request_date or fields.Date.today()) \
                if cat and req.department_id else False
            line.estimated_amount = (line.product_id.standard_price or 0.0) * (line.quantity or 0.0)
            line.budget_balance = line.budget_id.balance_amount if line.budget_id else 0.0
