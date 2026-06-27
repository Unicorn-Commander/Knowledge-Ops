# Knowledge-Ops v0

Knowledge-Ops is the Magic Unicorn federation and grounding layer. Agents call `kb.search` or `kb.ground`; the service decides which stores to query, merges evidence, enforces scope, and returns cited passages.

## Federation Map

| Source | v0 adapter | Backing store | Scope notes |
|---|---|---|---|
| `org_memory` | `adapters/ai_memory.py` | `~/AI-Memory/vectors/chunks.jsonl` now, Qdrant/Falkor live later | Preserves `personal` vs `global` from payload |
| `wiki` | `wiki.py` | Markdown under `wiki/` plus SQLite index | Default `global` |
| `file_index` | `file_index.py` | SQLite chunks over designated roots | Default `personal`; portable to Cognitive Companion |
| `metrics` | `metrics.py` | SQLite timeseries plus SVG chart rendering | Global/internal metrics |
| `qdrant` | `adapters/qdrant.py` | `org_memory`, `stable_documents`, etc. | Disabled by default; set `KNOWLEDGE_OPS_ENABLE_QDRANT=1` |
| `center_deep` | `adapters/center_deep.py` | Center Deep `/search` | Disabled by default; set `KNOWLEDGE_OPS_ENABLE_CENTER_DEEP=1` |

All embedding config is centralized in `knowledge_ops/config.py` and defaults to `BAAI/bge-m3`, 1024 dimensions, cosine-compatible with the existing Infinity/Qdrant stores.

## Tools

MCP tool names mirror the requested interface:

- `kb.search(query, scope?, sources?, k?)`
- `kb.ground(claim, scope?, k?)`
- `kb.list_sources()`
- `kb.get_doc(id)`
- `kb.upsert_doc(path, md)`
- `kb.list_docs()`
- `kb.doc_history(id)`
- `kb.record_metric(name, value, tags)`
- `kb.get_metric_chart(name, range)`
- `kb.find_file(query)`
- `kb.deep_read(path, query)`

REST exposes the same names as thin endpoints, for example `POST /kb.search`.

## First Run

```bash
cd ~/Knowledge-Ops
python3 -m knowledge_ops.cli init
python3 -m knowledge_ops.cli ingest-marketing
python3 -m knowledge_ops.cli index-files ~/Documents --limit 120
python3 -m knowledge_ops.cli demo
python3 -m uvicorn knowledge_ops.api:app --host 127.0.0.1 --port 8765
```

OpenAPI is available at `http://127.0.0.1:8765/docs`.

## REST Routes (actual)

Routes are **dot-style** (`/kb.<tool>`), mirroring the MCP tool names. There is no
`/kb/ground` or `/ground` (those 404). Full list:

| Method | Path | Notes |
|---|---|---|
| GET  | `/health` | Liveness — `{"ok": true}` |
| GET  | `/` | Redirects to the web GUI at `/ui/` |
| GET  | `/ui/` | **Web GUI** (Ask/Ground, Wiki, Files, Metrics, Sources) |
| GET  | `/info` | Service info JSON |
| POST | `/mcp` | **HTTP-MCP JSON-RPC** (`initialize`/`tools/list`/`tools/call`) for the Brigade broker |
| GET  | `/.well-known/mcps.json` | Brigade federation manifest |
| GET  | `/docs` | OpenAPI / Swagger |
| POST | `/kb.search` | body: `{query, scope?, sources?, k?}` |
| POST | `/kb.ground` | body: `{query, scope?, k?}` → verdict + grounded answer + cited passages |
| GET  | `/kb.list_sources` | source catalog + enabled/disabled status |
| GET  | `/kb.docs` | list wiki docs |
| GET  | `/kb.get_doc?id=...` | one wiki doc |
| POST | `/kb.upsert_doc` | body: `{path, md, scope?, owner?}` |
| GET  | `/kb.doc_history?id=...` | wiki versions |
| POST | `/kb.ingest_marketing_docs` | re-ingest the brand bible from `~/Documents` |
| POST | `/kb.record_metric` | body: `{name, value, tags?}` |
| GET  | `/kb.get_metric_chart?name=...` | SVG chart |
| GET  | `/kb.metrics?name=...` | metric rows |
| POST | `/kb.index_files?root=...&limit=...` | index a directory tree |
| GET  | `/kb.find_file?query=...&k=...&scope=...` | file search |
| GET  | `/kb.deep_read?path=...&query=...` | all chunks of one file |

Verified grounded call (returns evidence + citations):

```bash
curl -s -X POST http://127.0.0.1:8765/kb.ground \
  -H "Content-Type: application/json" \
  -d '{"query":"What is Magic Unicorn?","scope":"global","k":6}'
# => {"claim":"What is Magic Unicorn?","verdict":"evidence_found",
#     "answer":"Grounded summary: Magic Unicorn builds **sovereign AI you actually own** ...
#               Citations:\n[1] wiki:Magic Unicorn — Marketing & Brand Bible (v1) ...",
#     "passages":[ ... cited Passage objects ... ]}
```

## Web GUI

The FastAPI app serves its own single-page GUI at **`/ui/`** (static files under
`knowledge_ops/static/`, vanilla JS — no build step, no CDN). `/` redirects there.
On-brand Magic Unicorn (dark + purple/gradient, tokens from `magic-unicorn-ui`).
Sections: **Ask/Ground** (hero — cited answers with source/scope/owner clearly
attributed), **Wiki** (list/view/edit/history), **Files** (find + deep-read),
**Metrics** (SVG charts), **Sources/Status** (federated sources + the registered
MCP tools from the manifest).

## HTTP-MCP (`POST /mcp`)

`POST /mcp` speaks MCP JSON-RPC (`initialize`, `tools/list`, `tools/call`, `ping`)
so the Brigade broker's **brokered execution** works, not just registration.
`tools/call` dispatches to the same functions the REST routes use; the tool
catalog is single-sourced from `.well-known/mcps.json`. Verify:

```bash
curl -s -X POST http://127.0.0.1:8765/mcp -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

When `KNOWLEDGE_OPS_MCP_REQUIRE_JWT=1` (set on the public bigboy deploy), every
`tools/call` must carry a valid Brigade-minted RS256 JWT (`aud=knowledge-ops`,
issuer in `KNOWLEDGE_OPS_MCP_TRUSTED_ISSUERS`, verified against
`KNOWLEDGE_OPS_MCP_JWKS_URL`) — exactly the contract in the manifest's
`auth.federated_auth`. `initialize`/`tools/list`/`ping` stay open (public
metadata). Off by default, so local/tests/standalone need no token.

## Public auth gate (oauth2-proxy + Keycloak)

On bigboy the public host is fronted by `knowledge-ops-oauth2-proxy`
(Keycloak `uchub` realm, Magic Unicorn SSO) — see `docker-compose.bigboy.yml`.
Traefik routes the host to the proxy (`:4180`), which proxies to
`http://knowledge-ops:8765` after SSO. **Whitelisted (public): `/health`,
`/.well-known/mcps.json`, `/mcp`** — so monitoring, the broker's manifest fetch,
and brokered execution keep working (the app enforces the Brigade JWT on `/mcp`
itself). Humans hitting `/ui` or `/kb.*` get Magic Unicorn SSO. The **in-cluster**
`http://knowledge-ops:8765` is unchanged/open for agents (the proxy only sits on
the Traefik path). Revert the gate by restoring the pre-auth compose backup and
deleting the Keycloak `knowledge-ops` client.

## Deploy & Persistence

The service is a single FastAPI app on **port 8765**. SQLite lives in a mounted
volume so data survives restarts/redeploys. On startup it ensures the schema and
**self-seeds the wiki** from any markdown under `wiki/` (so a fresh volume answers
grounded queries with no manual ingest).

**Docker Compose (preferred — Mac/standalone):**
```bash
docker compose up -d --build      # http://127.0.0.1:8765 , volume knowledge_ops_data
docker compose logs -f knowledge-ops
docker compose down               # data persists in the named volume
```

**Docker Compose (bigboy — next to the stores + Brigade, live adapters ON):**
```bash
docker compose -f docker-compose.yml -f docker-compose.bigboy.yml up -d --build
# joins external unicorn-network; enables Qdrant + FalkorDB read-only adapters
# and points embeddings at unicorn-infinity-proxy:8080 (bge-m3).
```

**systemd (fallback, no Docker):** see `deploy/knowledge-ops.service`. Install with a
venv at `/opt/knowledge-ops/.venv` (`pip install '.[all]'`), then
`systemctl enable --now knowledge-ops`.

## Live Adapters (read-only)

The real stores run on bigboy on `unicorn-network`. Adapters are **off by default**
and **never crash** if a store is unreachable (they degrade to empty results).

| Adapter | Env to enable | URL env (in-network default) |
|---|---|---|
| Qdrant | `KNOWLEDGE_OPS_ENABLE_QDRANT=1` | `KNOWLEDGE_OPS_QDRANT_URL=http://unicorn-qdrant:6333` |
| FalkorDB | `KNOWLEDGE_OPS_ENABLE_FALKORDB=1` | `KNOWLEDGE_OPS_FALKORDB_URL=redis://unicorn-falkordb:6379` |
| Embeddings (bge-m3) | — | `KNOWLEDGE_OPS_EMBED_BASE_URL=http://unicorn-infinity-proxy:8080/v1` |

- **Qdrant** issues read-only search/scroll over the `org_memory,stable_documents`
  collections (1024-dim cosine, bge-m3). Needs the embeddings URL above.
- **FalkorDB** uses `GRAPH.RO_QUERY` (read-only) over graph `brigade_global`
  (`KNOWLEDGE_OPS_FALKORDB_GRAPH`), matching `Entity` nodes by name. Needs `redis>=5`
  (`pip install '.[live]'`).
- Center Deep stays gated behind `KNOWLEDGE_OPS_ENABLE_CENTER_DEEP=1`.

From a laptop you can reach the published ports over Tailscale (Qdrant `:6333`,
Infinity `:8086`, FalkorDB `:6380`), but in production this should run **on bigboy**
on `unicorn-network` next to the stores + Brigade.

## MCP Registration

### Local stdio (Claude Desktop / Cursor)

Install the MCP runtime (`pip install '.[mcp]'`, declared in `pyproject.toml`), then:

```json
{
  "mcpServers": {
    "knowledge_ops": {
      "command": "python3",
      "args": ["-m", "knowledge_ops.mcp_server"],
      "cwd": "/Users/aaronstransky/Knowledge-Ops"
    }
  }
}
```

### Brigade federation broker

The service publishes a discovery manifest at `/.well-known/mcps.json` (also at
`.well-known/mcps.json` in the repo), namespace **`knowledge`**, audience
**`knowledge-ops`** — validated against the live broker's federation schema. Register
it with `scripts/register_with_brigade.sh` (prints the exact admin command; does not
register on its own). The broker fetches the manifest **over HTTPS**, so Knowledge-Ops
must be served at `https://knowledge-ops.magicunicorn.dev/.well-known/mcps.json` first.

The service is intentionally storage-opaque: agents never call Qdrant, FalkorDB, or
Center Deep directly.

## Adding A Source

1. Add an adapter under `knowledge_ops/adapters/` that returns `list[Passage]`.
2. Add a config flag/URL in `config.py`.
3. Wire the adapter in `federation.search`.
4. Include scope and owner on every `Passage`.
5. Add a row in `list_sources`.

## RLS v0

RLS is enforced at the federation layer with a conservative scope filter. `scope=global` returns only global passages. `scope=personal` returns only personal passages. Omitting scope or using `scope=all` is an admin/dev path and returns both.

Future Keycloak integration should derive allowed scopes from the JWT and remove direct `all` access from untrusted callers.
