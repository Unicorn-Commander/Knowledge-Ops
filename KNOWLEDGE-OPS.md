# Knowledge-Ops v0.4

Knowledge-Ops is the Magic Unicorn governed evidence and memory layer. Agents call `kb.search` or `kb.ground`; the service decides which stores to query, merges authorized evidence, and returns cited passages with explicitly heuristic relationship labels.

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

- `kb.search(query, scope?, sources?, k?, as_of?)`
- `kb.ground(claim, scope?, sources?, k?, as_of?)`
- `kb.list_sources()`
- `kb.get_doc(id)`
- `kb.upsert_doc(path, md, validity?, supersedes_id?, review_due_at?, metadata?, visibility?)`
- `kb.inspect_citation(source, source_id, claim)`
- `kb.delete_doc(id, reason)`
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
| GET  | `/ready` | Readiness — storage, staging, and required worker heartbeat |
| GET  | `/metrics` | Authenticated low-cardinality OpenMetrics |
| GET  | `/` | Redirects to the web GUI at `/ui/` |
| GET  | `/ui/` | **Web GUI** (Ask/Ground, Wiki, Files, Metrics, Sources) |
| GET  | `/info` | Service info JSON |
| POST | `/mcp` | **HTTP-MCP JSON-RPC** (`initialize`/`tools/list`/`tools/call`) for the Brigade broker |
| GET  | `/.well-known/mcps.json` | Brigade federation manifest |
| GET  | `/docs` | OpenAPI / Swagger |
| POST | `/kb.search` | body: `{query, scope?, sources?, k?, as_of?}` |
| POST | `/kb.ground` | body: `{query, scope?, sources?, k?, as_of?}` → heuristic verdict + cited evidence excerpts |
| GET  | `/kb.list_sources` | source catalog + enabled/disabled status |
| GET  | `/kb.docs` | list wiki docs |
| GET  | `/kb.get_doc?id=...` | one wiki doc |
| POST | `/kb.upsert_doc` | body: `{path, md, scope?, owner?, valid_from?, valid_until?, supersedes_id?, review_due_at?, metadata?, visibility?}` |
| POST | `/kb.inspect_citation` | body: `{source, source_id, claim}` → authorized exact source span |
| POST | `/kb.delete_doc?id=...` | body: `{reason}` → durable tombstone + projection status |
| GET  | `/kb.doc_history?id=...` | wiki versions |
| POST | `/kb.ingest_marketing_docs` | re-ingest the brand bible from `~/Documents` |
| POST | `/kb.record_metric` | body: `{name, value, tags?}` |
| GET  | `/kb.get_metric_chart?name=...` | SVG chart |
| GET  | `/kb.metrics?name=...` | metric rows |
| POST | `/kb.index_files?root=...&limit=...` | index a directory tree |
| GET  | `/kb.find_file?query=...&k=...&scope=...` | file search |
| GET  | `/kb.deep_read?path=...&query=...` | all chunks of one file |
| GET/POST | `/kb.review_items`, `/kb.review_scan`, `/kb.review_resolve` | administrator memory-quality queue |
| GET/POST | `/kb.refresh_jobs`, `/kb.refresh_job`, `/kb.refresh_run`, `/kb.refresh_due`, `/kb.refresh_complete` | administrator refresh contracts |
| GET/POST | `/kb.deletion_outbox`, `/kb.deletion_outbox/{task_id}/redrive` | administrator source-owner deletion operations |

Verified grounded call (returns evidence + citations):

```bash
curl -s -X POST http://127.0.0.1:8765/kb.ground \
  -H "Content-Type: application/json" \
  -d '{"query":"What is Magic Unicorn?","scope":"global","k":6}'
# => {"claim":"What is Magic Unicorn?","verdict":"related_evidence",
#     "verification_status":"heuristic_not_verified",
#     "answer":"Evidence excerpts (not a generated factual conclusion): ...",
#     "passages":[ ... claim-linked Passage objects ... ]}
```

## Web GUI

The FastAPI app serves its own single-page GUI at **`/ui/`** (static files under
`knowledge_ops/static/`, vanilla JS — no build step, no CDN). `/` redirects there.
On-brand Magic Unicorn (dark + purple/gradient, tokens from `magic-unicorn-ui`).
Sections: **Ask/Ground** (cited excerpts and exact source-span inspection),
**Library** (lifecycle-aware list/view/edit/history/deletion), **Review**
(OpenWiki plus stale/conflict queues), **Files** (find + deep-read), **Operations**
(metrics, audit, refresh jobs), and **Sources** (federation plus MCP tools).

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
`auth.federated_auth`. `initialize`/`tools/list`/`ping` stay open because they
contain public metadata. Local stdio uses an explicit local-system principal;
the loopback-only development compose disables HTTP auth explicitly.

## Public auth gate (oauth2-proxy + Keycloak)

On bigboy the public host is fronted by `knowledge-ops-oauth2-proxy`
(Keycloak `uchub` realm, Magic Unicorn SSO) — see `docker-compose.bigboy.yml`.
Traefik routes the host to the proxy (`:4180`), which proxies to
`http://knowledge-ops:8765` after SSO. **Whitelisted (public): `/health`,
`/.well-known/mcps.json`, `/mcp`** — so monitoring, the broker's manifest fetch,
and brokered execution keep working. oauth2-proxy passes the signed Keycloak ID
token upstream, and the app verifies that token before every `/kb.*` operation.
Direct in-cluster calls therefore need a valid token too. Humans hitting `/ui`
or `/kb.*` still enter through Magic Unicorn SSO.

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

Config-driven REST/Postgres sources must also declare
`enforces_authorization=true` and apply the supplied `scope` and `owner`
parameters in the upstream query. Missing classification is restricted, and a
missing authorization contract keeps the adapter disabled.

## Authorization and visibility

REST and HTTP-MCP share one verified-principal policy. Omitting scope returns
authorized global knowledge plus personal records owned by token `sub`/verified identity aliases.
`scope=personal` cannot retrieve another owner. `scope=all`, restricted sources,
global writes, file indexing, and OpenWiki promotion require administrator or
specific elevated scopes. Missing source classification is `restricted`.
For OIDC, `sub` is authoritative; email is an alias only when the signed token
sets boolean `email_verified: true`, and `preferred_username` is not an owner.

### Workspace and project-bound documents

The client must send source tenancy metadata when creating a project document;
`scope: "global"` is not a substitute for a workspace binding. The REST and
HTTP-MCP upsert payloads accept either of these equivalent forms:

```json
{"metadata": {"workspace_id": "workspace-a", "project_id": "project-17"}}
```

```json
{"visibility": {"workspace_id": "workspace-a", "project_id": "project-17"}}
```

`project_id` is optional, but it requires `workspace_id` when present. The
verified token must carry the same workspace (`workspace_id` or `workspaces`)
and, for project-bound documents, the same project (`project_id` or `projects`).
Knowledge-Ops enforces this binding in document get/list/search predicates, so
the client must not rely on filtering results after retrieval. Unbound global
rows are treated as legacy compatibility rows only when
`KNOWLEDGE_OPS_ALLOW_LEGACY_GLOBAL_VISIBILITY=1`; the default is the safe deny
behavior for non-admin readers.

### Client-to-workspace mapping for service clients

Some verified service-client tokens identify their caller with `azp` (or the
fallback claim `client_id`) but carry no workspace claim. Configure the resource
server with `KNOWLEDGE_OPS_CLIENT_WORKSPACE_MAP` as a JSON object whose entries
contain required `workspaces` and optional `projects` arrays:

```sh
export KNOWLEDGE_OPS_CLIENT_WORKSPACE_MAP='{
  "ko-agent-brigade": {"workspaces": ["magic-unicorn"]},
  "ko-agent-claude": {"workspaces": ["magic-unicorn"]},
  "ko-agent-shafen": {"workspaces": ["magic-unicorn"]}
}'
```

The map is a fallback only: an already verified token's workspace claims win,
and the map never applies to anonymous or unverified requests. Malformed JSON
or an invalid entry fails startup rather than silently granting no mapping.
For dogfood, the status quo is that the three clients above see the global
legacy rows while `KNOWLEDGE_OPS_ALLOW_LEGACY_GLOBAL_VISIBILITY=1`; configure
the map, then run `retag-legacy --workspace magic-unicorn --legal-project
legal-ops --apply`, and finally unset the legacy visibility flag.

OpenWiki 0.2.0 is pinned as an untrusted producer. Its immutable staging,
OKF/security gates, and review-before-canonical contract are in
`docs/OPENWIKI-GOVERNANCE.md`.

Temporal validity, supersession, convergent deletion, quality queues, refresh
contracts, citation inspection, and the offline evaluation gate are specified in
`docs/MEMORY-LIFECYCLE.md`.
