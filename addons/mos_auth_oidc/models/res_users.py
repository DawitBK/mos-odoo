import logging

from psycopg2 import errors

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = 'res.users'

    mos_session_epoch = fields.Integer(
        string='Session epoch',
        default=0,
        copy=False,
        readonly=True,
        help='Raised when the identity provider reports that the person signed out or was disabled there. It is part of the '
        'session token, so raising it ends every session of the account at once without touching the password.',
    )

    def _get_session_token_fields(self):
        return super()._get_session_token_fields() | {'mos_session_epoch'}

    def _mos_sso_provider(self):
        return self.env.ref('mos_auth_oidc.provider_mos_sso')

    def _mos_bind_sso_subject(self, subject):
        """May this account sign in as the identity with this id? Binds the id at the first single sign-on.

        The same email arriving later with ANOTHER id is refused: an address given to someone else must not inherit the account.
        An id already bound to a different account is refused too (one person, one account).
        """
        self.ensure_one()
        provider = self._mos_sso_provider()
        if self.oauth_uid:
            return self.oauth_uid == subject and self.oauth_provider_id == provider
        try:
            with self.env.cr.savepoint():
                self.write({'oauth_provider_id': provider.id, 'oauth_uid': subject})
        except (errors.UniqueViolation, errors.IntegrityError):
            return False
        return True

    def _mos_end_sessions_of_subject(self, subject):
        """End every session of the account(s) bound to this identity id; returns how many accounts. An unknown id changes nothing."""
        provider = self._mos_sso_provider()
        users = self.sudo().search([('oauth_uid', '=', subject), ('oauth_provider_id', '=', provider.id)])
        for user in users:
            user.mos_session_epoch = (user.mos_session_epoch or 0) + 1
        return len(users)

    @api.model
    def _mos_apply_login_screen_defaults(self):
        """Only MOS's own single sign-on and the local password on the sign-in page; accounts are created by an administrator, never by sign-up."""
        own = self._mos_sso_provider()
        others = self.env['auth.oauth.provider'].sudo().search([('id', '!=', own.id), ('enabled', '=', True)])
        if others:
            others.write({'enabled': False})
        self.env['ir.config_parameter'].sudo().set_str('auth_signup.invitation_scope', 'b2b')
