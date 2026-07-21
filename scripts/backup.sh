#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: scripts/backup.sh <archive.tar.gz>" >&2
  exit 2
fi

exec "${PYTHON:-python3}" -m knowledge_ops.cli backup --out "$1"
