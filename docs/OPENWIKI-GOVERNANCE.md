# OpenWiki Governance Contract

Knowledge-Ops integrates OpenWiki as an untrusted knowledge producer, not as a
canonical database or authorization layer. OpenWiki never receives a writable
mount for the canonical wiki.

## Data Flow

```text
least-privilege connectors
  -> immutable raw connector snapshots
  -> sandboxed OpenWiki 0.2.0 producer
  -> allowlisted export directory
  -> content-addressed Knowledge-Ops staging
  -> OKF, secret, PII, prompt-injection, ACL, and provenance gates
  -> explicit administrator review
  -> versioned canonical Markdown + trusted provenance record
  -> rebuildable retrieval indexes
```

OpenWiki's connector credentials and raw snapshots remain outside this
repository. The producer should run without access to the Knowledge-Ops data
volume, with read-only connector snapshots, a single writable export directory,
and network egress disabled after connector capture.

## Producer Pin

The accepted producer version defaults to `0.2.0` and is configured with
`KNOWLEDGE_OPS_OPENWIKI_VERSION`. An import with another version fails before
staging. Upgrade the pin only after reviewing upstream changes and rerunning the
security and ingestion tests.

The pin tracks LangChain's [OpenWiki repository at the reviewed
revision](https://github.com/langchain-ai/openwiki/tree/d4e94ab513ab13908c6b61346b23dc17bbd59b1f)
and its [OpenWiki 0.2 OKF announcement](https://www.langchain.com/blog/openwiki-0-2-adds-okf-support).
The upstream `0.2.0` implementation emits typed root and nested indexes even
though its README describes untyped reserved indexes. Knowledge-Ops tests the
observed producer output and keeps narrowly compatible OKF reserved-page rules;
it does not treat arbitrary untyped Markdown as valid knowledge.

## Import Contract

Configure one or more server-side export roots, separated by the platform path
separator:

```bash
export KNOWLEDGE_OPS_OPENWIKI_IMPORT_ROOTS=/imports/openwiki
```

Every non-reserved Markdown concept must use OKF-compatible YAML frontmatter
with a non-empty `type`. The `index.md` and update-log names are reserved by OKF:
an untyped root index must declare `okf_version: "0.1"`, while nested indexes and
logs may omit a concept type. The upstream announcement uses both `log.md` and
`logs.md`, so Knowledge-Ops narrowly accepts either reserved filename. OpenWiki
`0.2.0` currently emits
`type: Documentation Index` on its indexes, which is accepted. Provenance should
include `source_uri` or `source`. Visibility defaults to `personal`; `global`
promotion requires a knowledge administrator.

`INSTRUCTIONS.md` is producer control metadata and `_plan.md` is transient
working state. Knowledge-Ops excludes either name and every dot-prefixed file or
directory component at every bundle depth before content hashing, security
scanning, staging, review, or canonical promotion. Ordinary names containing
dots, such as `api.v2.md`, remain eligible.

```markdown
---
type: knowledge
source_uri: connector://notion/page-id
scope: personal
---
# Decision

Approved source content.
```

Call `POST /kb.openwiki_stage` with the server-side path and pinned producer
version. The service walks from a no-follow directory descriptor, rejects
symlinks, paths outside the allowlist, oversized documents and bundles, and
invalid encodings, then hashes and stages the exact bytes captured from those
descriptors. It writes a content-addressed read-only snapshot and manifest.
Re-importing identical content returns the same import.

The producer must publish a quiesced, immutable export directory. Write a new
versioned directory to completion and expose it to the importer only after all
files are closed. Descriptor capture prevents link swaps and byte rereads, but
only producer-side immutability guarantees that a multi-file bundle represents
one coherent export point.

## Review Gates

- Missing OKF type, detected secrets, prompt-injection control text, invalid ACL,
  or snapshot tampering blocks promotion and requires a corrected re-import.
- PII findings require a written review note and explicit `accept_pii=true`.
- Only `knowledge-admin` can approve or reject an import.
- Promotion writes a stable `openwiki/<relative-path>` canonical document, so a
  later approved snapshot creates a normal wiki version instead of duplicate
  search results.
- Trusted provenance records the source URI, source content hash, producer
  version, import digest, reviewer, and review time.
- Rejected and approved snapshots remain immutable for audit. They are not
  searched as canonical knowledge.

## External Setup Still Required

Repository code cannot choose connector grants or operate the OpenWiki producer.
An operator must provision least-privilege connectors, choose retention and
deletion policy for raw snapshots, mount the export root read-only into
Knowledge-Ops, assign Keycloak knowledge roles, and validate restore and deletion
propagation in the target environment. No connector credential belongs in git.
