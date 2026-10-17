# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request

from ..models.login_animation import CLIP_FIELDS


class PerokLoginAnimationController(http.Controller):

    # the login page is public: serve only the clips of login animations
    @http.route('/perok_login/animation/<int:anim_id>/<string:field>', type='http', auth='public', sitemap=False)
    def animation_clip(self, anim_id, field, **kw):
        if field not in CLIP_FIELDS + ['model_file', 'happy_drop_image', 'page_bg_image', 'card_bg_image', 'panel_bg_image',
                                         'page_bg_video', 'card_bg_video', 'panel_bg_video']:
            raise request.not_found()
        rec = request.env['perok.login.animation'].sudo().browse(anim_id).exists()
        if not rec or not rec[field]:
            raise request.not_found()
        stream = request.env['ir.binary']._get_stream_from(
            rec, field, filename=rec[field + '_name'] if field + '_name' in rec else field,
            mimetype='model/gltf-binary' if field == 'model_file' else None)
        return stream.get_response(max_age=86400 * 7)

    # Settings > Login Page > Animations: the login page of one animation, shown in the
    # live preview of its form (the form's unsaved values are posted to it, see preview.js)
    @http.route('/perok_login/preview/<int:anim_id>', type='http', auth='user', website=True, sitemap=False)
    def animation_preview(self, anim_id, **kw):
        if not request.env.user.has_group('base.group_system'):
            raise request.not_found()
        request.update_context(perok_preview_id=anim_id)
        response = request.render('web.login', {
            'databases': None,
            'disable_database_manager': False,
            'reset_password_enabled': True,
        })
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        return response
