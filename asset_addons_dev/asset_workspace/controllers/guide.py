# -*- coding: utf-8 -*-
"""Asset Management user guide, served at /asset/guide (logged-in users)."""
from odoo import http
from odoo.http import request
from odoo.tools import file_open


class AssetGuide(http.Controller):

    @http.route(['/asset/guide'], type='http', auth='user', methods=['GET'])
    def asset_guide(self, **kw):
        with file_open('asset_workspace/static/guide/asset_management.html', 'rb') as f:
            body = f.read()
        return request.make_response(body, headers=[
            ('Content-Type', 'text/html; charset=utf-8'),
            ('Cache-Control', 'private, max-age=300'),
        ])
