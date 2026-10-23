# Knowledge-Ops

Knowledge-Ops is a governed evidence and memory service. It keeps canonical
documents in a versioned wiki, indexes local files, and returns cited results
through REST and HTTP-MCP. Automated grounding is labeled as heuristic rather
than presented as factual verification.

## Quickstart

On a fresh Linux host with Docker Engine and the Docker Compose plugin, run the
README block emitted by the installer:

```bash
docker compose up -d --build
curl -fsS http://127.0.0.1:8765/ready
```

The second command succeeds only when the SQLite database, schema, wiki, and
OpenWiki staging directories are ready. The standalone compose publishes the
service on loopback only, uses local SQLite volumes, keeps optional live
adapters disabled, and does not ingest the image-baked wiki corpus.

No `.env` file is needed for this quickstart. `.env.example` lists safe,
non-secret overrides for a local deployment. If you create `.env`, keep it
local and untracked.

## Bootstrap admin and agent credential

The standalone compose starts a local issuer and generates one bootstrap admin
bearer token on the first run. The token and signing key live in the Docker
volume, are not printed by the service, and are not stored in this repository.
The token is valid for 90 days; restarting the stack does not replace it while
it remains valid.

Read the token into a shell variable and verify the bootstrap identity:

```bash
ADMIN_TOKEN="$(docker compose exec -T knowledge-ops-auth cat /data/bootstrap-auth/bootstrap-admin.token)"
curl -fsS http://127.0.0.1:8765/kb.me -H "Authorization: Bearer $ADMIN_TOKEN"
```

The response has `name` `bootstrap-admin` and `is_admin` `true`. An agent can
use this same local bearer credential for the authenticated REST routes or the
loopback `/mcp` JSON-RPC endpoint. Keep it in a secret transport or process
environment; never paste it into a commit, issue, log, or chat transcript.

This local credential is the self-host token route; this tree does not claim to
provide a hosted signup or a per-agent PAT service. A public or multi-user
deployment must replace the local issuer settings with its own trusted OIDC or
broker issuer before exposing the application beyond loopback. Auth-off is
acceptable only when the API remains bound to `127.0.0.1`.

## API examples

With `ADMIN_TOKEN` set as above, an agent can ground a question:

```bash
curl -fsS -X POST http://127.0.0.1:8765/kb.ground \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"What evidence is available?","scope":"global","k":6}'
```

REST routes use dot notation, including `/kb.search`, `/kb.ground`,
`/kb.list_sources`, and `/kb.docs`. The HTTP-MCP endpoint is `/mcp`; its JSON-RPC
tools use the `knowledge` namespace. Every authenticated request is authorized
inside the application, not merely by a front proxy.

## Storage and safety defaults

- SQLite data and wiki projections persist in named Docker volumes.
- `KNOWLEDGE_OPS_SEED_INTERNAL_CORPUS=0` is pinned in the standalone compose.
  The build also refuses seed Markdown unless an operator explicitly selects an
  internal edition.
- Qdrant, FalkorDB, and other live adapters are disabled until an operator
  supplies an authorized, identity-aware configuration.
- Credentials for external adapters belong in host-only environment files. Do
  not put values in this repository.
- The local issuer is reachable only inside the Compose network. The generated
  token file is mode `0600` in the data volume.

## Optional configuration

Copy the names from `.env.example` into a host-only `.env` when changing local
defaults. The important controls are:

- `KNOWLEDGE_OPS_API_REQUIRE_AUTH`
- `KNOWLEDGE_OPS_API_AUDIENCE`
- `KNOWLEDGE_OPS_API_OIDC_JWKS_URL`
- `KNOWLEDGE_OPS_API_TRUSTED_ISSUERS`
- `KNOWLEDGE_OPS_MCP_JWKS_URL`
- `KNOWLEDGE_OPS_MCP_AUDIENCE`
- `KNOWLEDGE_OPS_MCP_TRUSTED_ISSUERS`
- `KNOWLEDGE_OPS_ENABLE_QDRANT`
- `KNOWLEDGE_OPS_ENABLE_FALKORDB`
- `KNOWLEDGE_OPS_ENABLE_CENTER_DEEP`
- `KNOWLEDGE_OPS_REQUIRE_WORKER`

The API and MCP issuer/audience values must describe the same trusted signer
that mints the bearer presented by the client. Do not set auth off on a
non-loopback bind.

## Development checks

The application test suite is deterministic and offline:

```bash
python3 -m pytest -q
python3 -m knowledge_ops.cli evaluate-memory
```

The worker is optional in the standalone path. Enable it only when a deployment
needs durable refresh scheduling and has configured its process-owned adapter
access. See [Operations Runbook](docs/OPERATIONS-RUNBOOK.md), [OpenWiki
Governance](docs/OPENWIKI-GOVERNANCE.md), and [Governed Memory Lifecycle](docs/MEMORY-LIFECYCLE.md)
for the application contracts.
