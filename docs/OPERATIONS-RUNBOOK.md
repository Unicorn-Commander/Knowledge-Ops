# Knowledge-Ops Operations Runbook

## Trust Boundaries

- SQLite is canonical for documents, lifecycle receipts, refresh runs, worker heartbeats, and the deletion outbox.
- The base worker executes local review scans only. No live connector or source-owner deletion adapter is registered by default.
- Queue rows contain logical source identifiers only. Credentials, bearer tokens, URLs with queries, and arbitrary adapter content must not enter SQLite or audit events.
- `/metrics` requires a `knowledge-admin` principal. Configure the scraper with an externally managed, least-privilege bearer token; do not put it in this repository.

## Worker

Start the API and durable scheduler without live credentials:

```bash
docker compose --profile worker up -d --build
knowledge-ops worker --once
knowledge-ops operations-status
```

The production topology makes the worker mandatory and joins it to the existing production networks while keeping live adapters off:

```bash
docker compose -f docker-compose.yml -f docker-compose.bigboy.yml --profile worker up -d --build
```

The Bigboy override sets `KNOWLEDGE_OPS_REQUIRE_WORKER=1`; use the same setting for any other environment where API readiness must fail without a fresh running worker. The worker claims jobs with expiring, renewable leases and fencing tokens. A graceful `SIGINT` or `SIGTERM` releases its leases; an expired lease is safe for another worker to reclaim.

The API, worker, and production SSO proxy run with a read-only root filesystem,
all Linux capabilities dropped, `no-new-privileges`, and a bounded temporary
filesystem. Only `/data` and `/wiki` are writable persistent state. Treat a
request for any additional writable mount or capability as a deployment review.

Connector and deletion adapters are process-owned implementations of `RefreshAdapter` and `DeletionAdapter` in `knowledge_ops.worker`. They must be idempotent, use the supplied idempotency key, return operational counters rather than source content, and source credentials from an external secret manager. Wiring and credentials are external deployment work.

## Metrics And Alerts

The OpenMetrics endpoint exposes finite-state aggregates without worker, job, source, or host identifiers. Suggested starting alerts:

```promql
knowledge_ops_worker_up < 1
increase(knowledge_ops_auth_failures_total[5m]) > 10
increase(knowledge_ops_jwks_stale_fallback_total[5m]) > 0
knowledge_ops_review_backlog{kind="stale"} > 50
knowledge_ops_review_backlog{kind="conflict"} > 10
knowledge_ops_refresh_runs{status="failed"} > 0
knowledge_ops_refresh_last_success_age_seconds{kind="review_scan"} > 90000
knowledge_ops_deletion_pending_projections > 0
knowledge_ops_deletion_outbox{status="failed"} > 0
knowledge_ops_deletion_outbox{status="pending"} > 25
knowledge_ops_source_health_count{status=~"failed|degraded"} > 0
```

Apply a `for` period suited to the polling interval, normally 2-5 minutes. A disabled source is informational until the corresponding external adapter is intentionally enabled.

- Auth failures: inspect bounded `auth.api.*` and `auth.mcp.*` audit rows, issuer/audience configuration, and identity-provider health. Never log the presented token.
- JWKS fallback: signing keys are refreshed after the configured 3600-second TTL. If refresh fails, an already cached `kid` is accepted for at most `KNOWLEDGE_OPS_API_JWKS_MAX_STALE_SECONDS` or `KNOWLEDGE_OPS_MCP_JWKS_MAX_STALE_SECONDS` beyond that TTL (300 seconds by default), then verification fails closed. Alert on any fallback and repair identity-provider reachability before the grace expires.
- Stale/conflict backlog: run or inspect the `review_scan` job, then resolve evidence through the governed review queue.
- Refresh age/failure: inspect retry count, terminal error summary, worker heartbeat, and source health. Re-enable a connector only after its external authorization contract is verified.
- Deletion backlog: distinguish local projection failures from source-owner outbox tasks. Local projections must converge. External pending/failed tasks require the owning source operator and an explicitly registered deletion adapter.
- Source health: disabled means fail-closed configuration; degraded or failed means an enabled adapter attempt did not complete.

## Backup And Restore

Backup archives contain sensitive organizational memory. Store them encrypted at rest, restrict them to the operator account, and replicate them to an approved off-host recovery location. The manifest SHA-256 values detect corruption; they do not authenticate the archive. Sign archives with an externally managed key when authenticity is required.

```bash
scripts/backup.sh /secure/off-host-staging/knowledge-ops-$(date +%Y%m%dT%H%M%S).tar.gz
scripts/restore_verify.sh /secure/off-host-staging/knowledge-ops-20260720T010000.tar.gz /empty/disposable/root
knowledge-ops reindex-wiki
```

The backup command rejects output inside the data, wiki, or OpenWiki staging roots and refuses to overwrite the configured database or its WAL sidecars even when the database is outside the data root. It creates the archive as mode `0600`. Restore refuses non-empty targets, rejects links, duplicate paths, excessive member counts, and oversized uncompressed archives, then installs mode `0700` directories and `0600` files. Verification applies all migrations, rebuilds wiki FTS, repairs markdown from canonical SQLite, checks every retained OpenWiki staging manifest against its database row and document checksums, reconciles local deletions, runs SQLite integrity checks, and reports external deletion backlog without treating recoverable source-owner work as local corruption.

Before a production restore, stop API and worker writers, preserve the damaged volume for forensics, verify the archive in a disposable volume, and then follow the platform's volume replacement procedure. The repository intentionally does not automate destructive replacement of a production volume.

## Identity Ownership

For OIDC callers, stable token `sub` is the authoritative personal owner. An
email is an additional legacy alias only when the signed claim contains the
boolean `email_verified: true`; `preferred_username` is never an OIDC ownership
alias. On the first authenticated request, schema v3 transactionally migrates
native personal wiki and file-index rows from that verified legacy email owner
to the stable subject. The durable receipt and audit row retain only an email
digest. Inventory federated source-owned records separately before changing
identity-provider mappings; do not preserve access by adding mutable username
aliases. Brigade-owned identity aliases follow the broker's separate
trusted-claim contract.

## External Blockers

- Identity provider: provision the scraper/service principal and rotate its token outside Git.
- Source owners: approve delete semantics, idempotency behavior, and least-privilege credentials for each live adapter.
- Platform operator: provide encrypted off-host backup storage, archive signing if required, alert routing, retention, and restore-drill scheduling.
- Connector owner: implement and review explicit adapter registration. Merely adding a URL or credential environment variable does not enable execution.
