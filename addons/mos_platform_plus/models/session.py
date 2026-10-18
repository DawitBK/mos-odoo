import time

from odoo import api, models
from odoo.http import request
from odoo.http.session import SessionExpiredException

TOUCH_EVERY = 60


class IrHttp(models.AbstractModel):
    """A signed-in session that has been idle longer than the configured limit is ended (FR-PLT-015).

    The limit is the system parameter `mos_platform_plus.idle_timeout_minutes`; 0 (the default) means no limit.
    """

    _inherit = 'ir.http'

    @classmethod
    def _pre_dispatch(cls, rule, args):
        super()._pre_dispatch(rule, args)
        session = request.session
        if not session.uid:
            return
        minutes = cls._mos_idle_limit_minutes(request.env)
        if minutes <= 0:
            return
        now = time.time()
        last = session.get('mos_last_seen')
        if last and now - last > minutes * 60:
            session.logout(keep_db=True)
            raise SessionExpiredException('Session idle for too long')
        if not last or now - last > TOUCH_EVERY:
            session['mos_last_seen'] = now

    @classmethod
    def _mos_idle_limit_minutes(cls, env):
        try:
            return int(env['ir.config_parameter'].sudo().get_str('mos_platform_plus.idle_timeout_minutes', '0') or 0)
        except ValueError:
            return 0

    @api.model
    def _mos_session_is_idle(self, last_seen, now=None):
        """Has a session last seen at `last_seen` outlived the limit?"""
        minutes = self._mos_idle_limit_minutes(self.env)
        return bool(minutes > 0 and last_seen and (now or time.time()) - last_seen > minutes * 60)
