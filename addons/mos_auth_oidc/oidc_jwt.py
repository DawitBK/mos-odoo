"""Verification of an OpenID Connect logout token (Back-Channel Logout 1.0) with nothing but `cryptography`.

The token reaches MOS over the network, not from the token endpoint, so its signature is checked against the keys the identity provider
publishes. Only RS256 is accepted; the issuer, audience, type, age and event are all required; a token that carries a nonce is refused
(that would be an ID token in disguise). Pure functions: the caller does the fetching of the keys.
"""

import base64
import json
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

BACKCHANNEL_LOGOUT_EVENT = 'http://schemas.openid.net/event/backchannel-logout'
MAX_AGE_SECONDS = 300
LEEWAY_SECONDS = 30


class LogoutTokenError(ValueError):
    """The logout token is not acceptable; the message says why (for the log, never for the caller)."""


def _b64url_decode(part):
    return base64.urlsafe_b64decode(part + '=' * (-len(part) % 4))


def _json_part(part, what):
    try:
        value = json.loads(_b64url_decode(part))
    except Exception as error:
        raise LogoutTokenError(f'the {what} is not readable') from error
    if not isinstance(value, dict):
        raise LogoutTokenError(f'the {what} is not an object')
    return value


def _public_key(jwk):
    try:
        numbers = rsa.RSAPublicNumbers(
            int.from_bytes(_b64url_decode(jwk['e']), 'big'),
            int.from_bytes(_b64url_decode(jwk['n']), 'big'),
        )
        return numbers.public_key()
    except Exception as error:
        raise LogoutTokenError('the signing key is not a usable RSA key') from error


def verify_logout_token(token, keys, issuer, audience, now=None):
    """Return the `sub` of a valid logout token, or raise LogoutTokenError.

    keys: the `keys` list of the provider's JWKS document.
    """
    now = time.time() if now is None else now
    parts = str(token or '').split('.')
    if len(parts) != 3 or not all(parts) or len(token) > 8192:
        raise LogoutTokenError('not a compact signed token')
    header = _json_part(parts[0], 'header')
    if header.get('alg') != 'RS256':
        raise LogoutTokenError('only RS256 is accepted')
    if header.get('typ') != 'logout+jwt':
        raise LogoutTokenError('the token type is not logout+jwt')
    jwk = next((k for k in keys if k.get('kid') == header.get('kid') and k.get('kty') == 'RSA'), None)
    if jwk is None:
        raise LogoutTokenError('no published key matches the token')
    try:
        _public_key(jwk).verify(_b64url_decode(parts[2]), f'{parts[0]}.{parts[1]}'.encode(), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as error:
        raise LogoutTokenError('the signature does not verify') from error

    claims = _json_part(parts[1], 'payload')
    if str(claims.get('iss', '')).rstrip('/') != str(issuer).rstrip('/'):
        raise LogoutTokenError('wrong issuer')
    aud = claims.get('aud')
    if audience not in (aud if isinstance(aud, list) else [aud]):
        raise LogoutTokenError('wrong audience')
    iat = claims.get('iat')
    if not isinstance(iat, (int, float)) or iat > now + LEEWAY_SECONDS or now - iat > MAX_AGE_SECONDS + LEEWAY_SECONDS:
        raise LogoutTokenError('the token is not recent')
    exp = claims.get('exp')
    if exp is not None and (not isinstance(exp, (int, float)) or exp + LEEWAY_SECONDS < now):
        raise LogoutTokenError('the token has expired')
    events = claims.get('events')
    if not isinstance(events, dict) or BACKCHANNEL_LOGOUT_EVENT not in events:
        raise LogoutTokenError('the logout event is missing')
    if 'nonce' in claims:
        raise LogoutTokenError('a logout token carries no nonce')
    sub = claims.get('sub')
    if not isinstance(sub, str) or not sub:
        raise LogoutTokenError('no subject')
    return sub
