# OpenWiki Governance Contract

Knowledge-Ops integrates OpenWiki as an untrusted knowledge producer, not as a
canonical database or authorization layer. OpenWiki never receives a writable
mount for the canonical wiki.

## Data Flow

```text
least-privilege connectors
  -> immutable raw connector snapshots
  -> sandboxed OpenWiki 0.1.0 producer
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

The accepted producer version defaults to `0.1.0` and is configured with
`KNOWLEDGE_OPS_OPENWIKI_VERSION`. An import with another version fails before
staging. Upgrade the pin only after reviewing upstream changes and rerunning the
security and ingestion tests.

## Import Contract

Configure one or more server-side export roots, separated by the platform path
separator:

```bash
export KNOWLEDGE_OPS_OPENWIKI_IMPORT_ROOTS=/imports/openwiki
```

Every Markdown file must use OKF-compatible YAML frontmatter with at least a
`type`. Provenance should include `source_uri` or `source`. Visibility defaults
to `personal`; `global` promotion requires a knowledge administrator.

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
version. The service rejects paths outside the allowlist, symlinks, oversized
documents and bundles, and invalid encodings. It then writes a content-addressed
read-only snapshot and a manifest. Re-importing identical content returns the
same import.

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
