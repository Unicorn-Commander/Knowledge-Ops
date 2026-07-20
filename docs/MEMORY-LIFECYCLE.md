# Governed Memory Lifecycle

Knowledge-Ops 0.3 adds upgrade-safe temporal validity, deletion propagation,
quality review queues, refresh run contracts, and a deterministic offline
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
External federated stores remain owned by their source applications; their own
deletion must be completed there before the next connector refresh.

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
state. Credential material is not accepted. Run records have a stable run id and
a unique `(job_id, idempotency_key)` contract, so repeated dispatch is safe.

- `review_scan` runs locally and completes synchronously.
- `connector_refresh` creates a pending contract for an external worker, which
  completes it through `/kb.refresh_complete`.
- `POST /kb.refresh_due` or `knowledge-ops refresh-due` dispatches due work. The
service intentionally does not run a hidden background scheduler.

Synchronous scan failures are recorded as failed runs before the error is
returned. Duplicate `(job_id, idempotency_key)` dispatches return the original
run, including under concurrent insert races.

Production still requires an operator-managed scheduler, connector workers and
their secret-store wiring, retry/alert policy, and named review ownership.

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

`init_db()` records schema migration version 1 and adds missing columns/tables in
place. It never drops or resets existing tables. Before rollout, take a database
backup, start one upgraded instance to apply the migration, run the test and
evaluation gates, then configure the external scheduler and connector workers.
