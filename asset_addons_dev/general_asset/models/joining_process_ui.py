"""Asset Assign Process form - photo and progress shown next to the title."""
from odoo import fields, models


class AssetJoiningProcessUI(models.Model):
    _inherit = 'asset.joining.process'

    jp_avatar = fields.Image(related='employee_id.avatar_128', string='Photo')
    jp_progress = fields.Float(compute='_compute_jp_progress', string='Assigned %')
    jp_progress_text = fields.Char(compute='_compute_jp_progress')

    # request_line_ids comes from asset_purchase (not a dependency), so no @depends
    def _compute_jp_progress(self):
        for rec in self:
            lines = rec['request_line_ids'] if 'request_line_ids' in rec._fields else rec.env['ir.model']
            req = sum(lines.mapped('quantity')) if lines else 0
            got = sum(lines.mapped('assigned_qty')) if lines else 0
            rec.jp_progress = (got * 100.0 / req) if req else 0.0
            rec.jp_progress_text = '%d of %d assigned' % (got, req)
