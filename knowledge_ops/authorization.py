"""Verified identities and document-level authorization policy.

External callers never choose an owner or broaden their own visibility. Both
REST and HTTP-MCP turn a cryptographically verified token into the same
``Principal`` and the federation layer applies this policy after every adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import threading
import time
from typing import Any, Iterable

import httpx

from . import __version__
from .brigade_auth import AuthError, bearer_token, verify_brigade_jwt
from .config import settings
from .models import Passage

logger = logging.getLogger("knowledge_ops.authorization")


class AuthorizationError(Exception):
    """An authenticated principal is not allowed to perform an operation."""


@dataclass(frozen=True)
class Principal:
    subject: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    roles: frozenset[str] = field(default_factory=frozenset)
    identities: frozenset[str] = field(default_factory=frozenset)
    workspaces: frozenset[str] = field(default_factory=frozenset)
    issuer: str = ""
    authenticated: bool = True

    @property
    def is_admin(self) -> bool:
        return "admin:knowledge" in self.scopes or "knowledge-admin" in self.roles

    def has_scope(self, required: str) -> bool:
        if self.is_admin or required in self.scopes:
            return True
        namespace = required.split(":", 1)[0]
        return "*" in self.scopes or f"{namespace}:*" in self.scopes

    def require(self, *required: str) -> None:
        missing = [scope for scope in required if not self.has_scope(scope)]
        if missing:
            raise AuthorizationError("missing required scope: " + ", ".join(missing))


def system_principal() -> Principal:
    """Explicit local/stdio identity; never used when HTTP auth is enabled."""
    return Principal(
        subject="local-system",
        scopes=frozenset({"admin:knowledge", "read:knowledge", "write:knowledge", "write:knowledge:global"}),
        roles=frozenset({"knowledge-admin"}),
        identities=frozenset({"local-system"}),
        issuer="local",
    )


def _strings(value: Any) -> set[str]:
    if isinstance(value, str):
        return {item for item in value.replace(",", " ").split() if item}
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value if item}
    return set()


def principal_from_claims(claims: dict[str, Any], *, oidc: bool = False) -> Principal:
    subject = str(claims.get("sub") or "").strip()
    if not subject:
        raise AuthorizationError("verified token is missing sub")

    scopes = _strings(claims.get("scope")) | _strings(claims.get("scp")) | _strings(claims.get("scopes"))
    roles = _strings((claims.get("realm_access") or {}).get("roles"))
    resource_access = claims.get("resource_access") or {}
    for client in (settings.api_expected_audience, settings.mcp_expected_audience):
        roles |= _strings((resource_access.get(client) or {}).get("roles"))
    roles |= _strings(claims.get("roles"))

    if oidc:
        scopes |= set(settings.api_default_oidc_scopes)
        if "knowledge-reader" in roles:
            scopes.add("read:knowledge")
        if "knowledge-editor" in roles:
            scopes.add("write:knowledge")
        if "knowledge-admin" in roles:
            scopes |= {"admin:knowledge", "write:knowledge:global", "read:knowledge:restricted"}

    identities = {subject.lower()}
    identity_keys = ("email", "preferred_username") if not oidc else ()
    if oidc and claims.get("email_verified") is True:
        identity_keys = ("email",)
    for key in identity_keys:
        value = str(claims.get(key) or "").strip().lower()
        if value:
            identities.add(value)
    workspaces = _strings(claims.get("workspace_id")) | _strings(claims.get("workspaces"))
    return Principal(
        subject=subject,
        scopes=frozenset(scopes),
        roles=frozenset(roles),
        identities=frozenset(identities),
        workspaces=frozenset(workspaces),
        issuer=str(claims.get("iss") or ""),
    )


_oidc_lock = threading.Lock()
_oidc_keys: dict[str, dict] = {}
_oidc_fetched_at = 0.0
_oidc_stale_fallback_total = 0


def oidc_stale_fallback_total() -> int:
    return _oidc_stale_fallback_total


def _fetch_oidc_keys() -> dict[str, dict]:
    response = httpx.get(
        settings.api_oidc_jwks_url,
        headers={"User-Agent": f"knowledge-ops/{__version__}", "Accept": "application/json"},
        timeout=10,
    )
    response.raise_for_status()
    return {key["kid"]: key for key in response.json().get("keys", []) if key.get("kid")}


def _oidc_signing_key(kid: str):
    import jwt as pyjwt

    global _oidc_keys, _oidc_fetched_at, _oidc_stale_fallback_total
    with _oidc_lock:
        current = time.time()
        age = max(0.0, current - _oidc_fetched_at)
        ttl = max(0, settings.api_jwks_ttl)
        fresh = age < ttl
        if kid not in _oidc_keys or not fresh:
            try:
                _oidc_keys = _fetch_oidc_keys()
                _oidc_fetched_at = time.time()
            except Exception as exc:
                max_age = ttl + max(0, settings.api_jwks_max_stale_seconds)
                fallback_age = max(0.0, time.time() - _oidc_fetched_at)
                if kid not in _oidc_keys or fallback_age > max_age:
                    raise AuthorizationError("OIDC signing keys are unavailable") from exc
                _oidc_stale_fallback_total += 1
        key = _oidc_keys.get(kid)
    if not key:
        raise AuthorizationError("OIDC token uses an unknown signing key")
    return pyjwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key))


def verify_oidc_jwt(token: str) -> dict[str, Any]:
    import jwt as pyjwt

    try:
        header = pyjwt.get_unverified_header(token)
        kid = str(header.get("kid") or "")
        if not kid:
            raise AuthorizationError("OIDC token is missing kid")
        claims = pyjwt.decode(
            token,
            _oidc_signing_key(kid),
            algorithms=["RS256"],
            audience=settings.api_expected_audience,
            issuer=settings.api_trusted_issuers or None,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
            leeway=settings.api_jwt_leeway,
        )
    except AuthorizationError:
        raise
    except Exception as exc:
        raise AuthorizationError("invalid OIDC token") from exc
    return claims


def principal_from_authorization(authorization: str | None, *, require_auth: bool = True) -> Principal:
    if not require_auth:
        return system_principal()
    token = bearer_token(authorization)
    if not token:
        raise AuthorizationError("missing bearer token")

    try:
        import jwt as pyjwt
        unverified = pyjwt.decode(token, options={"verify_signature": False, "verify_aud": False})
    except Exception as exc:
        raise AuthorizationError("malformed bearer token") from exc
    issuer = str(unverified.get("iss") or "")
    if issuer in settings.mcp_trusted_issuers:
        try:
            return principal_from_claims(verify_brigade_jwt(token))
        except AuthError as exc:
            raise AuthorizationError("invalid Brigade token") from exc
    if issuer in settings.api_trusted_issuers:
        return principal_from_claims(verify_oidc_jwt(token), oidc=True)
    raise AuthorizationError("token issuer is not trusted")


def require_tool_principal(authorization: str | None) -> Principal:
    token = bearer_token(authorization)
    try:
        claims = verify_brigade_jwt(token)
    except AuthError as exc:
        raise AuthorizationError("invalid Brigade token") from exc
    return principal_from_claims(claims)


def validate_requested_scope(principal: Principal, requested_scope: str | None) -> None:
    if requested_scope not in (None, "", "global", "personal", "all") and not str(requested_scope).startswith("workspace:"):
        raise AuthorizationError("unsupported knowledge scope")
    if requested_scope == "all" and not principal.is_admin:
        raise AuthorizationError("scope=all is reserved for knowledge administrators")
    if requested_scope == "personal" and not principal.subject:
        raise AuthorizationError("personal scope requires an identity")
    if requested_scope and str(requested_scope).startswith("workspace:"):
        workspace = str(requested_scope).split(":", 1)[1]
        if workspace not in principal.workspaces and not principal.is_admin:
            raise AuthorizationError("workspace is not assigned to this identity")


def can_read_passage(principal: Principal, passage: Passage, requested_scope: str | None = None) -> bool:
    if not principal.has_scope("read:knowledge"):
        return False
    validate_requested_scope(principal, requested_scope)
    if requested_scope in ("global", "personal") and passage.scope != requested_scope:
        return False
    if requested_scope and str(requested_scope).startswith("workspace:") and passage.scope != requested_scope:
        return False
    if passage.scope == "global":
        return True
    if passage.scope == "personal":
        return principal.is_admin or bool({passage.owner.lower()} & set(principal.identities))
    if passage.scope.startswith("workspace:"):
        return principal.is_admin or passage.scope.split(":", 1)[1] in principal.workspaces
    if passage.scope == "restricted":
        return principal.is_admin or principal.has_scope("read:knowledge:restricted")
    return False


def filter_passages(principal: Principal, passages: Iterable[Passage], requested_scope: str | None = None) -> list[Passage]:
    return [passage for passage in passages if can_read_passage(principal, passage, requested_scope)]


def sqlite_visibility_clause(
    principal: Principal,
    requested_scope: str | None,
    *,
    scope_column: str = "scope",
    owner_column: str = "owner",
) -> tuple[str, tuple[str, ...]]:
    """Build a parameterized native-store visibility predicate.

    Column names are supplied only by application code. All identity values are
    bound parameters, so native stores do not load another owner's rows before
    the defense-in-depth passage filter runs.
    """
    principal.require("read:knowledge")
    validate_requested_scope(principal, requested_scope)
    if principal.is_admin and requested_scope in (None, "", "all"):
        return "1=1", ()
    if requested_scope == "global":
        return f"{scope_column}=?", ("global",)
    owners = tuple(sorted(identity.lower() for identity in principal.identities if identity))
    if not owners:
        return "0=1", ()
    owner_slots = ",".join("?" for _ in owners)
    personal = f"({scope_column}=? AND lower({owner_column}) IN ({owner_slots}))"
    if requested_scope == "personal":
        return personal, ("personal", *owners)
    return f"({scope_column}=? OR {personal})", ("global", "personal", *owners)


def can_read_record(principal: Principal, record: dict[str, Any], requested_scope: str | None = None) -> bool:
    passage = Passage(
        source="authorization",
        source_id=str(record.get("id") or ""),
        title="",
        text="",
        score=0,
        scope=str(record.get("scope") or "restricted"),
        owner=str(record.get("owner") or ""),
    )
    return can_read_passage(principal, passage, requested_scope)


def authorized_write_target(principal: Principal, scope: str | None, owner: str | None = None) -> tuple[str, str]:
    principal.require("write:knowledge")
    target_scope = (scope or "personal").strip()
    if target_scope == "personal":
        # Caller-provided ownership is never trusted. Personal writes belong to sub.
        return target_scope, principal.subject
    if target_scope == "global":
        if not (principal.is_admin or principal.has_scope("write:knowledge:global")):
            raise AuthorizationError("global writes require write:knowledge:global")
        return target_scope, owner or principal.subject
    raise AuthorizationError("documents may be written only to personal or global scope")
