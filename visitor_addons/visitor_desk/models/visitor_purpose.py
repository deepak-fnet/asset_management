import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

HEX_RE = re.compile(r'^#[0-9a-fA-F]{6}$')


def _mix_with_white(hex_color, ratio):
    """Lighter tone of hex_color, used as the gradient end on badges and labels."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    r, g, b = (round(c + (255 - c) * ratio) for c in (r, g, b))
    return '#%02x%02x%02x' % (r, g, b)


class VisitorPurpose(models.Model):
    _name = 'visitor.purpose'
    _description = 'Purpose of Visit'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    icon = fields.Char(required=True, default='fa-handshake-o',
                       help='Font Awesome 4 icon class, e.g. fa-truck')
    color = fields.Char(required=True, default='#4f46e5',
                        help='Colour used on the desk chips and the printed visitor label.')
    color_end = fields.Char(compute='_compute_color_end')
    sequence = fields.Integer(default=10)
    default_hours = fields.Float('Default Stay (h)', default=2.0,
                                help='Pre-filled expected stay when this purpose is chosen.')
    active = fields.Boolean(default=True)
    visit_count = fields.Integer(compute='_compute_visit_count')

    _name_uniq = models.Constraint('UNIQUE(name)', 'This purpose already exists.')

    @api.depends('color')
    def _compute_color_end(self):
        for rec in self:
            rec.color_end = _mix_with_white(rec.color, 0.3) if rec.color and HEX_RE.match(rec.color) else rec.color

    def _compute_visit_count(self):
        counts = dict(self.env['visitor.visit']._read_group(
            [('purpose_id', 'in', self.ids)], ['purpose_id'], ['__count']))
        for rec in self:
            rec.visit_count = counts.get(rec, 0)

    @api.constrains('color')
    def _check_color(self):
        for rec in self:
            if not HEX_RE.match(rec.color or ''):
                raise ValidationError(_('Colour must be a hex value like #4f46e5.'))

    @api.constrains('icon')
    def _check_icon(self):
        for rec in self:
            if not re.match(r'^fa-[a-z0-9-]+$', rec.icon or ''):
                raise ValidationError(_('Icon must be a Font Awesome class like fa-truck.'))

    def unlink(self):
        if self.env['visitor.visit'].search_count([('purpose_id', 'in', self.ids)], limit=1):
            raise ValidationError(_('This purpose is used on visits: archive it instead of deleting it.'))
        return super().unlink()

    @api.model
    def desk_purposes(self):
        return self.search_read([], ['name', 'icon', 'color', 'color_end', 'default_hours'])
