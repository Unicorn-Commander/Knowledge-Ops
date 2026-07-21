# Governed Memory Lifecycle

Knowledge-Ops 0.4 provides upgrade-safe temporal validity, convergent deletion,
quality review queues, durable refresh execution, and a deterministic offline
evaluation. SQLite remains canonical. OpenWiki remains an untrusted staged
producer and cannot write directly to canonical memory.

## Validity and Supersession

Each wiki document has `valid_from`, optional `valid_until`, optional
`supersedes_id`, and a lifecycle state:

- `active`: eligible for current retrieval while its validity interval contains
  the query time.
- `superseded`: excluded from current retrieval, but eligible for `as_of`
  retrieval inside its closed historical interval.
- `tombstoned`: never retrievable, including through historical queries.

A replacement must start after its predecessor, preserve scope and owner, and
cannot supersede itself or a tombstone. Publishing it closes the predecessor at
the replacement's `valid_from` and removes the predecessor from current FTS,
the file index, and the Markdown projection. Superseded and tombstoned records
are immutable; changes are published as a new active replacement.
Ordinary edits preserve the original validity start and predecessor link. Both
are immutable after publication; a different effective start is represented by
a new replacement document.

Use the optional `as_of` ISO 8601 field on `/kb.search` or `/kb.ground` to query
historical wiki validity. An `as_of` request defaults to the wiki and rejects
current-only sources so historical evidence cannot be mixed with present state.

## Governed Deletion

`POST /kb.delete_doc?id=<doc>` requires a reason. Personal documents may be
deleted by their owner or an administrator. Global deletion requires
`knowledge-admin`.

The operation purges retrievable content from:

- canonical wiki body and prior version bodies;
- wiki FTS;
- file-index chunks and FTS for the Markdown projection;
- the Markdown projection on disk;
- OpenWiki staged plaintext and its manifest reference in every retained import
  snapshot, when applicable.

A minimal content-free tombstone (`title` is replaced and `body` is empty) and
deletion receipt remain for auditability. Each
projection is durably marked `pending`, `success`, or `not_applicable`. Repeated
deletion requests and startup reconciliation retry pending work, including a
crash between canonical deletion and projection cleanup. Startup also rechecks
successful receipts so a filesystem rollback or stale reindex cannot resurrect
content. Disk seeding does not resurrect known tombstones, and later OpenWiki
bundles cannot stage a document at a tombstoned canonical path.
External federated stores remain owned by their source applications. A
credential-free outbox records one idempotent task per configured source-owner
adapter. The durable worker processes only explicitly registered adapters with
process-owned credentials. Exhausted tasks stay failed for operator review;
after the source-side condition is corrected, a knowledge administrator can
redrive a failed task from Operations or
`POST /kb.deletion_outbox/{task_id}/redrive`. Local projection reconciliation
normally scans pending receipts only; startup and restore verification also
recheck completed receipts to prevent rollback-based resurrection.

## Review Queue

Administrators can scan and resolve `/kb.review_items` findings in the Review
UI. The current deterministic detectors are deliberately narrow:

- `stale`: an active document has passed its explicit `review_due_at`;
- `conflict`: same-title, same-owner documents have opposing lexical polarity,
  labeled `title_polarity_v1`.

These are review signals, not semantic truth judgments. Operators resolve items
with a note. Findings that disappear are auto-resolved by the next scan.

## Refresh Contracts

Refresh jobs contain only a job id, kind, source reference, interval, and enabled
state. Credential material and connector endpoints are not accepted. Each run
snapshots its kind and logical source in the same transaction that enqueues it,
so later job edits affect only future runs. Run records have a stable run id and unique
`(job_id, idempotency_key)` contract, so repeated scheduling is safe.

The `knowledge-ops worker` process is the scheduler and executor. Each poll
enqueues due jobs and claims bounded batches with expiring leases, heartbeat
renewal, and fencing tokens. Claims serialize both job id and source reference,
so two job definitions cannot refresh the same source concurrently. A crash or
graceful shutdown makes work reclaimable without allowing an old owner to
commit. Exponential retry is capped by configured delay and attempt ceilings;
lease crashes also consume the ceiling and become terminal when exhausted.

- `review_scan` invokes the built-in deterministic review scanner.
- `connector_refresh` runs only when the worker process has an explicitly
  registered idempotent adapter for its logical source reference.
- `POST /kb.refresh_due`, `POST /kb.refresh_run`, and their CLI equivalents are
  administrative enqueue controls. The worker also schedules due work itself.
- `/kb.refresh_complete` is retained as an authenticated administrative
  compatibility operation; normal production completion uses the worker's
  fenced lease path.

Duplicate enqueue requests return the original run, including under concurrent
insert races. Production still requires source-owner adapter implementations,
external secret-store wiring, alert routing, and named review ownership, but it
does not require a separate cron scheduler.

## Citation Inspection

Wiki and file-index passages carry exact character spans. Wiki citation ids are
bound to the document revision and fail closed after an edit. Grounding attaches
a stable claim id, citation id, matched claim terms, and the existing
conservative evidence relationship. The Ask and Files views open a native dialog
that shows the authorized source excerpt and highlights matched terms. The
inspection API rechecks source authorization and does not fetch unsupported
external content. File chunks created before migration require one reindex before
exact span inspection is available.

## Evaluation Gate

Run the isolated, network-free gate before release:

```bash
python3 -m knowledge_ops.cli evaluate-memory
```

The suite covers temporal updates, possible-conflict triage, abstention,
federation-level unauthorized recall, deletion, exact citation spans, Recall@5,
MRR, and fixture citation precision.
It exits nonzero unless all thresholds pass. Test fixtures use a temporary
database and never read or modify production memory.

## Upgrade and Rollout

`init_db()` records schema migration version 3 and adds missing columns/tables in
place under a lock beside the configured database. It never drops or resets
existing tables. Before rollout, take a database backup, start one upgraded
instance to apply the migration, run the test and evaluation gates, then start
the API and worker topology and confirm worker-backed readiness.

Version 3 also migrates native personal wiki and file-index rows from a verified
legacy OIDC email owner to the token's stable `sub` on the first authenticated
request. The migration is transactional and its receipt stores only an email
digest. Unverified email and mutable username claims never trigger it.
