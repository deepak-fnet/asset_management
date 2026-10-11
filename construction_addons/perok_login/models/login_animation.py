# -*- coding: utf-8 -*-
import json
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# clip field -> what it is for (also the order shown in the form)
CLIP_FIELDS = ['idle_video', 'look_video', 'eyes_close_video', 'eyes_open_video', 'happy_video', 'sad_video']


def _hex_rgb(value):
    m = re.fullmatch(r'#?([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})', (value or '').strip())
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


class PerokLoginAnimation(models.Model):
    _name = 'perok.login.animation'
    _description = 'Login Page Animation'
    _order = 'is_default desc, sequence, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    mascot_type = fields.Selection(
        [('parrot', 'Parrot (built-in)'), ('video', 'Video clips / images'), ('model3d', '3D model (GLB)')],
        string='Type', required=True, default='video')
    is_default = fields.Boolean(string='Default', help='The animation shown on the login page. Only one can be the default.')
    is_system = fields.Boolean(readonly=True, copy=False, help='Built-in animation: cannot be deleted.')

    # each state accepts a video clip (MP4/WebM) or a still image (PNG/WebP, transparent background)
    idle_video = fields.Binary('Idle (loop)', attachment=True,
                               help='Plays in a loop while nothing happens. Required.')
    idle_video_name = fields.Char()
    look_video = fields.Binary('Look at form', attachment=True,
                               help='Optional. Plays once when the email field is focused; the last frame is held.')
    look_video_name = fields.Char()
    eyes_close_video = fields.Binary('Eyes close', attachment=True,
                                     help='Plays once when the password field is focused; the last frame is held.')
    eyes_close_video_name = fields.Char()
    eyes_open_video = fields.Binary('Eyes open', attachment=True,
                                    help='Optional. Plays when the password is made visible (eye icon).')
    eyes_open_video_name = fields.Char()
    happy_video = fields.Binary('Happy (login success)', attachment=True,
                                help='Plays once on a successful login, while Odoo opens.')
    happy_video_name = fields.Char()
    sad_video = fields.Binary('Sad (login failure)', attachment=True,
                              help='Plays once on a wrong login, then back to idle.')
    sad_video_name = fields.Char()
    # playback per clip: speed, and the part that repeats while the state lasts
    # (loop from / to in seconds; both 0 = play once and hold the last frame,
    # "to" 0 = up to the end of the clip; the idle clip always repeats)
    idle_speed = fields.Float('Speed', default=1.0)
    idle_loop_from = fields.Float('Loop from (s)')
    idle_loop_to = fields.Float('Loop to (s)')
    look_speed = fields.Float('Speed', default=1.0)
    look_loop_from = fields.Float('Loop from (s)')
    look_loop_to = fields.Float('Loop to (s)')
    eyes_close_speed = fields.Float('Speed', default=1.0)
    eyes_close_loop_from = fields.Float('Loop from (s)')
    eyes_close_loop_to = fields.Float('Loop to (s)')
    eyes_open_speed = fields.Float('Speed', default=1.0)
    eyes_open_loop_from = fields.Float('Loop from (s)')
    eyes_open_loop_to = fields.Float('Loop to (s)')
    happy_speed = fields.Float('Speed', default=1.0)
    happy_loop_from = fields.Float('Loop from (s)')
    happy_loop_to = fields.Float('Loop to (s)')
    sad_speed = fields.Float('Speed', default=1.0)
    happy_run = fields.Boolean('Run across the page on success',
                               help='The "Happy" clip leaves its panel and moves over the form, off the screen.')
    happy_run_delay = fields.Float('Run starts after (s)', default=0.8,
                                   help='When, in the "Happy" clip, the character starts running.')
    happy_run_speed = fields.Integer('Run speed (px/s)', default=900)
    # an object the clip loses mid-way (e.g. a bag that vanishes): shown dropping
    # to the floor and left behind in the panel while the character runs off
    happy_drop_image = fields.Binary('Dropped item (PNG)', attachment=True)
    happy_drop_time = fields.Float('Drops at (s)', default=1.2)
    happy_drop_rect = fields.Char('Item position in the clip', help='x,y,width,height in clip pixels')

    # 3D character: a Mixamo-rigged GLB with an "idle" clip (optional "walk", "sit")
    model_file = fields.Binary('3D model (GLB)', attachment=True,
                               help='Mixamo-rigged .glb with an "idle" animation; "walk" and "sit" are used when present.')
    model_file_name = fields.Char()
    model_path = fields.Char('Built-in model', help='Static path of a model shipped with the module (used when no file is uploaded).')

    # ---------- page look (used when this record is the default) ----------
    page_bg = fields.Char('Page background', default='#EDD1A9')
    card_bg = fields.Char('Card background', default='#FFFEFC')
    panel_bg = fields.Char('Mascot panel', default='#D3892A')
    heading_color = fields.Char('Heading text', default='#1C1917')
    subtext_color = fields.Char('Sub-heading text', default='#A8A29E')
    label_color = fields.Char('Field labels', default='#78716C')
    input_line_color = fields.Char('Input underline', default='#E7E5E4')
    input_focus_color = fields.Char('Input underline (focused)', default='#1C1917')
    link_color = fields.Char('Links', default='#EF4444')
    button_bg = fields.Char('Button', default='#1C1917')
    button_text = fields.Char('Button text', default='#FFFFFF')
    card_radius = fields.Integer('Card corners (px)', default=28)
    panel_radius = fields.Integer('Panel corners (px)', default=20)
    button_radius = fields.Integer('Button corners (px)', default=24)
    font_family = fields.Selection([
        ('Plus Jakarta Sans', 'Plus Jakarta Sans'), ('Inter', 'Inter'), ('Poppins', 'Poppins'),
        ('Nunito', 'Nunito'), ('Roboto', 'Roboto'), ('Montserrat', 'Montserrat'),
    ], string='Font', default='Plus Jakarta Sans')
    heading_text = fields.Char('Heading', default='Welcome back', translate=True)
    subheading_text = fields.Char('Sub-heading', default='What are you selling today ?', translate=True)
    show_reset_link = fields.Boolean('Show "Reset My Password"', default=True)
    show_footer = fields.Boolean('Show "Manage Databases / Powered by"', default=True)

    _THEME_VARS = [
        ('page_bg', '--pk-page-bg'), ('card_bg', '--pk-card-bg'), ('panel_bg', '--pk-panel-bg'),
        ('heading_color', '--pk-heading'), ('subtext_color', '--pk-subtext'), ('label_color', '--pk-label'),
        ('input_line_color', '--pk-input-line'), ('input_focus_color', '--pk-input-focus'),
        ('link_color', '--pk-link'), ('button_bg', '--pk-btn-bg'), ('button_text', '--pk-btn-text'),
    ]

    def action_reset_theme(self):
        fnames = [f for f, _v in self._THEME_VARS] + ['card_radius', 'panel_radius', 'button_radius', 'font_family']
        defaults = self.default_get(fnames)
        defaults.update({f: False for f, _r, _c in self._BG_IMAGES})
        defaults.update({'page_bg_video': False, 'page_bg_video_name': False})
        defaults.update(self.default_get(['page_bg_opacity', 'z_page', 'z_card']))
        defaults.update({'page_bg_transparent': False, 'card_bg_transparent': False,
                         'panel_bg_transparent': False, 'button_bg_transparent': False})
        defaults.update(self.default_get(['card_width', 'card_height', 'card_split', 'panel_width', 'panel_height',
                                          'mascot_fit', 'z_card_video', 'z_panel', 'z_form', 'card_video_fit',
                                          'card_video_position', 'panel_video_fit', 'panel_video_position']))
        defaults.update({'card_bg_video': False, 'card_bg_video_name': False,
                         'panel_bg_video': False, 'panel_bg_video_name': False, 'keep_in_panel': False})
        defaults.update({'card_bg_image': False, 'card_opacity': 100, 'card_blur': 0, 'panel_opacity': 100})
        self.write(defaults)
        return True

    # ---------- sizes, videos and layers ----------
    card_width = fields.Integer('Card width (px)', default=980)
    card_height = fields.Integer('Card height (px)', default=600, help='Minimum height of the card.')
    card_split = fields.Integer('Mascot column (%)', default=48, help='Share of the card taken by the mascot side.')
    card_bg_video = fields.Binary('Card background video', attachment=True,
                                  help='MP4 or WebM, played muted in a loop inside the card, behind its content.')
    card_bg_video_name = fields.Char()
    panel_width = fields.Integer('Panel width (%)', default=80, help='Of the mascot column.')
    panel_height = fields.Integer('Panel height (%)', default=100, help='Of the card height, centred.')
    panel_bg_video = fields.Binary('Panel background video', attachment=True,
                                   help='MP4 or WebM, played muted in a loop inside the mascot panel, behind the mascot.')
    panel_bg_video_name = fields.Char()
    mascot_fit = fields.Selection([
        ('free', 'Free: stands out of the panel'),
        ('contain', 'Inside the panel: whole video visible'),
        ('cover', 'Fill the panel: cropped to the panel'),
    ], string='Video fit', default='free', required=True,
        help='Use "Fill the panel" for clips that have their own background (not transparent).')
    card_video_fit = fields.Selection([
        ('contain', 'Whole video (no crop)'),
        ('cover', 'Fill (cropped, zoomed in)'),
    ], string='Card video fit', default='contain', required=True)
    card_video_position = fields.Selection([('left', 'Left'), ('center', 'Centre'), ('right', 'Right')], string='Card video position', default='left', required=True)
    panel_video_fit = fields.Selection([
        ('contain', 'Whole video (no crop)'),
        ('cover', 'Fill (cropped, zoomed in)'),
    ], string='Panel video fit', default='cover', required=True)
    panel_video_position = fields.Selection([('left', 'Left'), ('center', 'Centre'), ('right', 'Right')], string='Panel video position', default='center', required=True)
    z_card_video = fields.Integer('Card video layer', default=0)
    z_panel = fields.Integer('Panel & mascot layer', default=2)
    z_form = fields.Integer('Form & texts layer', default=3)

    keep_in_panel = fields.Boolean('Keep the mascot inside the panel',
                                   help='Centres the mascot in its panel and cuts it at the panel edges, so wide '
                                        'movements (dancing, waving) never reach the card or the form.')

    # see-through card / panel (100 = solid); a blurred card reads like frosted glass
    card_opacity = fields.Integer('Card opacity (%)', default=100)
    card_blur = fields.Integer('Card blur (px)', default=0,
                               help='Blurs what shows through a see-through card (frosted glass).')
    panel_opacity = fields.Integer('Panel opacity (%)', default=100)

    # optional pictures over the background colours (cover, centred)
    page_bg_image = fields.Binary('Page background image', attachment=True,
                                  help='PNG, JPG, WebP or an animated GIF.')
    page_bg_video = fields.Binary('Page background video', attachment=True,
                                  help='MP4 or WebM, played muted in a loop behind the page (covers the image).')
    page_bg_video_name = fields.Char()
    card_bg_image = fields.Binary('Card background image', attachment=True)
    panel_bg_image = fields.Binary('Mascot panel image', attachment=True)

    # transparent background colours
    page_bg_transparent = fields.Boolean('Transparent page colour')
    card_bg_transparent = fields.Boolean('Transparent card colour')
    panel_bg_transparent = fields.Boolean('Transparent panel colour')
    button_bg_transparent = fields.Boolean('Transparent button')
    # page picture / video layer
    page_bg_opacity = fields.Integer('Page image / video opacity (%)', default=100)
    z_page = fields.Integer('Page image / video layer', default=0)
    z_card = fields.Integer('Card layer', default=1)

    _BG_IMAGES = [
        # field, CSS rule the picture goes on, rules made transparent so it shows
        # (the page picture is its own fixed layer, see the template; the card's
        # picture goes on its ::before layer, see login.css)
        ('panel_bg_image', 'body.pk-body .pk-stage__bg', []),
    ]

    def _login_theme_css(self):
        """CSS variable overrides for the login page (only safe values)."""
        self.ensure_one()
        decls = []
        see_through = {'page_bg': self.page_bg_transparent, 'card_bg': self.card_bg_transparent,
                       'panel_bg': self.panel_bg_transparent, 'button_bg': self.button_bg_transparent}
        for fname, var in self._THEME_VARS:
            if see_through.get(fname):
                decls.append('%s: transparent;' % var)
                continue
            val = (self[fname] or '').strip()
            if re.fullmatch(r'#[0-9A-Fa-f]{3,8}|rgba?\([0-9.,\s%]+\)', val):
                decls.append('%s: %s;' % (var, val))
        for fname, var in [('card_radius', '--pk-card-radius'), ('panel_radius', '--pk-panel-radius'),
                           ('button_radius', '--pk-btn-radius')]:
            decls.append('%s: %dpx;' % (var, max(0, min(int(self[fname] or 0), 60))))
        if self.font_family:
            # unquoted: the <style> body is HTML-escaped, quotes would break it
            decls.append('--pk-font: %s;' % re.sub(r'[^A-Za-z ]', '', self.font_family))
        css = 'body.pk-body { %s }' % ' '.join(decls)
        version = int(self.write_date.timestamp()) if self.write_date else 0
        for fname, rule, clear in self._BG_IMAGES:
            if self[fname]:
                # unquoted url(): the <style> body is HTML-escaped
                css += ' %s { background-image: url(/perok_login/animation/%d/%s?v=%d) !important; ' \
                       'background-size: cover !important; background-position: center !important; ' \
                       'background-repeat: no-repeat !important; }' % (rule, self.id, fname, version)
                for other in clear:
                    css += ' %s { background: transparent !important; }' % other
        if self.page_bg_video or self.page_bg_image:
            # the page colour stays on the body; the layers above it let the picture / video through
            css += ' body.pk-body #wrapwrap, body.pk-body .pk-wrap { background: transparent !important; }'
            page_alpha = max(0, min(self.page_bg_opacity if self.page_bg_opacity is not False else 100, 100)) / 100.0
            css += ' body.pk-body .pk-page-bg, body.pk-body .pk-page-video { opacity: %.2f; z-index: %d; }' % (
                page_alpha, max(-10, min(self.z_page or 0, 50)))
        css += ' body.pk-body .pk-wrap { position: relative; z-index: %d; }' % max(-10, min(
            self.z_card if self.z_card is not False else 1, 50))
        # card: colour at the chosen opacity, picture on a fading layer, optional blur
        alpha = max(0, min(self.card_opacity if self.card_opacity is not False else 100, 100)) / 100.0
        rgb = _hex_rgb(self.card_bg) or (255, 254, 252)
        css += ' body.pk-body .pk-card { background: rgba(%d, %d, %d, %.2f) !important;' % (
            rgb + (0.0 if self.card_bg_transparent else alpha,))
        if self.card_bg_transparent:
            css += ' box-shadow: none !important;'
        if self.card_blur:
            blur = max(0, min(self.card_blur, 40))
            css += ' backdrop-filter: blur(%dpx); -webkit-backdrop-filter: blur(%dpx);' % (blur, blur)
        if self.card_bg_image:
            css += ' --pk-card-img: url(/perok_login/animation/%d/card_bg_image?v=%d); --pk-card-img-opacity: %.2f;' % (
                self.id, version, alpha)
        css += ' }'
        # the card's children above its picture layer
        css += ' body.pk-body .pk-card > :not(.pk-card-video) { position: relative; }'
        clamp = lambda v, lo, hi, d: max(lo, min(int(v if v not in (False, None) else d), hi))
        # card size and column split
        split = clamp(self.card_split, 20, 80, 48)
        css += ' body.pk-body .pk-card { max-width: %dpx; min-height: %dpx; grid-template-columns: %d%% %d%%; }' % (
            clamp(self.card_width, 600, 1800, 980), clamp(self.card_height, 380, 1200, 600), split, 100 - split)
        # panel size: width share of the mascot column, height centred in the card
        pw, ph = clamp(self.panel_width, 30, 100, 80), clamp(self.panel_height, 30, 100, 100)
        css += ' body.pk-body .pk-stage { --pk-box: %dcqw; }' % pw
        if ph < 100:
            css += ' body.pk-body .pk-stage__bg { top: %.1f%%; bottom: %.1f%%; }' % ((100 - ph) / 2.0, (100 - ph) / 2.0)
        fit = self.mascot_fit or ('contain' if self.keep_in_panel else 'free')
        if fit == 'free' and self.keep_in_panel:
            fit = 'contain'
        if fit in ('contain', 'cover'):
            # the stage shrinks to the panel and clips there; the clip fills it
            css += ' body.pk-body .pk-stage { right: calc(100%% - var(--pk-box)); top: %.1f%%; bottom: %.1f%%;' \
                   ' overflow: hidden; border-radius: var(--pk-panel-radius); --pk-bx: calc(var(--pk-box) / 2); }' % (
                       (100 - ph) / 2.0, (100 - ph) / 2.0)
            css += ' body.pk-body .pk-stage .pk-stage__bg { top: 0; bottom: 0; }'
            css += ' body.pk-body .pk-stage__bird { left: 0; top: 0; bottom: auto; width: 100%; height: 100%; translate: none; }'
            css += ' body.pk-body .pk-vid--human video, body.pk-body .pk-vid--human img { object-fit: %s; object-position: 50%% %s; }' % (
                fit, '50%' if fit == 'cover' else '100%')
            css += ' body.pk-body .pk-stage__shadow { display: none; }'
        # layers
        css += ' body.pk-body .pk-card-video { z-index: %d; }' % clamp(self.z_card_video, -10, 50, 0)
        css += ' body.pk-body .pk-left { z-index: %d; }' % clamp(self.z_panel, -10, 50, 2)
        css += ' body.pk-body .pk-right { z-index: %d; }' % clamp(self.z_form, -10, 50, 3)
        if self.card_bg_video:
            css += ' body.pk-body .pk-card-video { opacity: %.2f; object-fit: %s; object-position: %s center; }' % (
                alpha, self.card_video_fit or 'contain', self.card_video_position or 'left')
        if self.panel_bg_video:
            css += ' body.pk-body .pk-panel-video { object-fit: %s; object-position: %s center; }' % (
                self.panel_video_fit or 'cover', self.panel_video_position or 'center')
        panel = max(0, min(self.panel_opacity if self.panel_opacity is not False else 100, 100)) / 100.0
        if panel < 1:
            css += ' body.pk-body .pk-stage__bg { opacity: %.2f !important; }' % panel
        return css

    def _login_page_video_url(self):
        self.ensure_one()
        if not self.page_bg_video:
            return False
        version = int(self.write_date.timestamp()) if self.write_date else 0
        return '/perok_login/animation/%d/page_bg_video?v=%d' % (self.id, version)

    def _login_video_url(self, field):
        self.ensure_one()
        if not self[field]:
            return False
        version = int(self.write_date.timestamp()) if self.write_date else 0
        return '/perok_login/animation/%d/%s?v=%d' % (self.id, field, version)

    def _login_font_url(self):
        self.ensure_one()
        if not self.font_family or self.font_family == 'Plus Jakarta Sans':
            return False
        return 'https://fonts.googleapis.com/css2?family=%s:wght@400;500;600;700;800&display=swap' % \
            self.font_family.replace(' ', '+')

    @api.constrains('mascot_type', 'idle_video', 'model_file', 'model_path')
    def _check_idle(self):
        for rec in self:
            if rec.mascot_type == 'model3d' and not (rec.model_file or rec.model_path):
                raise ValidationError(_('Upload the 3D model (.glb) for a 3D animation.'))

    def _keep_single_default(self):
        defaults = self.filtered('is_default')
        if defaults:
            self.search([('id', 'not in', defaults[-1:].ids), ('is_default', '=', True)]).write({'is_default': False})

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._keep_single_default()
        return recs

    def write(self, vals):
        if self.filtered('is_system') and ({'mascot_type', 'is_system'} & set(vals)):
            raise UserError(_('The built-in parrot animation cannot be changed.'))
        res = super().write(vals)
        if vals.get('is_default'):
            self._keep_single_default()
        return res

    @api.ondelete(at_uninstall=False)
    def _unlink_except_system(self):
        if self.filtered('is_system'):
            raise UserError(_('The built-in parrot animation cannot be deleted.'))

    @api.model
    def _get_login_animation(self):
        """The default animation, falling back to the built-in parrot."""
        rec = self.sudo().search([('is_default', '=', True)], limit=1)
        if not rec or (rec.mascot_type == 'model3d' and not (rec.model_file or rec.model_path)):
            return self.sudo().browse()
        return rec

    def _login_model_url(self):
        self.ensure_one()
        if self.model_file:
            version = int(self.write_date.timestamp()) if self.write_date else 0
            return '/perok_login/animation/%d/model_file?v=%d' % (self.id, version)
        return self.model_path

    def _login_clips_json(self):
        """URLs of the uploaded clips for login.js (only the clips that exist)."""
        self.ensure_one()
        version = int(self.write_date.timestamp()) if self.write_date else 0
        clips, images = {}, []
        mimetypes = {
            att.res_field: att.mimetype
            for att in self.env['ir.attachment'].sudo().search([
                ('res_model', '=', self._name), ('res_id', '=', self.id), ('res_field', 'in', CLIP_FIELDS)])
        }
        for field in CLIP_FIELDS:
            if self[field]:
                name = field[:-len('_video')]
                clips[name] = '/perok_login/animation/%d/%s?v=%d' % (self.id, field, version)
                # still images (one pose per state) are shown with <img>, clips with <video>
                if (mimetypes.get(field) or '').startswith('image/'):
                    images.append(name)
        clips['_images'] = images
        cfg = {}
        for name in ('idle', 'look', 'eyes_close', 'eyes_open', 'happy', 'sad'):
            if name not in clips:
                continue
            speed = self[name + '_speed'] or 1.0
            lf = self[name + '_loop_from'] if name != 'sad' else 0.0
            lt = self[name + '_loop_to'] if name != 'sad' else 0.0
            cfg[name] = {'speed': max(0.1, min(speed, 4.0)), 'from': max(0.0, lf or 0.0), 'to': max(0.0, lt or 0.0)}
        clips['_cfg'] = cfg
        if self.happy_run and self.happy_video and 'happy' not in images:
            clips['_run'] = {'delay': max(0.0, self.happy_run_delay or 0.0),
                             'speed': max(200, min(self.happy_run_speed or 900, 4000))}
            rect = [float(v) for v in re.findall(r'-?\d+(?:\.\d+)?', self.happy_drop_rect or '')]
            if self.happy_drop_image and len(rect) == 4:
                clips['_run']['drop'] = {
                    'url': '/perok_login/animation/%d/happy_drop_image?v=%d' % (self.id, version),
                    't': max(0.0, self.happy_drop_time or 0.0),
                    'rect': rect,
                }
        return json.dumps(clips)
