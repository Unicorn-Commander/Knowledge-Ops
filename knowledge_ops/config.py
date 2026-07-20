from __future__ import annotations

import os
import json
from pathlib import Path
from pydantic import BaseModel, Field


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
    api_jwt_leeway: int = int(os.getenv("KNOWLEDGE_OPS_API_JWT_LEEWAY", "60"))
    max_request_bytes: int = int(os.getenv("KNOWLEDGE_OPS_MAX_REQUEST_BYTES", "1048576"))
    rate_limit_per_minute: int = int(os.getenv("KNOWLEDGE_OPS_RATE_LIMIT_PER_MINUTE", "120"))

    # OpenWiki is an untrusted producer. It may write only to immutable staging;
    # an authorized review action promotes validated documents to the wiki.
    openwiki_staging_dir: Path = Field(default_factory=lambda: Path(os.getenv(
        "KNOWLEDGE_OPS_OPENWIKI_STAGING",
        Path(os.getenv("KNOWLEDGE_OPS_DATA", Path.home() / "Knowledge-Ops" / "data")) / "openwiki" / "staging",
    )))
    openwiki_import_roots: list[Path] = Field(default_factory=lambda: [
        Path(p).expanduser() for p in os.getenv("KNOWLEDGE_OPS_OPENWIKI_IMPORT_ROOTS", "").split(os.pathsep) if p.strip()
    ])
    openwiki_producer_version: str = os.getenv("KNOWLEDGE_OPS_OPENWIKI_VERSION", "0.1.0")

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

    enable_qdrant: bool = os.getenv("KNOWLEDGE_OPS_ENABLE_QDRANT", "0") == "1"
    enable_center_deep: bool = os.getenv("KNOWLEDGE_OPS_ENABLE_CENTER_DEEP", "0") == "1"
    enable_falkordb: bool = os.getenv("KNOWLEDGE_OPS_ENABLE_FALKORDB", "0") == "1"

    # HTTP-MCP /mcp Brigade-JWT gate. Off by default (local/tests/standalone need
    # no token); set =1 on the public deploy where oauth2-proxy whitelists /mcp so
    # the broker reaches us — then the Brigade JWT is enforced here. Defaults match
    # .well-known/mcps.json auth.federated_auth.
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
    mcp_jwt_leeway: int = int(os.getenv("KNOWLEDGE_OPS_MCP_JWT_LEEWAY", "60"))

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        self.openwiki_staging_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
