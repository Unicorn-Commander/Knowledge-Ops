from __future__ import annotations

import os
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

    qdrant_url: str = os.getenv("KNOWLEDGE_OPS_QDRANT_URL", "http://unicorn-qdrant:6333")
    qdrant_collections: list[str] = Field(default_factory=lambda: [
        c.strip() for c in os.getenv("KNOWLEDGE_OPS_QDRANT_COLLECTIONS", "org_memory,stable_documents").split(",") if c.strip()
    ])
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

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.wiki_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
