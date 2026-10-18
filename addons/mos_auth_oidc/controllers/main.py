"""OpenID Connect login: authorization code flow with PKCE and a confidential client.

Odoo's own OAuth login uses the implicit flow, which a modern identity provider does not offer. This
controller replaces only the sign-in hand-off: it never creates users (FR-PLT-011), it matches the
verified email of the identity to an existing active user, and it reuses Odoo's own session path.

Settings (system parameters, set by the deployment, never in code):
    mos_auth_oidc.issuer         e.g. https://sso.example.com/sso
    mos_auth_oidc.client_id      the client registered at the identity provider
    mos_auth_oidc.client_secret
    mos_auth_oidc.redirect_uri   optional; defaults to <this server>/mos/oidc/callback
"""

import base64
import hashlib
import json
import logging
import secrets
import time
from urllib.parse import urlencode

import requests

from odoo import http
from odoo.http import request
from odoo.http.session import authenticate
from odoo.addons.web.controllers.utils import _get_login_redirect_url, ensure_db

_logger = logging.getLogger(__name__)
TIMEOUT = 10


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _param(name, default=''):
    return request.env['ir.config_parameter'].sudo().get_str(f'mos_auth_oidc.{name}', default)


def _discovery():
    issuer = _param('issuer').rstrip('/')
    if not issuer:
        raise ValueError('mos_auth_oidc.issuer is not set')
    response = requests.get(f'{issuer}/.well-known/openid-configuration', timeout=TIMEOUT)
    response.raise_for_status()
    document = response.json()
    if document.get('issuer', '').rstrip('/') != issuer:
        raise ValueError('The identity provider reports a different issuer than the one configured')
    return document


def _id_token_claims(id_token):
    """Claims of an ID token received directly from the token endpoint over TLS.

    OpenID Connect Core 3.1.3.7 allows skipping the signature check in exactly this case; the issuer,
    audience, nonce and expiry are still checked by the caller.
    """
    try:
        payload = id_token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    except Exception as error:  # malformed token
        raise ValueError('The ID token is not readable') from error


class MosOidcController(http.Controller):
    @http.route('/mos/oidc/login', type='http', auth='none', readonly=False)
    def login(self, **kw):
        ensure_db()
        try:
            discovery = _discovery()
        except Exception:
            _logger.exception('OIDC discovery failed')
            return request.redirect('/web/login?oauth_error=2')
        verifier = secrets.token_urlsafe(48)
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        redirect = kw.get('redirect') or '/odoo'
        request.session['mos_oidc'] = {'state': state, 'nonce': nonce, 'verifier': verifier, 'next': redirect if redirect.startswith('/') and not redirect.startswith('//') else '/odoo'}
        redirect_uri = _param('redirect_uri') or request.httprequest.url_root.rstrip('/') + '/mos/oidc/callback'
        query = urlencode(
            {
                'client_id': _param('client_id'),
                'response_type': 'code',
                'scope': 'openid email profile',
                'redirect_uri': redirect_uri,
                'state': state,
                'nonce': nonce,
                'code_challenge': _b64url(hashlib.sha256(verifier.encode()).digest()),
                'code_challenge_method': 'S256',
            }
        )
        return request.redirect(f"{discovery['authorization_endpoint']}?{query}", local=False)

    @http.route('/mos/oidc/callback', type='http', auth='none', readonly=False)
    def callback(self, **kw):
        ensure_db()
        saved = request.session.pop('mos_oidc', None)
        failure = request.redirect('/web/login?oauth_error=3')
        if not saved or not secrets.compare_digest(kw.get('state', ''), saved['state']) or 'code' not in kw:
            return failure
        try:
            discovery = _discovery()
            redirect_uri = _param('redirect_uri') or request.httprequest.url_root.rstrip('/') + '/mos/oidc/callback'
            token = requests.post(
                discovery['token_endpoint'],
                data={
                    'grant_type': 'authorization_code',
                    'code': kw['code'],
                    'redirect_uri': redirect_uri,
                    'client_id': _param('client_id'),
                    'client_secret': _param('client_secret'),
                    'code_verifier': saved['verifier'],
                },
                timeout=TIMEOUT,
            )
            token.raise_for_status()
            tokens = token.json()
            claims = _id_token_claims(tokens['id_token'])
            audience = claims.get('aud')
            audience = audience if isinstance(audience, list) else [audience]
            if (
                claims.get('iss', '').rstrip('/') != discovery['issuer'].rstrip('/')
                or _param('client_id') not in audience
                or claims.get('nonce') != saved['nonce']
                or claims.get('exp', 0) < time.time()
            ):
                raise ValueError('The ID token failed validation')
            info = requests.get(discovery['userinfo_endpoint'], headers={'Authorization': f"Bearer {tokens['access_token']}"}, timeout=TIMEOUT)
            info.raise_for_status()
            identity = info.json()
            if identity.get('sub') != claims.get('sub') or not identity.get('email') or identity.get('email_verified') is not True:
                raise ValueError('The identity has no verified email')
        except Exception:
            _logger.exception('OIDC sign-in failed')
            return failure

        user = request.env['res.users'].sudo().search([('login', '=ilike', identity['email']), ('active', '=', True)], limit=1)
        if not user:
            _logger.info('OIDC sign-in refused: %s has no MOS account', identity['email'])
            return failure
        if hasattr(user, '_mos_apply_idp_claims'):  # roles from the provider's groups, when mos_platform_plus is installed
            user._mos_apply_idp_claims(identity)
        session_token = secrets.token_urlsafe(32)
        user.write({'oauth_access_token': session_token})
        request.env.cr.commit()
        auth_info = authenticate(request.session, request.env, {'login': user.login, 'token': session_token, 'type': 'oauth_token'})
        response = request.redirect(_get_login_redirect_url(auth_info['uid'], saved['next']), 303)
        response.autocorrect_location_header = False
        return response
