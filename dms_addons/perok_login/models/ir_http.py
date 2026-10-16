# -*- coding: utf-8 -*-
import json

from odoo import models
from odoo.http import request

# Sent by static/src/js/login.js when it posts the login form in the background
LOGIN_HEADER = 'X-Perok-Login'


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _post_dispatch(cls, response):
        """For a background login, hand the redirect target back as JSON.

        The login page posts its form with fetch() so the parrot can fly away
        before the browser leaves the page. A fetch cannot read a redirect's
        Location (nor the "#action=..." part other modules put in it), so the
        final redirect of a successful POST /web/login is turned into
        {"redirect": <location>} here, after every controller override has run.
        The session cookie set by the login stays on the response.
        """
        super()._post_dispatch(response)
        httprequest = request.httprequest
        if (httprequest.method == 'POST'
                and httprequest.path in ('/web/login', '/web/login/totp')
                and httprequest.headers.get(LOGIN_HEADER) == '1'
                and 300 <= getattr(response, 'status_code', 0) < 400):
            location = response.headers.get('Location')
            if location:
                response.status_code = 200
                response.headers.pop('Location', None)
                response.headers['Content-Type'] = 'application/json'
                response.headers['Cache-Control'] = 'no-store'
                response.set_data(json.dumps({'redirect': location}))
