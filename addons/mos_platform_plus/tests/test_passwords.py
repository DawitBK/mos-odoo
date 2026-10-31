from odoo.addons.mos_platform_plus.models.passwords import post_init_hook
from odoo.exceptions import UserError
from odoo.tests import HttpCase, tagged
from odoo.tests.common import new_test_user


@tagged('post_install', '-at_install')
class TestPasswords(HttpCase):
    """FR-PLT-018: a minimum password length, lockout after repeated failures, and a password an administrator sets is temporary."""

    def setUp(self):
        super().setUp()
        self.admin_user = new_test_user(self.env, login='pw_admin', groups='mos_core.group_mos_admin,base.group_system')
        self.person = new_test_user(self.env, login='pw_person', groups='mos_core.group_mos_user', password='Start-pass-12345')

    def test_a_reset_by_an_administrator_is_temporary_and_a_change_by_the_person_is_not(self):
        self.person.with_user(self.admin_user).sudo()._change_password('Temporary-pass-1')
        self.assertTrue(self.person.sudo().mos_must_change_password)
        self.person.with_user(self.person).change_password('Temporary-pass-1', 'My-own-pass-12345')
        self.assertFalse(self.person.sudo().mos_must_change_password)

    def test_an_administrator_changing_their_own_password_is_not_asked_again(self):
        self.admin_user.with_user(self.admin_user)._change_password('Admin-own-pass-12345')
        self.assertFalse(self.admin_user.sudo().mos_must_change_password)

    def test_the_application_does_not_open_until_the_password_is_changed(self):
        self.person.sudo().mos_must_change_password = True
        self.authenticate('pw_person', 'Start-pass-12345')
        response = self.url_open('/odoo', allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn('/my/security', response.headers['Location'])
        self.person.sudo().mos_must_change_password = False
        self.assertEqual(self.url_open('/odoo', allow_redirects=False).status_code, 200)

    def test_a_short_password_and_one_without_a_digit_are_refused(self):
        self.env['ir.config_parameter'].sudo().set_str('auth_password_policy.minlength', '10')
        person = self.person.with_user(self.person).with_context(mos_enforce_password_policy=True)
        with self.assertRaises(UserError):
            person.change_password('Start-pass-12345', 'short1')
        with self.assertRaises(UserError):
            person.change_password('Start-pass-12345', 'onlyletterspassword')
        person.change_password('Start-pass-12345', 'Longer-pass-12')

    def test_the_complexity_rule_can_be_switched_off(self):
        self.env['ir.config_parameter'].sudo().set_str('mos_platform_plus.password_complexity', '0')
        self.person.with_user(self.person).with_context(mos_enforce_password_policy=True).change_password('Start-pass-12345', 'onlyletterspassword')

    def test_a_new_installation_gets_the_policy_and_an_existing_choice_is_kept(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_str('base.login_cooldown_after', '')
        params.set_str('base.login_cooldown_duration', '120')
        post_init_hook(self.env)
        self.assertEqual(params.get_str('base.login_cooldown_after'), '5')
        self.assertEqual(params.get_str('base.login_cooldown_duration'), '120', 'a value somebody chose is not overwritten')

    def test_sensitive_roles_are_sent_to_turn_on_two_step_sign_in_only_when_the_installation_asks(self):
        boss = new_test_user(self.env, login='pw_boss', groups='mos_approvals.group_mos_finance_manager', password='Boss-pass-12345')
        params = self.env['ir.config_parameter'].sudo()
        self.assertFalse(boss._mos_totp_missing(), 'off by default')
        params.set_str('mos_platform_plus.require_totp', '1')
        self.assertTrue(boss._mos_totp_missing())
        self.assertFalse(self.person._mos_totp_missing(), 'an ordinary role is not asked')
        self.authenticate('pw_boss', 'Boss-pass-12345')
        response = self.url_open('/odoo', allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn('/my/security', response.headers['Location'])
