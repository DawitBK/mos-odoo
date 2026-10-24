"""OpenID Connect login: authorization code flow with PKCE and a confidential client.

Odoo's own OAuth login uses the implicit flow, which a modern identity provider does not offer. This
controller replaces only the sign-in hand-off: it never creates users (FR-PLT-011), it matches the
verified email of the identity to an existing active user, and it reuses Odoo's own session path.

What it adds on top of the sign-in, so that MOS behaves like the other systems behind the same identity provider:
  * the provider's id for the person (`sub`) is bound to the user at the first sign-in; the same email with another id is refused;
  * a person the provider vouches for but MOS does not know gets a plain page that says so and offers "use another account"
    (it ends the single sign-on session at the provider) instead of Odoo's invitation message;
  * the provider's back-channel logout (`/mos/oidc/backchannel-logout`) ends every MOS session of that person.

Settings (system parameters, set by the deployment, never in code):
    mos_auth_oidc.issuer         e.g. https://sso.example.com/sso
    mos_auth_oidc.client_id      the client registered at the identity provider
    mos_auth_oidc.client_secret
    mos_auth_oidc.redirect_uri   optional; defaults to <this server>/mos/oidc/callback
    mos_auth_oidc.logout_redirect_uri  optional; where the browser returns after "use another account"; defaults to <this server>/odoo
"""

import base64
import hashlib
import json
import logging
import secrets
import time
from urllib.parse import urlencode

import requests
from markupsafe import escape

from odoo import SUPERUSER_ID, http
from odoo.http import request
from odoo.http.session import authenticate
from odoo.addons.web.controllers.utils import _get_login_redirect_url, ensure_db

from ..oidc_jwt import LogoutTokenError, verify_logout_token

_logger = logging.getLogger(__name__)
TIMEOUT = 10
JWKS_TTL_SECONDS = 600
_jwks_cache = {}


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


def _jwks(discovery, force=False):
    """The provider's published signing keys, kept for ten minutes (a rotated key is picked up by one forced refresh)."""
    uri = discovery.get('jwks_uri')
    if not uri:
        raise ValueError('The identity provider publishes no keys')
    cached = _jwks_cache.get(uri)
    if cached and not force and time.time() - cached[0] < JWKS_TTL_SECONDS:
        return cached[1]
    response = requests.get(uri, timeout=TIMEOUT)
    response.raise_for_status()
    keys = response.json().get('keys', [])
    _jwks_cache[uri] = (time.time(), keys)
    return keys


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


def _redirect_uri():
    return _param('redirect_uri') or request.httprequest.url_root.rstrip('/') + '/mos/oidc/callback'


def _deny(reason, email=''):
    """Send the browser to the plain page that explains why this sign-in did not work (the reason and address are kept in the session, not in the URL)."""
    request.session['mos_oidc_denied'] = {'reason': reason, 'email': email}
    return request.redirect('/mos/oidc/denied')


_DENIED_TEXT = {
    'no-account': 'Single sign-on has you signed in as <strong>{email}</strong>, and MOS has no account for that address. Ask a MOS administrator to add you, or sign in as someone else.',
    'different-person': 'The person signed in to single sign-on is not the person this MOS account (<strong>{email}</strong>) belongs to. Ask a MOS administrator to look at the account, or sign in as someone else.',
    'no-access': 'You have not been given access to MOS. Ask an administrator to give you a role for it in the identity provider, or sign in as someone else.',
    'failed': 'Single sign-on did not complete. Try again; if it keeps happening, tell a MOS administrator.',
}


def _denied_page(reason, email, logout_url):
    """A small self-contained page (no Odoo layout, so nothing about the database is revealed to someone who is not signed in)."""
    text = _DENIED_TEXT.get(reason, _DENIED_TEXT['failed']).format(email=escape(email or ''))
    switch = f'<a class="primary" href="{escape(logout_url)}">Sign out of single sign-on and use another account</a>' if logout_url else ''
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>You are not signed in to MOS</title><style>body{font-family:system-ui,sans-serif;background:#f4f5f7;margin:0}'
        'main{max-width:26rem;margin:12vh auto;background:#fff;padding:1.5rem 1.75rem;border-radius:.5rem;box-shadow:0 1px 4px #0002}'
        'a{display:block;text-align:center;padding:.6rem;margin-top:.75rem;border-radius:.35rem;border:1px solid #888;color:#222;text-decoration:none}'
        'a.primary{background:#714b67;border-color:#714b67;color:#fff}</style></head><body><main>'
        f'<h1 style="font-size:1.2rem">You are not signed in to MOS</h1><p>{text}</p>{switch}<a href="/web/login">Back to the sign-in page</a></main></body></html>'
    )


class MosOidcController(http.Controller):
    @http.route('/mos/oidc/login', type='http', auth='none', readonly=False)
    def login(self, **kw):
        ensure_db()
        try:
            discovery = _discovery()
        except Exception:
            _logger.exception('OIDC discovery failed')
            return _deny('failed')
        verifier = secrets.token_urlsafe(48)
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        redirect = kw.get('redirect') or '/odoo'
        request.session['mos_oidc'] = {'state': state, 'nonce': nonce, 'verifier': verifier, 'next': redirect if redirect.startswith('/') and not redirect.startswith('//') else '/odoo'}
        query = urlencode(
            {
                'client_id': _param('client_id'),
                'response_type': 'code',
                # app_roles: the roles the identity provider gave this person for MOS, when it manages MOS's access from its console.
                'scope': 'openid email profile app_roles',
                'redirect_uri': _redirect_uri(),
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
        # The provider refused because this person holds no role for MOS there: say so, rather than "did not complete".
        if saved and kw.get('error') == 'access_denied' and secrets.compare_digest(kw.get('state', ''), saved['state']):
            return _deny('no-access')
        if not saved or not secrets.compare_digest(kw.get('state', ''), saved['state']) or 'code' not in kw:
            return _deny('failed')
        try:
            discovery = _discovery()
            token = requests.post(
                discovery['token_endpoint'],
                data={
                    'grant_type': 'authorization_code',
                    'code': kw['code'],
                    'redirect_uri': _redirect_uri(),
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
            return _deny('failed')

        # As the system user for the rest of this request, not `sudo()` on the route's own environment: a public route has no user at all (uid
        # None), and Odoo flushes pending work (a new account's avatar, binding an identity) through whichever environment it finds, which
        # then fails its access checks with "Expected singleton". `authenticate` below builds its own environment, so it is unaffected.
        request.update_env(user=SUPERUSER_ID)
        users = request.env['res.users']
        user = users.search([('login', '=ilike', identity['email']), ('active', '=', True)], limit=1)
        if not user and hasattr(users, '_mos_provision_from_idp'):
            # Only when the deployment lets the identity provider decide who may use MOS (mos_platform_plus, off by default).
            user = users._mos_provision_from_idp(identity)
        if not user:
            _logger.info('OIDC sign-in refused: %s has no MOS account', identity['email'])
            return _deny('no-account', identity['email'])
        if not user._mos_bind_sso_subject(identity['sub']):
            _logger.warning('OIDC sign-in refused: the identity for %s is not the person this account is bound to', identity['email'])
            return _deny('different-person', identity['email'])
        if hasattr(user, '_mos_apply_idp_claims'):  # roles from the provider's groups, when mos_platform_plus is installed
            user._mos_apply_idp_claims(identity)
        session_token = secrets.token_urlsafe(32)
        user.write({'oauth_access_token': session_token})
        # Settle what is still to be computed (a new account's avatar, for one) with the system user's environment: the commit would otherwise
        # flush it through this route's own, which has no user and cannot pass the access check.
        users.env.flush_all()
        request.env.cr.commit()
        auth_info = authenticate(request.session, request.env, {'login': user.login, 'token': session_token, 'type': 'oauth_token'})
        response = request.redirect(_get_login_redirect_url(auth_info['uid'], saved['next']), 303)
        response.autocorrect_location_header = False
        return response

    @http.route('/mos/oidc/denied', type='http', auth='none', readonly=True)
    def denied(self, **kw):
        """Why a single sign-on did not work, in plain words, with the way out: use another account, or go back to the sign-in page."""
        ensure_db()
        state = request.session.get('mos_oidc_denied') or {'reason': 'failed', 'email': ''}
        logout_url = ''
        try:
            discovery = _discovery()
            if discovery.get('end_session_endpoint'):
                back = _param('logout_redirect_uri') or request.httprequest.url_root.rstrip('/') + '/odoo'
                logout_url = discovery['end_session_endpoint'] + '?' + urlencode({'client_id': _param('client_id'), 'post_logout_redirect_uri': back})
        except Exception:
            _logger.exception('OIDC discovery failed while showing the denied page')
        return request.make_response(_denied_page(state['reason'], state['email'], logout_url), headers=[('Content-Type', 'text/html; charset=utf-8'), ('Cache-Control', 'no-store')])

    @http.route('/mos/oidc/backchannel-logout', type='http', auth='none', methods=['POST'], csrf=False, readonly=False)
    def backchannel_logout(self, logout_token='', **kw):
        """The identity provider says a person signed out or was disabled there: end every MOS session of that person.

        400 for a token that is not valid (nothing changes); 200 otherwise, also for a person MOS never saw, so the call reveals nothing.
        """
        ensure_db()
        try:
            discovery = _discovery()
            audience = _param('client_id')
            try:
                subject = verify_logout_token(logout_token, _jwks(discovery), discovery['issuer'], audience)
            except LogoutTokenError as error:
                if 'no published key' not in str(error):
                    raise
                # a rotated key: look once more before refusing
                subject = verify_logout_token(logout_token, _jwks(discovery, force=True), discovery['issuer'], audience)
        except LogoutTokenError as error:
            _logger.warning('back-channel logout refused: %s', error)
            return request.make_json_response({'error': 'invalid_logout_token'}, status=400)
        except Exception:
            _logger.exception('back-channel logout could not be checked')
            return request.make_json_response({'error': 'temporarily_unavailable'}, status=503)
        ended = request.env['res.users'].sudo()._mos_end_sessions_of_subject(subject)
        _logger.info('back-channel logout: %s account(s) signed out', ended)
        return request.make_json_response({})
