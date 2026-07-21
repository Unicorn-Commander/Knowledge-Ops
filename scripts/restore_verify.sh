#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: scripts/restore_verify.sh <archive.tar.gz> <empty-target-root>" >&2
  exit 2
fi

exec "${PYTHON:-python3}" -m knowledge_ops.cli restore-verify "$1" --target "$2"
