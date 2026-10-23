from __future__ import annotations

import json
import os
import socket
from pathlib import Path

from pydantic import BaseModel, Field


def _parse_client_workspace_map() -> dict[str, dict[str, list[str]]]:
    raw = os.getenv("KNOWLEDGE_OPS_CLIENT_WORKSPACE_MAP", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "KNOWLEDGE_OPS_CLIENT_WORKSPACE_MAP must be valid JSON "
            f"(line {exc.lineno}, column {exc.colno})"
        ) from exc
    if not isinstance(value, dict):
        raise TypeError("KNOWLEDGE_OPS_CLIENT_WORKSPACE_MAP must be a JSON object")

    parsed: dict[str, dict[str, list[str]]] = {}
    for client_id, entry in value.items():
        if not isinstance(client_id, str) or not client_id.strip():
            raise ValueError("KNOWLEDGE_OPS_CLIENT_WORKSPACE_MAP client IDs must be non-empty strings")
        if not isinstance(entry, dict):
            raise TypeError("KNOWLEDGE_OPS_CLIENT_WORKSPACE_MAP entries must be objects")
        workspaces = entry.get("workspaces")
        projects = entry.get("projects", [])
        if not isinstance(workspaces, list) or not all(isinstance(item, str) and item.strip() for item in workspaces):
            raise ValueError(
                "KNOWLEDGE_OPS_CLIENT_WORKSPACE_MAP workspaces must be a list of non-empty strings"
            )
        if not isinstance(projects, list) or not all(isinstance(item, str) and item.strip() for item in projects):
            raise ValueError(
                "KNOWLEDGE_OPS_CLIENT_WORKSPACE_MAP projects must be a list of non-empty strings"
            )
        parsed[client_id.strip()] = {
            "workspaces": [item.strip() for item in workspaces],
            "projects": [item.strip() for item in projects],
        }
    return parsed


class Settings(BaseModel):
    base_dir: Path = Field(default_factory=lambda: Path(os.getenv("KNOWLEDGE_OPS_HOME", Path.home() / "Knowledge-Ops")))
    data_dir: Path = Field(default_factory=lambda: Path(os.getenv("KNOWLEDGE_OPS_DATA", Path.home() / "Knowledge-Ops" / "data")))
    wiki_dir: Path = Field(default_factory=lambda: Path(os.getenv("KNOWLEDGE_OPS_WIKI", Path.home() / "Knowledge-Ops" / "wiki")))
    db_path: Path = Field(default_factory=lambda: Path(os.getenv("KNOWLEDGE_OPS_DB", Path.home() / "Knowledge-Ops" / "data" / "knowledge_ops.sqlite3")))

    ai_memory_chunks: Path = Field(default_factory=lambda: Path(os.getenv("AI_MEMORY_CHUNKS", Path.home() / "AI-Memory" / "vectors" / "chunks.jsonl")))
    default_file_roots: list[Path] = Field(default_factory=lambda: [
        Path(os.getenv("KNOWLEDGE_OPS_FILE_ROOT", Path.home() / "Documents"))
    ])
    default_personal_owner: str = os.getenv("KNOWLEDGE_OPS_DEFAULT_PERSONAL_OWNER", "").strip()

    # The application, not the edge proxy, owns authorization. Production API
    # routes require a signed OIDC or Brigade token by default. Local compose
    # explicitly disables this while binding only to loopback.
    api_require_auth: bool = os.getenv("KNOWLEDGE_OPS_API_REQUIRE_AUTH", "1") == "1"
    api_oidc_jwks_url: str = os.getenv(
        "KNOWLEDGE_OPS_API_OIDC_JWKS_URL",
        "https://auth.magicunicorn.dev/realms/uchub/protocol/openid-connect/certs",
    )
    api_expected_audience: str = os.getenv("KNOWLEDGE_OPS_API_AUDIENCE", "knowledge-ops")
    api_trusted_issuers: list[str] = Field(default_factory=lambda: [
        i.strip() for i in os.getenv(
            "KNOWLEDGE_OPS_API_TRUSTED_ISSUERS",
            "https://auth.magicunicorn.dev/realms/uchub",
        ).split(",") if i.strip()
    ])
    api_default_oidc_scopes: list[str] = Field(default_factory=lambda: [
        s.strip() for s in os.getenv("KNOWLEDGE_OPS_API_DEFAULT_OIDC_SCOPES", "read:knowledge").split(",") if s.strip()
    ])
    api_jwks_ttl: int = int(os.getenv("KNOWLEDGE_OPS_API_JWKS_TTL", "3600"))
    api_jwks_max_stale_seconds: int = int(os.getenv(
        "KNOWLEDGE_OPS_API_JWKS_MAX_STALE_SECONDS", "300"
    ))
    api_jwt_leeway: int = int(os.getenv("KNOWLEDGE_OPS_API_JWT_LEEWAY", "60"))
    max_request_bytes: int = int(os.getenv("KNOWLEDGE_OPS_MAX_REQUEST_BYTES", "1048576"))
    rate_limit_per_minute: int = int(os.getenv("KNOWLEDGE_OPS_RATE_LIMIT_PER_MINUTE", "120"))
    client_workspace_map: dict[str, dict[str, list[str]]] = Field(default_factory=_parse_client_workspace_map)

    # The scheduler is an explicitly started, separate process. Adapter names
    # are logical identifiers only; endpoints and credentials never enter the
    # durable refresh or deletion queues.
    worker_id: str = os.getenv(
        "KNOWLEDGE_OPS_WORKER_ID", f"{socket.gethostname()}-{os.getpid()}"
    )
    worker_poll_seconds: float = float(os.getenv("KNOWLEDGE_OPS_WORKER_POLL_SECONDS", "5"))
    worker_heartbeat_seconds: int = int(os.getenv("KNOWLEDGE_OPS_WORKER_HEARTBEAT_SECONDS", "10"))
    worker_stale_seconds: int = int(os.getenv("KNOWLEDGE_OPS_WORKER_STALE_SECONDS", "45"))
    worker_lease_seconds: int = int(os.getenv("KNOWLEDGE_OPS_WORKER_LEASE_SECONDS", "60"))
    worker_retry_base_seconds: int = int(os.getenv("KNOWLEDGE_OPS_WORKER_RETRY_BASE_SECONDS", "30"))
    worker_retry_max_seconds: int = int(os.getenv("KNOWLEDGE_OPS_WORKER_RETRY_MAX_SECONDS", "3600"))
    worker_max_attempts: int = int(os.getenv("KNOWLEDGE_OPS_WORKER_MAX_ATTEMPTS", "5"))
    worker_batch_size: int = int(os.getenv("KNOWLEDGE_OPS_WORKER_BATCH_SIZE", "25"))
    require_worker: bool = os.getenv("KNOWLEDGE_OPS_REQUIRE_WORKER", "0") == "1"
    deletion_targets: dict[str, str] = Field(default_factory=lambda: json.loads(
        os.getenv("KNOWLEDGE_OPS_DELETION_TARGETS", "{}")
    ))

    # OpenWiki is an untrusted producer. It may write only to immutable staging;
    # an authorized review action promotes validated documents to the wiki.
    openwiki_staging_dir: Path = Field(default_factory=lambda: Path(os.getenv(
        "KNOWLEDGE_OPS_OPENWIKI_STAGING",
        Path(os.getenv("KNOWLEDGE_OPS_DATA", Path.home() / "Knowledge-Ops" / "data")) / "openwiki" / "staging",
    )))
    openwiki_import_roots: list[Path] = Field(default_factory=lambda: [
        Path(p).expanduser() for p in os.getenv("KNOWLEDGE_OPS_OPENWIKI_IMPORT_ROOTS", "").split(os.pathsep) if p.strip()
    ])
    openwiki_producer_version: str = os.getenv("KNOWLEDGE_OPS_OPENWIKI_VERSION", "0.2.0")

    qdrant_url: str = os.getenv("KNOWLEDGE_OPS_QDRANT_URL", "http://unicorn-qdrant:6333")
    qdrant_collections: list[str] = Field(default_factory=lambda: [
        c.strip() for c in os.getenv("KNOWLEDGE_OPS_QDRANT_COLLECTIONS", "org_memory,stable_documents").split(",") if c.strip()
    ])
    falkordb_graphs: list[str] = Field(default_factory=lambda: [
        g.strip() for g in os.getenv("KNOWLEDGE_OPS_FALKORDB_GRAPHS", os.getenv("KNOWLEDGE_OPS_FALKORDB_GRAPH", "brigade_global")).split(",") if g.strip()
    ])
    rest_sources: list[dict] = Field(default_factory=lambda: json.loads(os.getenv("KNOWLEDGE_OPS_REST_SOURCES", "[]")))
    postgres_sources: list[dict] = Field(default_factory=lambda: json.loads(os.getenv("KNOWLEDGE_OPS_POSTGRES_SOURCES", "[]")))
    embed_base_url: str = os.getenv("KNOWLEDGE_OPS_EMBED_BASE_URL", "http://127.0.0.1:8086/v1").rstrip("/")
    embed_model: str = os.getenv("KNOWLEDGE_OPS_EMBED_MODEL", "BAAI/bge-m3")
    embed_dims: int = int(os.getenv("KNOWLEDGE_OPS_EMBED_DIMS", "1024"))
    center_deep_url: str = os.getenv("KNOWLEDGE_OPS_CENTER_DEEP_URL", "http://127.0.0.1:8080")

    # FalkorDB (graph) — read-only. Backing store on bigboy: unicorn-falkordb:6379.
    falkordb_url: str = os.getenv("KNOWLEDGE_OPS_FALKORDB_URL", "redis://unicorn-falkordb:6379")
    falkordb_graph: str = os.getenv("KNOWLEDGE_OPS_FALKORDB_GRAPH", "brigade_global")

    # Unicorn Brigade Guide proxy (/kb.guide.*): entitled KO users chat with
    # Brigade agents through us. The key is a secret — it travels upstream as
    # X-API-Key only and must never appear in a response body or log line.
    # Empty key = Guide not configured (the endpoints answer 503).
    brigade_url: str = os.getenv("KNOWLEDGE_OPS_BRIGADE_URL", "http://unicorn-brigade:8100")
    brigade_key: str = os.getenv("KNOWLEDGE_OPS_BRIGADE_KEY", "")
    brigade_agent: str = os.getenv("KNOWLEDGE_OPS_BRIGADE_AGENT", "")
    # Brigade's list endpoint doesn't surface accessPolicy.key_names grants, so
    # deployments name the granted agents explicitly for the picker.
    brigade_agents: str = os.getenv("KNOWLEDGE_OPS_BRIGADE_AGENTS", "")

    # Image-baked wiki seed corpus. seed_wiki_from_disk() ingests EVERY *.md under
    # wiki_dir into the KB as scope=global owner=magic-unicorn on every startup, so a
    # single internal markdown file that reaches a customer host becomes a
    # customer-readable KB row with no human in the loop. Defaults OFF: only the
    # internal ring, which legitimately ships a corpus, sets this to 1.
    # P-00099 finding 3, 2026-09-15 — measured live on the customer deployment.
    seed_internal_corpus: bool = os.getenv("KNOWLEDGE_OPS_SEED_INTERNAL_CORPUS", "0") == "1"

    # Global rows without a workspace binding predate receiver-side project
    # visibility. Keep that legacy behavior opt-in; newly ingested unbound
    # global rows are therefore safe by default for non-admin principals.
    allow_legacy_global_visibility: bool = os.getenv(
        "KNOWLEDGE_OPS_ALLOW_LEGACY_GLOBAL_VISIBILITY", "0"
    ) == "1"

    enable_qdrant: bool = os.getenv("KNOWLEDGE_OPS_ENABLE_QDRANT", "0") == "1"
    enable_center_deep: bool = os.getenv("KNOWLEDGE_OPS_ENABLE_CENTER_DEEP", "0") == "1"
    enable_falkordb: bool = os.getenv("KNOWLEDGE_OPS_ENABLE_FALKORDB", "0") == "1"

    # Legacy compatibility setting retained for callers of brigade_auth's older
    # helper. The HTTP-MCP /mcp route always validates a Brigade JWT now.
    mcp_require_jwt: bool = os.getenv("KNOWLEDGE_OPS_MCP_REQUIRE_JWT", "0") == "1"
    mcp_jwks_url: str = os.getenv("KNOWLEDGE_OPS_MCP_JWKS_URL", "https://brigade.magicunicorn.dev/.well-known/jwks.json")
    mcp_expected_audience: str = os.getenv("KNOWLEDGE_OPS_MCP_AUDIENCE", "knowledge-ops")
    mcp_trusted_issuers: list[str] = Field(default_factory=lambda: [
        i.strip() for i in os.getenv(
            "KNOWLEDGE_OPS_MCP_TRUSTED_ISSUERS",
            "https://brigade.magicunicorn.dev,https://brigade.unicorncommander.ai",
        ).split(",") if i.strip()
    ])
    mcp_jwks_ttl: int = int(os.getenv("KNOWLEDGE_OPS_MCP_JWKS_TTL", "3600"))
    mcp_jwks_max_stale_seconds: int = int(os.getenv(
        "KNOWLEDGE_OPS_MCP_JWKS_MAX_STALE_SECONDS", "300"
    ))
    mcp_jwt_leeway: int = int(os.getenv("KNOWLEDGE_OPS_MCP_JWT_LEEWAY", "60"))
    # Emergency compatibility switch for MCP clients that cannot send a bearer
    # token during discovery. Disabled by default; tools/call never uses it.
    mcp_allow_anonymous_discovery: bool = os.getenv("KO_MCP_ALLOW_ANONYMOUS_DISCOVERY", "false").lower() == "true"

    def ensure_dirs(self) -> None:
        for directory in (self.data_dir, self.wiki_dir, self.openwiki_staging_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)


settings = Settings()
