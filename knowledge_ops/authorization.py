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
    projects: frozenset[str] = field(default_factory=frozenset)

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


def _claim_ids(*values: Any) -> set[str]:
    """Extract bounded identifier claims without trusting arbitrary nested data."""
    found: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            value = value.get("id") or value.get("value")
        found |= _strings(value)
    return found


def normalize_visibility(
    value: Any = None,
    *,
    metadata: Any = None,
) -> dict[str, str] | None:
    """Normalize an optional receiver-side workspace/project binding.

    Producers may send the identifiers directly in source metadata or under an
    explicit ``visibility`` object. A project without its workspace is
    ambiguous and is rejected rather than treated as globally visible.
    """
    raw: dict[str, Any] = {}
    for candidate in (metadata,):
        if not isinstance(candidate, dict):
            continue
        nested = candidate.get("visibility")
        if nested is not None:
            if not isinstance(nested, dict):
                raise ValueError("visibility metadata must be an object")
            raw.update(nested)
        for key in ("workspace_id", "project_id"):
            if key in candidate:
                raw[key] = candidate[key]
    if value is not None:
        if not isinstance(value, dict):
            raise ValueError("visibility must be an object")
        raw.update(value)

    present = {key for key in ("workspace_id", "project_id") if key in raw}
    if not present:
        return None
    workspace_id = str(raw.get("workspace_id") or "").strip()
    project_id = str(raw.get("project_id") or "").strip()
    if not workspace_id:
        raise ValueError("visibility.workspace_id is required")
    if any(len(item) > 200 or any(ord(char) < 32 for char in item) for item in (workspace_id, project_id) if item):
        raise ValueError("visibility identifiers are invalid")
    binding = {"workspace_id": workspace_id}
    if project_id:
        binding["project_id"] = project_id
    return binding


def record_visibility(record: dict[str, Any]) -> dict[str, str] | None:
    """Read a canonical or adapter record's normalized visibility binding."""
    metadata = record.get("metadata")
    explicit = record.get("visibility")
    if explicit is None and (record.get("visibility_workspace_id") or record.get("visibility_project_id")):
        explicit = {
            "workspace_id": record.get("visibility_workspace_id"),
            "project_id": record.get("visibility_project_id"),
        }
    return normalize_visibility(explicit, metadata=metadata)


def can_read_visibility(
    principal: Principal,
    binding: dict[str, str] | None,
    *,
    scope: str,
) -> bool:
    """Apply the receiver-side workspace/project gate to one record."""
    if principal.is_admin:
        return True
    if binding is None:
        return scope != "global" or settings.allow_legacy_global_visibility
    workspace_id = binding["workspace_id"]
    if workspace_id not in principal.workspaces:
        return False
    project_id = binding.get("project_id")
    return not project_id or project_id in principal.projects


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
    workspace_claim = claims.get("workspace")
    workspaces = _claim_ids(claims.get("workspace_id"), claims.get("workspaces"), workspace_claim)
    projects = _claim_ids(
        claims.get("project_id"), claims.get("project_ids"), claims.get("projects"),
        claims.get("project"),
    )
    return Principal(
        subject=subject,
        scopes=frozenset(scopes),
        roles=frozenset(roles),
        identities=frozenset(identities),
        workspaces=frozenset(workspaces),
        issuer=str(claims.get("iss") or ""),
        projects=frozenset(projects),
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
    try:
        binding = record_visibility(passage.__dict__)
    except ValueError:
        return False
    if not can_read_visibility(principal, binding, scope=passage.scope):
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
    workspace_column: str = "visibility_workspace_id",
    project_column: str = "visibility_project_id",
) -> tuple[str, tuple[str, ...]]:
    """Build a parameterized native-store visibility predicate.

    Column names are supplied only by application code. All identity values are
    bound parameters, so native stores do not load another owner's rows before
    the defense-in-depth passage filter runs.
    """
    principal.require("read:knowledge")
    validate_requested_scope(principal, requested_scope)
    if principal.is_admin:
        if requested_scope in (None, "", "all"):
            return "1=1", ()
        return f"{scope_column}=?", (requested_scope,)

    workspace_ids = tuple(sorted(identity for identity in principal.workspaces if identity))
    if workspace_ids:
        workspace_slots = ",".join("?" for _ in workspace_ids)
        project_ids = tuple(sorted(identity for identity in principal.projects if identity))
        if project_ids:
            project_slots = ",".join("?" for _ in project_ids)
            project_match = f"{project_column} IS NULL OR {project_column} IN ({project_slots})"
            project_params = project_ids
        else:
            project_match = f"{project_column} IS NULL"
            project_params = ()
        bound = f"({workspace_column} IN ({workspace_slots}) AND ({project_match}))"
        bound_params = (*workspace_ids, *project_params)
    else:
        bound = "0=1"
        bound_params = ()
    legacy = "1=1" if settings.allow_legacy_global_visibility else "0=1"
    binding = (
        f"(({workspace_column} IS NULL AND {project_column} IS NULL AND "
        f"({scope_column} != 'global' OR {legacy})) OR {bound})"
    )

    def scoped(clause: str, clause_params: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
        return f"({clause} AND {binding})", (*clause_params, *bound_params)

    if requested_scope == "global":
        return scoped(f"{scope_column}=?", ("global",))
    if requested_scope and str(requested_scope).startswith("workspace:"):
        return scoped(f"{scope_column}=?", (requested_scope,))
    owners = tuple(sorted(identity.lower() for identity in principal.identities if identity))
    if not owners:
        return "0=1", ()
    owner_slots = ",".join("?" for _ in owners)
    personal = f"({scope_column}=? AND lower({owner_column}) IN ({owner_slots}))"
    if requested_scope == "personal":
        return scoped(personal, ("personal", *owners))
    global_clause, global_params = scoped(f"{scope_column}=?", ("global",))
    personal_clause, personal_params = scoped(personal, ("personal", *owners))
    return f"({global_clause} OR {personal_clause})", (*global_params, *personal_params)


def can_read_record(principal: Principal, record: dict[str, Any], requested_scope: str | None = None) -> bool:
    metadata = dict(record.get("metadata") or {})
    if record.get("visibility") is not None:
        metadata["visibility"] = record["visibility"]
    elif record.get("visibility_workspace_id") or record.get("visibility_project_id"):
        metadata["visibility"] = {
            "workspace_id": record.get("visibility_workspace_id"),
            "project_id": record.get("visibility_project_id"),
        }
    passage = Passage(
        source="authorization",
        source_id=str(record.get("id") or ""),
        title="",
        text="",
        score=0,
        scope=str(record.get("scope") or "restricted"),
        owner=str(record.get("owner") or ""),
        metadata=metadata,
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
