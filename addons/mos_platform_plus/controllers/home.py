from odoo import http
from odoo.http import request
from odoo.addons.web.controllers.home import Home


class MosHome(Home):
    @http.route()
    def web_client(self, s_action=None, **kw):
        """A person whose password an administrator reset is taken to their security page before the application opens."""
        uid = request.session.uid
        user = request.env['res.users'].sudo().browse(uid) if uid else None
        if user and (user.mos_must_change_password or user._mos_totp_missing()):
            return request.redirect('/my/security', 303)
        return super().web_client(s_action, **kw)
