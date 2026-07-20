# Knowledge-Ops

Knowledge-Ops is Magic Unicorn's governed evidence and memory layer: one authorized, cited retrieval surface for humans, apps, and Brigade agents. It retrieves evidence from the canonical wiki, indexed files, org memory, Qdrant, FalkorDB, Center Deep, REST, and read-only SQL without making callers know where each store lives. Automated evidence relationships are labeled heuristic and are not represented as factual verification.

Public dogfood: `https://knowledge-ops.magicunicorn.dev`  
Human GUI: `/ui/`  
Brokered agent surface: `/mcp` with namespace `knowledge`

![Knowledge-Ops GUI](docs/screenshots/knowledge-ops-gui.png)

## What It Does

- Retrieves evidence with citations and conservative contradiction/abstention labels.
- Curates canonical docs in a versioned markdown wiki.
- Stages OpenWiki/OKF output behind security and human-review gates before canonical promotion.
- Searches native and federated stores through one API.
- Preserves app ownership: Knowledge-Ops reads external stores, it does not take over their write paths.
- Exposes the same capabilities to the GUI, REST clients, and Brigade MCP tools.
- Registers the Librarian as the knowledge-domain agent that uses this surface.

## Architecture

Knowledge-Ops uses a dual-layer federation model.

```mermaid
flowchart LR
  Human[Human / GUI] --> KO[Knowledge-Ops API]
  Agent[Brigade agents] --> Broker[Brigade broker]
  Broker --> MCP[/Knowledge-Ops /mcp/]
  MCP --> KO
  Apps[Suite apps] --> KO

  KO --> Native[Native stores]
  Native --> Wiki[Wiki: markdown + SQLite]
  Native --> Files[File index: SQLite FTS]
  Native --> Metrics[Metrics: SQLite timeseries]
  Native --> Memory[Org memory JSONL]

  KO --> Federated[Federated stores]
  Federated --> Qdrant[Qdrant collections]
  Federated --> Falkor[FalkorDB graphs]
  Federated --> REST[REST adapters]
  Federated --> SQL[Read-only Postgres adapters]
  Federated --> CenterDeep[Center Deep Data Intel]
```

Native stores are owned by Knowledge-Ops. Federated stores remain owned by the source app. The fan-out merges and re-ranks passages, enforces identity-derived visibility, and returns cited evidence.

OpenWiki is a producer outside this trust boundary. Its governed import contract is documented in [OpenWiki Governance](docs/OPENWIKI-GOVERNANCE.md).

## GUI

The GUI is served directly by FastAPI at `/ui/`, with no build step. It includes Ask, Library, OpenWiki Review, Files, Sources, and Operations.

![Federated sources](docs/screenshots/knowledge-ops-sources.png)

The public host is behind Keycloak SSO via oauth2-proxy. `/health`, `/.well-known/mcps.json`, and `/mcp` are public for monitoring and broker operation; `/mcp` tool calls still require the Brigade JWT in production.

![SSO gate](docs/screenshots/knowledge-ops-sso-gate.png)

## Consume It

Production REST calls require a signed Keycloak or Brigade bearer token. Local
loopback compose explicitly disables API auth for development only.

REST:

```bash
curl -s -X POST https://knowledge-ops.magicunicorn.dev/kb.ground \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"What is Magic Unicorn?","scope":"global","k":6}'
```

MCP via Brigade:

- `knowledge.search`
- `knowledge.ground`
- `knowledge.list_sources`
- `knowledge.get_doc`
- `knowledge.list_docs`
- `knowledge.doc_history`
- `knowledge.find_file`
- `knowledge.deep_read`
- `knowledge.upsert_doc`
- `knowledge.record_metric`

REST routes are dot-style: `/kb.search`, `/kb.ground`, `/kb.list_sources`, not `/kb/search`.

Optional source filters:

- `wiki`, `file_index`, `org_memory`, `qdrant`, `falkordb`, `center_deep`
- `qdrant:meeting_transcripts`
- `qdrant:crisis_evidence`
- `falkordb:brigade_global`
- `rest:<adapter_name>`
- `postgres:<adapter_name>`

## Federated Sources

Current live wiring includes:

- Qdrant: `org_memory`, `stable_documents`, `meeting_transcripts`, `crisis_evidence`, `contact_ops_person_voice`
- FalkorDB: `brigade_global`, `org_memory`, `brigade_knowledge`, `brigade_memories`, `agent_meeting_ops_canonical`, Contact-Ops graphs, Customer-Ops graph, `majiks_research`
- Center Deep Data Intel: `/api/v1/catalogue/quick-search`
- Native wiki, file index, metrics, and org-memory JSONL

`contact_ops_person_voice` is present but empty on the live Qdrant instance at the time of wiring. SQL-backed app adapters are supported by config and should keep credentials in host env files, not git.

## Add A Source Adapter

For code adapters, add a module under `knowledge_ops/adapters/` that returns `list[Passage]`, wire it in `federation.search`, and add status rows in `list_sources`.

For config-only REST adapters:

```bash
export KNOWLEDGE_OPS_REST_SOURCES='[
  {
    "name": "example_app",
    "url": "https://example.internal/api/search",
    "method": "GET",
    "enforces_authorization": true,
    "scope": "global",
    "query_param": "q",
    "limit_param": "limit",
    "results_key": "results"
  }
]'
```

For config-only Postgres adapters:

```bash
export KNOWLEDGE_OPS_POSTGRES_SOURCES='[
  {
    "name": "accounting_ops",
    "dsn": "postgresql://readonly_user:REMOVED@accounting-postgres:5432/accounting",
    "enforces_authorization": true,
    "scope": "global",
    "sql": "select id, title, body as text, scope, owner, uri from knowledge_docs where search_vector @@ plainto_tsquery(%(query)s) and (scope = 'global' or (scope = 'personal' and owner = %(owner)s)) limit %(k)s"
  }
]'
```

REST and Postgres adapters are disabled unless `enforces_authorization=true` explicitly records that the upstream query uses the supplied `scope` and `owner` identity parameters. Postgres queries must be `SELECT` statements containing an `%(owner)s` or `%(owners)s` identity placeholder and should return `id`, `title`, and `text`, with `scope` and `owner` classifications plus optional `uri` and `score`. The adapter starts a read-only transaction and degrades to empty results on failure.

## Security Model

- Every `/kb.*` data request is authorized inside the app with a verified Keycloak or Brigade token; edge SSO alone is not authorization.
- Brigade calls `/mcp` with an RS256 JWT, `aud=knowledge-ops`, and every tool enforces its manifest scopes.
- Omitted scope means shared global plus the caller's own personal knowledge. `all`, restricted sources, global writes, indexing, and OpenWiki promotion require explicit privileges.
- Personal owner values are derived from verified token identity, never request input. Unknown source classification is restricted, not global.
- Wiki paths and file roots are confined and symlinks are rejected. Request body, rate, and result-count bounds are enforced.
- SQLite is canonical for wiki documents; atomic Markdown projections are reconciled at startup after a crash.
- Federated adapters are read-only. Qdrant uses search only; FalkorDB uses `GRAPH.RO_QUERY`; Postgres queries are read-only `SELECT`.
- Secrets stay out of git. Use host env files for DB DSNs and bearer tokens.

## The Librarian

The Librarian is the knowledge-domain Brigade agent:

- Agent id: `librarian`
- Display name: `the Librarian`
- Model: `local/Qwen_Qwen3.6-35B-A3B-Q4_K_M`
- Domain: `knowledge`
- Tools: Knowledge-Ops MCP tools plus `memory`
- Brand-Ops identity: `knowledge-ops/the-librarian`
- Avatar: `https://brand-ops.magicunicorn.dev/api/brands/knowledge-ops/agents/the-librarian/avatar`
- Voice: `bf_emma` via `kokoro-onnx` at speed `0.96`
- Behavior: answer only from cited evidence, curate and maintain the wiki, ingest and organize docs, flag stale areas and gaps, and say "not found" when evidence is missing.

Rollback:

```sql
DELETE FROM agents WHERE id = 'librarian';
```

## Deploy

Local:

```bash
docker compose up -d --build
```

Bigboy:

```bash
rsync -az --delete --exclude .git ./ magicunicorn:/home/muut/Knowledge-Ops/
ssh magicunicorn 'cd /home/muut/Knowledge-Ops && docker compose -f docker-compose.yml -f docker-compose.bigboy.yml up -d --build'
```

Verify:

```bash
curl -s https://knowledge-ops.magicunicorn.dev/health
curl -s https://knowledge-ops.magicunicorn.dev/.well-known/mcps.json
```

Rollback deploy:

```bash
ssh magicunicorn 'cd /home/muut/Knowledge-Ops && cp docker-compose.bigboy.yml.bak-pre-gui-20260627 docker-compose.bigboy.yml && docker compose -f docker-compose.yml -f docker-compose.bigboy.yml up -d --build'
```

## Tests

```bash
python3 -m pytest -q
```

Runtime dependencies are pinned with artifact hashes in `requirements.lock`.
Regenerate it with Python 3.12 and `pip-tools==7.6.0` after changing project
dependencies:

```bash
python3.12 -m piptools compile pyproject.toml --extra all --generate-hashes --strip-extras --output-file requirements.lock
```
