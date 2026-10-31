import re

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import config

DEFAULTS = {
    # Odoo's own limits, not copies of them: an address stops after this many failed sign-ins, for this many seconds (FR-PLT-018).
    'base.login_cooldown_after': '5',
    'base.login_cooldown_duration': '300',
}


# Roles that move money or change who may: when the installation asks for it, they sign in in two steps.
SENSITIVE_GROUPS = ['mos_core.group_mos_admin', 'mos_approvals.group_mos_finance_manager', 'mos_approvals.group_mos_general_manager', 'mos_payables.group_mos_cashier']


class ResUsers(models.Model):
    """The password rules of FR-PLT-018: length (Odoo's password policy) and a letter-and-digit mix, and a password an administrator sets
    for someone is temporary: that person must choose their own at the next sign-in."""

    _inherit = 'res.users'

    mos_must_change_password = fields.Boolean(string='Must change password at next sign-in', copy=False, groups='mos_core.group_mos_admin')

    def _check_password_policy(self, passwords):
        # The suites create hundreds of throw-away users with short passwords; only the test of the rules themselves turns the rules on.
        if config['test_enable'] and not self.env.context.get('mos_enforce_password_policy'):
            return
        super()._check_password_policy(passwords)
        if self.env['ir.config_parameter'].sudo().get_str('mos_platform_plus.password_complexity', '1') == '0':
            return
        for password in passwords:
            if password and not (re.search(r'[A-Za-z]', password) and re.search(r'\d', password)):
                raise UserError(self.env._('Your password must contain at least one letter and one digit.'))

    def _mos_totp_missing(self):
        """True when the installation requires two-step sign-in for sensitive roles and this person has not turned it on (TDD 21)."""
        self.ensure_one()
        if self.env['ir.config_parameter'].sudo().get_str('mos_platform_plus.require_totp', '0') != '1':
            return False
        sensitive = [self.env.ref(x, raise_if_not_found=False) for x in SENSITIVE_GROUPS]
        if not any(g and self.sudo().has_group(g.get_external_id()[g.id]) for g in sensitive):
            return False
        return not self.sudo().totp_enabled

    def _change_password(self, new_passwd):
        res = super()._change_password(new_passwd)
        for user in self:
            # Both the person's own change and an administrator's reset arrive here; only the administrator's is temporary.
            user.sudo().mos_must_change_password = user != self.env.user
        return res


def post_init_hook(env):
    """On a new installation only: set the lockout where nobody has chosen a value yet."""
    params = env['ir.config_parameter'].sudo()
    for key, value in DEFAULTS.items():
        if not params.get_str(key):
            params.set_str(key, value)
