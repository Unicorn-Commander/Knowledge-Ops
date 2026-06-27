"""Brigade federation JWT verification for the HTTP-MCP surface.

The Brigade broker proxies ``tools/call`` to our public ``mcp_endpoint`` with an
``Authorization: Bearer <jwt>`` header — a short-lived RS256 token minted by
Brigade (``iss`` = Brigade, ``aud`` = our ``expected_audience``), exactly the
contract advertised in ``.well-known/mcps.json`` ``auth.federated_auth``.

When the public surface is gated by oauth2-proxy we whitelist ``/mcp`` at the
proxy (so the broker's request reaches us) and enforce the Brigade JWT *here*
instead — otherwise a whitelisted ``/mcp`` would re-open anonymous tool calls.

JWKS is fetched with ``httpx`` (a core dep) and a real ``User-Agent`` — Brigade's
nginx 403s the default ``Python-urllib`` UA, which is why we don't use PyJWT's
``PyJWKClient`` (it fetches via urllib). Keys are cached by ``kid`` with a TTL and
refreshed on a cache miss. Verification is **lazy + opt-in**: nothing is fetched
unless ``KNOWLEDGE_OPS_MCP_REQUIRE_JWT=1``, so local runs, the test-suite, and the
standalone (no-proxy) deployment need no token and make no network calls.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

import httpx

from .config import settings

logger = logging.getLogger("knowledge_ops.auth")

# Brigade's edge (nginx) rejects the default urllib User-Agent with 403; send a real one.
_USER_AGENT = "knowledge-ops/0.1 (+https://knowledge-ops.magicunicorn.dev)"

_jwks_lock = threading.Lock()
_jwks_keys: dict[str, dict] = {}   # kid -> JWK dict
_jwks_fetched_at: float = 0.0


class AuthError(Exception):
    """Raised when an enforced Brigade JWT is missing or invalid (-> HTTP 401)."""


def _fetch_jwks() -> dict[str, dict]:
    resp = httpx.get(settings.mcp_jwks_url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"}, timeout=10)
    resp.raise_for_status()
    return {k["kid"]: k for k in resp.json().get("keys", []) if k.get("kid")}


def _signing_key_for(kid: str):
    """Return a public key object for ``kid`` from Brigade's JWKS (cached, refresh-on-miss)."""
    import jwt as pyjwt

    global _jwks_fetched_at, _jwks_keys
    with _jwks_lock:
        fresh = (time.time() - _jwks_fetched_at) < settings.mcp_jwks_ttl
        if kid not in _jwks_keys or not fresh:
            try:
                _jwks_keys = _fetch_jwks()
                _jwks_fetched_at = time.time()
            except Exception as exc:
                if kid not in _jwks_keys:  # nothing usable cached -> fail closed
                    raise AuthError(f"jwks fetch failed: {exc}") from exc
        jwk = _jwks_keys.get(kid)
    if jwk is None:
        raise AuthError(f"unknown signing key kid={kid}")
    return pyjwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))


def bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def verify_brigade_jwt(token: Optional[str]) -> dict:
    """Validate a Brigade-minted RS256 JWT. Returns the claims or raises AuthError."""
    if not token:
        raise AuthError("missing bearer token")
    try:
        import jwt as pyjwt
    except ModuleNotFoundError as exc:  # pragma: no cover - image always ships pyjwt
        raise AuthError("jwt verification unavailable") from exc

    try:
        header = pyjwt.get_unverified_header(token)
    except Exception as exc:
        raise AuthError(f"malformed token: {exc}") from exc
    kid = header.get("kid")
    if not kid:
        raise AuthError("token missing kid")

    key = _signing_key_for(kid)
    try:
        claims = pyjwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=settings.mcp_expected_audience,
            options={"require": ["exp", "iat", "aud"]},
            leeway=settings.mcp_jwt_leeway,
        )
    except Exception as exc:  # pyjwt raises a family of exceptions; collapse to AuthError
        raise AuthError(f"invalid token: {exc}") from exc

    iss = claims.get("iss")
    if settings.mcp_trusted_issuers and iss not in settings.mcp_trusted_issuers:
        raise AuthError(f"untrusted issuer: {iss}")
    return claims


def require_for_tool_call(authorization: Optional[str]) -> Optional[dict]:
    """Gate a tools/call. No-op unless ``mcp_require_jwt`` is enabled.

    Returns the verified claims (or None when enforcement is off). Raises
    AuthError when enforcement is on and the token is absent/invalid.
    """
    if not settings.mcp_require_jwt:
        return None
    claims = verify_brigade_jwt(bearer_token(authorization))
    logger.info("brigade mcp tools/call authorized: sub=%s jti=%s", claims.get("sub"), claims.get("jti"))
    return claims
