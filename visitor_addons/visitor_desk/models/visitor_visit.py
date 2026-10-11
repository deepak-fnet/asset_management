import base64
import io
import secrets
from datetime import timedelta

import qrcode

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .visitor_visitor import VISITOR_GROUPS, normalize_mobile

ACTIVE_STATES = ('registered', 'checked_in')
# Fields that describe who/why; frozen once the visit is closed so history can't be rewritten
LOCKED_FIELDS = {'visitor_name', 'mobile', 'company_name', 'email', 'purpose_id', 'purpose_note',
                 'host_id', 'photo', 'visitor_id', 'check_in', 'check_out'}
# Ignore a second scan of the same pass right after check-in (double beep at the gate)
SCAN_COOLDOWN = timedelta(seconds=60)


class VisitorVisit(models.Model):
    _name = 'visitor.visit'
    _description = 'Visit'
    _inherit = ['mail.thread']
    _order = 'registered_on desc, id desc'

    name = fields.Char('Visitor ID', default=lambda self: _('New'), readonly=True, copy=False)
    pass_token = fields.Char(readonly=True, copy=False, index=True,
                             default=lambda self: secrets.token_hex(4).upper())
    visitor_id = fields.Many2one('visitor.visitor', readonly=True, index=True, ondelete='restrict')
    visitor_name = fields.Char('Visitor Name', required=True, tracking=True)
    mobile = fields.Char(required=True)
    company_name = fields.Char('Visitor Company')
    email = fields.Char()
    purpose_id = fields.Many2one('visitor.purpose', 'Purpose', required=True, tracking=True, ondelete='restrict',
                                 default=lambda self: self.env['visitor.purpose'].search([], limit=1))
    purpose_note = fields.Char('Purpose Details')
    host_id = fields.Many2one('res.users', 'Person to Meet', tracking=True)
    photo = fields.Image(max_width=1024, max_height=1024, groups=VISITOR_GROUPS)
    photo_consent = fields.Boolean('Photo Consent',
                                   help='Visitor was informed why the photo is captured and agreed to it.')
    expected_hours = fields.Float('Expected Stay (h)', default=2.0)
    state = fields.Selection([
        ('registered', 'Registered'),
        ('checked_in', 'Checked In'),
        ('checked_out', 'Checked Out'),
        ('cancelled', 'Cancelled'),
    ], default='registered', required=True, tracking=True, index=True)
    registered_on = fields.Datetime(default=fields.Datetime.now, readonly=True)
    registered_by = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    check_in = fields.Datetime(readonly=True, tracking=True)
    check_in_by = fields.Many2one('res.users', readonly=True)
    check_out = fields.Datetime(readonly=True, tracking=True)
    check_out_by = fields.Many2one('res.users', readonly=True)
    duration = fields.Float('Duration (h)', compute='_compute_duration', store=True)
    is_overstay = fields.Boolean(compute='_compute_is_overstay')
    visitor_blocked = fields.Boolean(related='visitor_id.is_blocked')
    visitor_visit_count = fields.Integer(related='visitor_id.visit_count')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)

    _pass_token_uniq = models.Constraint('UNIQUE(pass_token)', 'Pass code must be unique.')

    @api.depends('check_in', 'check_out')
    def _compute_duration(self):
        for rec in self:
            if rec.check_in and rec.check_out:
                rec.duration = (rec.check_out - rec.check_in).total_seconds() / 3600
            else:
                rec.duration = 0.0

    def _compute_is_overstay(self):
        now = fields.Datetime.now()
        for rec in self:
            rec.is_overstay = bool(
                rec.state == 'checked_in' and rec.check_in and rec.expected_hours
                and now > rec.check_in + timedelta(hours=rec.expected_hours)
            )

    @api.constrains('mobile')
    def _check_mobile(self):
        for rec in self:
            if len(normalize_mobile(rec.mobile)) < 10:
                raise ValidationError(_('Please enter a valid 10-digit mobile number.'))

    @api.constrains('visitor_id', 'state')
    def _check_single_active_visit(self):
        for rec in self.filtered(lambda v: v.state in ACTIVE_STATES and v.visitor_id):
            other = self.search([
                ('visitor_id', '=', rec.visitor_id.id),
                ('state', 'in', ACTIVE_STATES),
                ('id', '!=', rec.id),
            ], limit=1)
            if other:
                raise ValidationError(other._active_visit_message())

    def _active_visit_message(self):
        self.ensure_one()
        if self.state == 'checked_in':
            since = fields.Datetime.context_timestamp(self, self.check_in).strftime('%I:%M %p')
            return _('%(name)s is already inside since %(time)s (%(ref)s). Check them out before registering again.',
                     name=self.visitor_name, time=since, ref=self.name)
        return _('%(name)s is already registered and waiting at the gate (%(ref)s). '
                 'Check them in or cancel that pass instead of creating a new one.',
                 name=self.visitor_name, ref=self.name)

    def _is_expired(self):
        """A pass that was never used is only valid on the day it was registered."""
        self.ensure_one()
        if self.state != 'registered' or not self.registered_on:
            return False
        registered_day = fields.Datetime.context_timestamp(self, self.registered_on).date()
        return registered_day < fields.Date.context_today(self)

    @api.constrains('photo', 'photo_consent')
    def _check_photo_consent(self):
        for rec in self.sudo():
            if rec.photo and not rec.photo_consent:
                raise ValidationError(_('Please confirm the visitor agreed to the photo before saving it.'))

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('visitor.visit') or _('New')
            if not vals.get('visitor_id') and vals.get('mobile'):
                vals['visitor_id'] = self._find_or_create_visitor(vals).id
        visits = super().create(vals_list)
        visits._sync_visitor_photo()
        return visits

    def write(self, vals):
        if (LOCKED_FIELDS & vals.keys() and not self.env.su
                and not self.env.user.has_group('visitor_desk.group_visitor_manager')):
            closed = self.filtered(lambda v: v.state in ('checked_out', 'cancelled'))
            if closed:
                raise UserError(_('%s is closed and can no longer be edited.', closed[0].name))
        res = super().write(vals)
        if 'photo' in vals:
            self._sync_visitor_photo()
        return res

    @api.model
    def _find_or_create_visitor(self, vals):
        Visitor = self.env['visitor.visitor']
        visitor = Visitor.search([('mobile_key', '=', normalize_mobile(vals['mobile']))], limit=1)
        details = {
            'name': vals.get('visitor_name'),
            'company_name': vals.get('company_name'),
            'email': vals.get('email'),
        }
        details = {k: v for k, v in details.items() if v}
        if visitor:
            visitor.write(details)
            return visitor
        return Visitor.create(dict(details, mobile=vals['mobile']))

    def _sync_visitor_photo(self):
        # The visitor card always shows the latest captured face; each visit keeps its own snapshot.
        for rec in self.sudo():
            if rec.photo and rec.visitor_id:
                rec.visitor_id.photo = rec.photo

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_check_in(self):
        for rec in self:
            if rec.state != 'registered':
                raise UserError(_('%s is not waiting for check-in.', rec.name))
            if rec._is_expired():
                raise UserError(_('Pass %s has expired (it was valid only on the day it was issued). '
                                  'Please register the visitor again.', rec.name))
            if rec.visitor_blocked and not self.env.context.get('force_check_in'):
                raise UserError(_('%(name)s is blocked: %(reason)s',
                                  name=rec.visitor_name, reason=rec.visitor_id.block_reason or '-'))
            rec.write({'state': 'checked_in', 'check_in': fields.Datetime.now(), 'check_in_by': self.env.uid})
            rec._notify_host()
        return True

    def action_check_out(self):
        for rec in self:
            if rec.state != 'checked_in':
                raise UserError(_('%s is not inside, so they cannot be checked out.', rec.name))
            rec.write({'state': 'checked_out', 'check_out': fields.Datetime.now(), 'check_out_by': self.env.uid})
        return True

    def action_cancel(self):
        if self.filtered(lambda v: v.state == 'checked_in'):
            raise UserError(_('A visitor who is inside cannot be cancelled: check them out instead.'))
        self.filtered(lambda v: v.state == 'registered').write({'state': 'cancelled'})
        return True

    def action_print_pass(self):
        return self.env.ref('visitor_desk.action_report_visitor_pass').report_action(self)

    def _pass_qr_data_uri(self):
        # Embedded so wkhtmltopdf doesn't need to fetch the QR over HTTP
        self.ensure_one()
        buf = io.BytesIO()
        qrcode.make(self.pass_token, box_size=8, border=2).save(buf, format='PNG')
        return 'data:image/png;base64,%s' % base64.b64encode(buf.getvalue()).decode()

    def _pass_colors(self):
        self.ensure_one()
        return (self.purpose_id.color or '#475569', self.purpose_id.color_end or '#64748b')

    def _notify_host(self):
        # Informational only: the host doesn't have to do anything in this flow.
        self.ensure_one()
        if self.host_id:
            self.message_post(
                body=_('%(visitor)s from %(company)s has checked in and is on the way to meet you.',
                       visitor=self.visitor_name, company=self.company_name or _('-')),
                partner_ids=self.host_id.partner_id.ids,
                message_type='notification',
                subtype_xmlid='mail.mt_note',
            )

    # ------------------------------------------------------------------
    # Desk (OWL client action) API
    # ------------------------------------------------------------------
    def _desk_card(self):
        self.ensure_one()
        return {
            'id': self.id,
            'name': self.name,
            'pass_token': self.pass_token,
            'visitor_name': self.visitor_name,
            'company_name': self.company_name or '',
            'mobile': self.mobile,
            'purpose': self.purpose_id.name or '',
            'purpose_icon': self.purpose_id.icon or 'fa-circle',
            'purpose_color': self.purpose_id.color or '#4f46e5',
            'host': self.host_id.name or '',
            'state': self.state,
            'registered_on': fields.Datetime.to_string(self.registered_on),
            'check_in': fields.Datetime.to_string(self.check_in) if self.check_in else False,
            'check_out': fields.Datetime.to_string(self.check_out) if self.check_out else False,
            'expected_hours': self.expected_hours,
            'blocked': self.visitor_blocked,
            'expired': self._is_expired(),
            'write_date': fields.Datetime.to_string(self.write_date),
        }

    @api.model
    def desk_data(self):
        now = fields.Datetime.now()
        today_start = fields.Datetime.to_datetime(fields.Date.context_today(self))
        # context_today is local; shift back to UTC so "today" matches the user's day
        tz_offset = fields.Datetime.context_timestamp(self, now).utcoffset() or timedelta()
        today_start -= tz_offset

        inside = self.search([('state', '=', 'checked_in')], order='check_in asc')
        waiting = self.search([('state', '=', 'registered'), ('registered_on', '>=', today_start)])
        today = self.search([('registered_on', '>=', today_start), ('state', '!=', 'cancelled')])
        out_today = today.filtered(lambda v: v.state == 'checked_out')
        avg = sum(out_today.mapped('duration')) / len(out_today) if out_today else 0.0
        recent = self.search([('write_date', '>=', today_start), ('state', '!=', 'registered')],
                             order='write_date desc', limit=8)
        return {
            'kpi': {
                'inside': len(inside),
                'waiting': len(waiting),
                'today': len(today),
                'checked_out': len(out_today),
                'avg_stay': round(avg, 1),
                'overstay': len(inside.filtered('is_overstay')),
                'returning': len(today.filtered(lambda v: v.visitor_id.visit_count > 1)),
            },
            'inside': [v._desk_card() for v in inside],
            'waiting': [v._desk_card() for v in waiting],
            'recent': [v._desk_card() for v in recent],
            'server_now': fields.Datetime.to_string(now),
        }

    @api.model
    def desk_register(self, vals, check_in=False):
        allowed = {'visitor_name', 'mobile', 'company_name', 'email', 'purpose_id', 'purpose_note',
                   'host_id', 'photo', 'photo_consent', 'expected_hours'}
        visit = self.create({k: v for k, v in vals.items() if k in allowed})
        if check_in:
            visit.action_check_in()
        return visit._desk_card()

    @api.model
    def desk_scan(self, code):
        """Security scans the QR (pass token) or types the Visitor ID: toggles in/out."""
        code = (code or '').strip()
        visit = self.search(['|', ('pass_token', '=', code.upper()), ('name', '=', code)], limit=1)
        if not visit:
            return {'status': 'not_found', 'code': code}
        if visit.state == 'registered':
            if visit._is_expired():
                return {'status': 'expired', 'visit': visit._desk_card()}
            if visit.visitor_blocked:
                return {'status': 'blocked', 'visit': visit._desk_card(),
                        'reason': visit.visitor_id.block_reason or ''}
            visit.action_check_in()
            return {'status': 'checked_in', 'visit': visit._desk_card()}
        if visit.state == 'checked_in':
            if visit.check_in and fields.Datetime.now() - visit.check_in < SCAN_COOLDOWN:
                return {'status': 'too_soon', 'visit': visit._desk_card()}
            visit.action_check_out()
            return {'status': 'checked_out', 'visit': visit._desk_card()}
        return {'status': visit.state, 'visit': visit._desk_card()}

    @api.model
    def _cron_expire_passes(self):
        """Unused passes from previous days are cancelled so they can't be reused."""
        stale = self.sudo().search([('state', '=', 'registered'),
                                    ('registered_on', '<', fields.Datetime.now() - timedelta(hours=12))])
        stale.filtered(lambda v: v._is_expired()).write({'state': 'cancelled'})

    # ------------------------------------------------------------------
    # Privacy
    # ------------------------------------------------------------------
    @api.model
    def _cron_purge_photos(self):
        days = int(self.env['ir.config_parameter'].sudo().get_param('visitor_desk.photo_retention_days', 90))
        if days <= 0:
            return
        cutoff = fields.Datetime.now() - timedelta(days=days)
        self.sudo().search([
            ('state', 'in', ('checked_out', 'cancelled')),
            ('registered_on', '<', cutoff),
            ('photo', '!=', False),
        ]).write({'photo': False})
        self.env['visitor.visitor'].sudo().search([
            ('last_visit_date', '<', cutoff),
            ('photo', '!=', False),
        ]).write({'photo': False})
