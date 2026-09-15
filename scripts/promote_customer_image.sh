#!/bin/sh
# Promote a CI-built Knowledge-Ops customer image from the CI runner host to a
# deploy host, refusing anything that is not provably corpus-free and CI-built.
#
# Why this script exists (P-00099 finding 3, 2026-09-15). The customer-facing
# instance was running `knowledge-ops:0.7.1-noseed`: a `docker build` run by hand on
# one host, from a Dockerfile kept only on that host, derived from an image whose
# `0.7.1` tag pointed at DIFFERENT content on the two hosts that had it. There was no
# way to answer "what source is the customer running". A registry would be the right
# answer; the org has no write:package PAT yet, so this is the honest interim: the
# BUILD is CI's (clean checkout of a pushed commit, labelled with the revision, and
# asserted corpus-free), and promotion is a recorded, re-runnable recipe rather than
# somebody's shell history.
#
# usage: scripts/promote_customer_image.sh <image:tag> <src-ssh-host> <dst-ssh-host>
# e.g.:  scripts/promote_customer_image.sh knowledge-ops:0.7.2-customer-abc123def456 \
#            muut@192.168.10.10 muut@100.87.46.87
set -eu

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <image:tag> <src-ssh-host> <dst-ssh-host>" >&2
  exit 2
fi

image="$1"
src="$2"
dst="$3"

say() { printf '%s\n' "$*"; }
die() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

say "== source: $src  image: $image"
src_id="$(ssh -n "$src" "docker image inspect '$image' --format '{{.Id}}'")" \
  || die "image not found on $src"
rev="$(ssh -n "$src" "docker image inspect '$image' --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}'")"
edition="$(ssh -n "$src" "docker image inspect '$image' --format '{{index .Config.Labels \"org.magicunicorn.knowledge-ops.seed-corpus\"}}'")"

# Gate 1 — it must be a CI build, i.e. it must know which commit it came from.
case "$rev" in
  ''|'<no value>') die "no org.opencontainers.image.revision label: this is a hand build, not a CI build" ;;
esac
# Gate 2 — it must be the customer edition.
[ "$edition" = "none" ] || die "seed-corpus label is '$edition', want 'none' (this image may carry internal docs)"
# Gate 3 — do not take the label's word for it. Look in the image.
baked="$(ssh -n "$src" "docker run --rm --entrypoint sh '$image' -c 'find /opt/knowledge-ops/wiki /wiki -name \"*.md\" 2>/dev/null | sort'")"
[ -z "$baked" ] || die "seed markdown found inside the image:
$baked"

say "   revision:    $rev"
say "   seed-corpus: $edition (verified empty in-image)"
say "   image id:    $src_id"

say "== streaming $image: $src -> $dst"
ssh -n "$src" "docker save '$image'" | ssh "$dst" "docker load"

dst_id="$(ssh -n "$dst" "docker image inspect '$image' --format '{{.Id}}'")" \
  || die "image did not land on $dst"
[ "$dst_id" = "$src_id" ] || die "image id differs after transfer: $src_id != $dst_id"

# Gate 4 — re-prove it on the destination. A tag is not a fact.
baked_dst="$(ssh -n "$dst" "docker run --rm --entrypoint sh '$image' -c 'find /opt/knowledge-ops/wiki /wiki -name \"*.md\" 2>/dev/null | sort'")"
[ -z "$baked_dst" ] || die "seed markdown present on $dst:
$baked_dst"

say "== OK"
say "   $dst now has $image"
say "   id $dst_id (identical to source), revision $rev, seed-corpus none"
say ""
say "   Next: pin it in the deploy compose and in"
say "   /home/muut/knowledge-ops-orgs/provision-knowledge-ops-org.sh (IMAGE=), then"
say "   docker compose -p <project> up -d --no-deps <services>"
