import base64
import json
import time
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from odoo.tests import HttpCase, tagged
from odoo.tests.common import TransactionCase

from ..oidc_jwt import BACKCHANNEL_LOGOUT_EVENT, LogoutTokenError, verify_logout_token

ISSUER = 'https://idp.example.com/sso'
CLIENT = 'mos'


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def int_b64(number):
    return b64(number.to_bytes((number.bit_length() + 7) // 8, 'big'))


class Idp:
    """A tiny identity provider key: signs logout tokens and publishes the matching key set."""

    def __init__(self, kid='k1'):
        self.kid = kid
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = self.key.public_key().public_numbers()
        self.jwk = {'kty': 'RSA', 'kid': kid, 'alg': 'RS256', 'use': 'sig', 'n': int_b64(numbers.n), 'e': int_b64(numbers.e)}

    def token(self, header=None, **claims):
        head = {'alg': 'RS256', 'kid': self.kid, 'typ': 'logout+jwt', **(header or {})}
        now = int(time.time())
        body = {'iss': ISSUER, 'aud': CLIENT, 'iat': now, 'exp': now + 120, 'jti': 'j1', 'sub': 'sub-1', 'events': {BACKCHANNEL_LOGOUT_EVENT: {}}, **claims}
        body = {k: v for k, v in body.items() if v is not None}
        signing_input = f'{b64(json.dumps(head).encode())}.{b64(json.dumps(body).encode())}'
        signature = self.key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
        return f'{signing_input}.{b64(signature)}'


@tagged('post_install', '-at_install')
class TestLogoutToken(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.idp = Idp()
        cls.keys = [cls.idp.jwk]

    def verify(self, token, **kw):
        return verify_logout_token(token, kw.get('keys', self.keys), ISSUER, CLIENT)

    def test_a_valid_token_names_the_person(self):
        self.assertEqual(self.verify(self.idp.token(sub='abc-123')), 'abc-123')

    def test_every_defect_is_refused(self):
        other = Idp()  # same kid, different key: a forged signature
        cases = {
            'a key the provider does not publish': other.token(),
            'another client': self.idp.token(aud='someone-else'),
            'another issuer': self.idp.token(iss='https://evil.example.com'),
            'an ordinary token type': self.idp.token(header={'typ': 'JWT'}),
            'a different algorithm': self.idp.token(header={'alg': 'HS256'}),
            'no logout event': self.idp.token(events={}),
            'a nonce (an ID token in disguise)': self.idp.token(nonce='n'),
            'no subject': self.idp.token(sub=None),
            'a stale token': self.idp.token(iat=int(time.time()) - 3600, exp=None),
            'an expired token': self.idp.token(iat=int(time.time()) - 200, exp=int(time.time()) - 100),
            'a token from the future': self.idp.token(iat=int(time.time()) + 3600),
            'text that is not a token': 'not-a-token',
        }
        for label, token in cases.items():
            with self.subTest(label), self.assertRaises(LogoutTokenError):
                self.verify(token)

    def test_the_audience_may_be_a_list(self):
        self.assertEqual(self.verify(self.idp.token(aud=['other', CLIENT])), 'sub-1')

    def test_an_unknown_kid_says_so_so_the_caller_can_refresh_the_keys(self):
        with self.assertRaisesRegex(LogoutTokenError, 'no published key'):
            self.verify(Idp(kid='rotated').token())


@tagged('post_install', '-at_install')
class TestSsoBinding(TransactionCase):
    def setUp(self):
        super().setUp()
        self.user = self.env['res.users'].create({'name': 'Person', 'login': 'person@example.com', 'email': 'person@example.com'})
        self.other = self.env['res.users'].create({'name': 'Other', 'login': 'other@example.com', 'email': 'other@example.com'})

    def test_the_first_sign_in_binds_the_id_and_the_same_id_keeps_working(self):
        self.assertTrue(self.user._mos_bind_sso_subject('sub-A'))
        self.assertEqual(self.user.oauth_uid, 'sub-A')
        self.assertTrue(self.user._mos_bind_sso_subject('sub-A'))

    def test_the_same_account_with_another_id_is_refused(self):
        self.user._mos_bind_sso_subject('sub-A')
        self.assertFalse(self.user._mos_bind_sso_subject('sub-B'))
        self.assertEqual(self.user.oauth_uid, 'sub-A')

    def test_an_id_belongs_to_one_account(self):
        self.user._mos_bind_sso_subject('sub-A')
        self.assertFalse(self.other._mos_bind_sso_subject('sub-A'))
        self.assertFalse(self.other.oauth_uid)

    def test_signing_out_raises_the_epoch_of_that_person_only(self):
        self.user._mos_bind_sso_subject('sub-A')
        self.other._mos_bind_sso_subject('sub-B')
        before = (self.user.mos_session_epoch, self.other.mos_session_epoch)
        self.assertEqual(self.env['res.users']._mos_end_sessions_of_subject('sub-A'), 1)
        self.assertEqual(self.user.mos_session_epoch, before[0] + 1)
        self.assertEqual(self.other.mos_session_epoch, before[1])
        self.assertEqual(self.env['res.users']._mos_end_sessions_of_subject('never-seen'), 0)

    def test_the_epoch_is_part_of_the_session_token_so_raising_it_ends_the_sessions(self):
        self.assertIn('mos_session_epoch', self.user._get_session_token_fields())
        self.user._mos_bind_sso_subject('sub-A')
        before = self.user._compute_session_token('a-session-id')
        self.env['res.users']._mos_end_sessions_of_subject('sub-A')
        self.user.invalidate_recordset()
        self.assertNotEqual(self.user._compute_session_token('a-session-id'), before)

    def test_the_sign_in_page_offers_only_our_single_sign_on(self):
        self.env['res.users']._mos_apply_login_screen_defaults()
        enabled = self.env['auth.oauth.provider'].sudo().search([('enabled', '=', True)])
        self.assertLessEqual(set(enabled.mapped('name')), {'SSO'})
        self.assertEqual(self.env['ir.config_parameter'].sudo().get_str('auth_signup.invitation_scope'), 'b2b')


@tagged('post_install', '-at_install')
class TestHttp(HttpCase):
    def setUp(self):
        super().setUp()
        self.idp = Idp()
        params = self.env['ir.config_parameter'].sudo()
        params.set_str('mos_auth_oidc.issuer', ISSUER)
        params.set_str('mos_auth_oidc.client_id', CLIENT)
        self.user = self.env['res.users'].create({'name': 'Person', 'login': 'person@example.com', 'email': 'person@example.com'})
        self.user._mos_bind_sso_subject('sub-A')
        self.discovery = {
            'issuer': ISSUER,
            'jwks_uri': f'{ISSUER}/jwks',
            'end_session_endpoint': f'{ISSUER}/session/end',
            'authorization_endpoint': f'{ISSUER}/auth',
        }

    def _fake_get(self, url, **kw):
        class Reply:
            def __init__(reply, payload):
                reply.payload = payload

            def raise_for_status(reply):
                return None

            def json(reply):
                return reply.payload

        return Reply(self.discovery if url.endswith('openid-configuration') else {'keys': [self.idp.jwk]})

    def _logout(self, token):
        from odoo.addons.mos_auth_oidc.controllers import main

        main._jwks_cache.clear()
        with patch('odoo.addons.mos_auth_oidc.controllers.main.requests.get', side_effect=self._fake_get):
            return self.url_open('/mos/oidc/backchannel-logout', data={'logout_token': token})

    def test_a_valid_logout_token_ends_that_persons_sessions(self):
        epoch = self.user.mos_session_epoch
        self.assertEqual(self._logout(self.idp.token(sub='sub-A')).status_code, 200)
        self.user.invalidate_recordset()
        self.assertEqual(self.user.mos_session_epoch, epoch + 1)

    def test_a_person_this_system_never_saw_is_answered_the_same(self):
        self.assertEqual(self._logout(self.idp.token(sub='never-seen')).status_code, 200)

    def test_an_invalid_token_is_refused_and_changes_nothing(self):
        epoch = self.user.mos_session_epoch
        for token in ('not-a-token', self.idp.token(aud='someone-else'), Idp().token(sub='sub-A')):
            self.assertEqual(self._logout(token).status_code, 400)
        self.user.invalidate_recordset()
        self.assertEqual(self.user.mos_session_epoch, epoch)

    def test_the_denied_page_says_it_plainly_and_offers_the_way_out(self):
        with patch('odoo.addons.mos_auth_oidc.controllers.main.requests.get', side_effect=self._fake_get):
            page = self.url_open('/mos/oidc/denied')
        self.assertEqual(page.status_code, 200)
        self.assertIn('You are not signed in to MOS', page.text)
        self.assertIn(f'{ISSUER}/session/end', page.text)
        self.assertNotIn('invitation', page.text)

    def _start_login(self):
        """Begin a sign-in and return the query the browser is sent to the provider with (and keep the session cookie, like a browser)."""
        from urllib.parse import parse_qs, urlparse

        with patch('odoo.addons.mos_auth_oidc.controllers.main.requests.get', side_effect=self._fake_get):
            reply = self.url_open('/mos/oidc/login', allow_redirects=False)
        self.assertEqual(reply.status_code, 303)
        return {k: v[0] for k, v in parse_qs(urlparse(reply.headers['Location']).query).items()}

    def test_the_sign_in_asks_for_the_roles_the_provider_manages(self):
        query = self._start_login()
        self.assertEqual(query['scope'].split(), ['openid', 'email', 'profile', 'app_roles'])
        self.assertEqual(query['code_challenge_method'], 'S256')

    def test_the_provider_refusing_says_there_is_no_access_not_that_it_did_not_complete(self):
        query = self._start_login()
        reply = self.url_open(f"/mos/oidc/callback?error=access_denied&state={query['state']}", allow_redirects=False)
        self.assertEqual(reply.status_code, 303)
        self.assertTrue(reply.headers['Location'].endswith('/mos/oidc/denied'))
        with patch('odoo.addons.mos_auth_oidc.controllers.main.requests.get', side_effect=self._fake_get):
            page = self.url_open('/mos/oidc/denied')
        self.assertIn('You have not been given access to MOS', page.text)
        self.assertNotIn('did not complete', page.text)

    def test_a_refusal_with_the_wrong_state_is_not_believed(self):
        self._start_login()
        reply = self.url_open('/mos/oidc/callback?error=access_denied&state=forged', allow_redirects=False)
        self.assertEqual(reply.status_code, 303)
        with patch('odoo.addons.mos_auth_oidc.controllers.main.requests.get', side_effect=self._fake_get):
            page = self.url_open('/mos/oidc/denied')
        self.assertIn('did not complete', page.text)
