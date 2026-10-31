from unittest.mock import MagicMock, patch

import requests

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.mos_auth_oidc.controllers import main
from odoo.addons.mos_auth_oidc.controllers.main import internal_address

ISSUER = 'https://idp.example.test/sso'
INTERNAL = 'http://host.docker.internal:15080/sso-backend'


@tagged('post_install', '-at_install')
class TestInternalAddress(TransactionCase):
    """Sign-in must not depend on the public internet when the identity provider is on the same machine."""

    def test_an_address_under_the_issuer_is_called_under_the_internal_url(self):
        self.assertEqual(internal_address(f'{ISSUER}/token', ISSUER, INTERNAL), f'{INTERNAL}/token')
        self.assertEqual(internal_address(f'{ISSUER}/jwks?x=1', ISSUER, INTERNAL), f'{INTERNAL}/jwks?x=1')
        self.assertEqual(internal_address(f'{ISSUER}/.well-known/openid-configuration', f'{ISSUER}/', f'{INTERNAL}/'), f'{INTERNAL}/.well-known/openid-configuration')
        self.assertEqual(internal_address(ISSUER, ISSUER, INTERNAL), INTERNAL)

    def test_anything_else_is_left_alone(self):
        self.assertEqual(internal_address('https://idp.example.test/sso-other/token', ISSUER, INTERNAL), 'https://idp.example.test/sso-other/token', 'a path boundary, not a prefix')
        self.assertEqual(internal_address('https://elsewhere.test/token', ISSUER, INTERNAL), 'https://elsewhere.test/token')
        self.assertEqual(internal_address(f'{ISSUER}/token', ISSUER, ''), f'{ISSUER}/token', 'no internal URL: nothing changes')
        self.assertEqual(internal_address(f'{ISSUER}/token', '', INTERNAL), f'{ISSUER}/token')

    def test_the_setting_is_read_from_the_system_parameters(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_str('mos_auth_oidc.issuer', ISSUER)
        params.set_str('mos_auth_oidc.internal_url', INTERNAL)
        with patch.object(main, '_param', side_effect=lambda name, default='': params.get_str(f'mos_auth_oidc.{name}', default)):
            self.assertEqual(main._internal(f'{ISSUER}/userinfo'), f'{INTERNAL}/userinfo')


@tagged('post_install', '-at_install')
class TestReadsAreRetried(TransactionCase):
    """A reading of the provider that fails to connect or stalls is tried again; one the provider answered (even with an error) is not."""

    def _reply(self, status=200):
        reply = MagicMock()
        reply.status_code = status
        reply.raise_for_status.side_effect = None if status < 400 else requests.HTTPError(f'{status}')
        return reply

    def test_a_stall_is_retried_and_the_answer_is_used(self):
        good = self._reply()
        with patch.object(main.requests, 'get', side_effect=[requests.Timeout('stalled'), requests.ConnectionError('reset'), good]) as get, patch.object(main.time, 'sleep'):
            self.assertIs(main._get('http://idp.test/x'), good)
        self.assertEqual(get.call_count, 3)

    def test_it_gives_up_after_three_attempts_and_says_why(self):
        with patch.object(main.requests, 'get', side_effect=requests.Timeout('stalled')) as get, patch.object(main.time, 'sleep'):
            with self.assertRaises(requests.Timeout):
                main._get('http://idp.test/x')
        self.assertEqual(get.call_count, 3)

    def test_an_error_answer_is_not_retried(self):
        with patch.object(main.requests, 'get', return_value=self._reply(404)) as get, patch.object(main.time, 'sleep'):
            with self.assertRaises(requests.HTTPError):
                main._get('http://idp.test/x')
        self.assertEqual(get.call_count, 1)
