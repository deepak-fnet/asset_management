from odoo import api, fields, models

VISITOR_GROUPS = 'visitor_desk.group_visitor_reception,visitor_desk.group_visitor_security'


def normalize_mobile(mobile):
    """Keep digits only and compare on the last 10 so +91 / spaces don't create duplicates."""
    digits = ''.join(ch for ch in (mobile or '') if ch.isdigit())
    return digits[-10:]


class VisitorVisitor(models.Model):
    _name = 'visitor.visitor'
    _description = 'Visitor'
    _inherit = ['mail.thread']
    _order = 'last_visit_date desc, id desc'

    name = fields.Char(required=True, tracking=True)
    mobile = fields.Char(required=True, tracking=True)
    mobile_key = fields.Char(compute='_compute_mobile_key', store=True, index=True)
    company_name = fields.Char('Company')
    email = fields.Char()
    photo = fields.Image(max_width=1024, max_height=1024, groups=VISITOR_GROUPS)
    photo_128 = fields.Image('Photo (small)', related='photo', max_width=128, max_height=128, store=True, groups=VISITOR_GROUPS)
    is_blocked = fields.Boolean('Blocked', tracking=True,
                                help='Security will be warned when this visitor tries to check in.')
    block_reason = fields.Char()
    visit_ids = fields.One2many('visitor.visit', 'visitor_id', string='Visits')
    visit_count = fields.Integer(compute='_compute_visit_stats', store=True)
    last_visit_date = fields.Datetime(compute='_compute_visit_stats', store=True)

    _mobile_key_uniq = models.Constraint('UNIQUE(mobile_key)', 'A visitor with this mobile number already exists.')

    @api.depends('mobile')
    def _compute_mobile_key(self):
        for rec in self:
            rec.mobile_key = normalize_mobile(rec.mobile) or False

    @api.depends('visit_ids', 'visit_ids.registered_on')
    def _compute_visit_stats(self):
        for rec in self:
            rec.visit_count = len(rec.visit_ids)
            rec.last_visit_date = max(rec.visit_ids.mapped('registered_on'), default=False)

    @api.model
    def lookup_by_mobile(self, mobile):
        """Used by the desk to greet returning visitors and pre-fill the form."""
        key = normalize_mobile(mobile)
        if len(key) < 10:
            return False
        visitor = self.search([('mobile_key', '=', key)], limit=1)
        if not visitor:
            return False
        last = visitor.visit_ids[:1]
        active = visitor.visit_ids.filtered(lambda v: v.state in ('registered', 'checked_in'))[:1]
        return {
            'id': visitor.id,
            'name': visitor.name,
            'company_name': visitor.company_name or '',
            'email': visitor.email or '',
            'visit_count': visitor.visit_count,
            'has_photo': bool(visitor.photo_128),
            'is_blocked': visitor.is_blocked,
            'block_reason': visitor.block_reason or '',
            'last_purpose_id': last.purpose_id.id or False,
            'last_host_id': last.host_id.id or False,
            'active_visit': active._desk_card() if active else False,
            'active_message': active._active_visit_message() if active else '',
        }
