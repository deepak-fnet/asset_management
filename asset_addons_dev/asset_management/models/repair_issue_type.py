from odoo import api, fields, models


class RepairIssueType(models.Model):
    """Configurable repair issue types (Software, Hardware, AC Service, ...).
    Each one says which built-in behaviour it follows - hardware needs a
    handover mode, general (non-IT) needs a repair cost on close - so new
    types can be added without code."""
    _name = 'repair.issue.type'
    _description = 'Repair Issue Type'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    behaviour = fields.Selection([
        ('software', 'Software'),
        ('hardware', 'Hardware'),
        ('general', 'General / Non-IT'),
    ], string='Works As', required=True, default='general',
        help="Hardware: handover mode required. General / Non-IT: repair "
             "cost required when closing.")


class RepairManagement(models.Model):
    _inherit = 'repair.management'

    issue_type_id = fields.Many2one('repair.issue.type', string='Issue Type', tracking=True)

    @api.onchange('issue_type_id')
    def _onchange_issue_type_id(self):
        if self.issue_type_id:
            self.issue_type = self.issue_type_id.behaviour
            self._onchange_issue_type()

    def _sync_issue_type_vals(self, vals):
        # issue_type (the behaviour key all the repair rules use) always
        # follows the configured type; code that only passes the old key
        # gets the first configured type for it.
        if vals.get('issue_type_id'):
            vals['issue_type'] = self.env['repair.issue.type'].browse(vals['issue_type_id']).behaviour
        elif vals.get('issue_type') and 'issue_type_id' not in vals:
            vals['issue_type_id'] = self.env['repair.issue.type'].search(
                [('behaviour', '=', vals['issue_type'])], limit=1).id
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._sync_issue_type_vals(dict(v)) for v in vals_list])

    def write(self, vals):
        return super().write(self._sync_issue_type_vals(dict(vals)))
