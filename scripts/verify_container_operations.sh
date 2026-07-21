#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: scripts/verify_container_operations.sh <image>" >&2
  exit 2
fi

image="$1"
prefix="knowledge-ops-smoke-$$"
network="$prefix"
data_volume="${prefix}-data"
wiki_volume="${prefix}-wiki"
api="${prefix}-api"
worker="${prefix}-worker"

cleanup() {
  docker rm -f "$api" "$worker" >/dev/null 2>&1 || true
  docker volume rm "$data_volume" "$wiki_volume" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker network create "$network" >/dev/null
docker volume create "$data_volume" >/dev/null
docker volume create "$wiki_volume" >/dev/null

docker run --detach --name "$api" --network "$network" \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777 \
  --volume "$data_volume:/data" --volume "$wiki_volume:/wiki" \
  --env KNOWLEDGE_OPS_DATA=/data \
  --env KNOWLEDGE_OPS_DB=/data/knowledge_ops.sqlite3 \
  --env KNOWLEDGE_OPS_WIKI=/wiki \
  --env KNOWLEDGE_OPS_OPENWIKI_STAGING=/data/openwiki/staging \
  --env KNOWLEDGE_OPS_API_REQUIRE_AUTH=0 \
  --env KNOWLEDGE_OPS_REQUIRE_WORKER=1 \
  --env KNOWLEDGE_OPS_ENABLE_QDRANT=0 \
  --env KNOWLEDGE_OPS_ENABLE_FALKORDB=0 \
  --env KNOWLEDGE_OPS_ENABLE_CENTER_DEEP=0 \
  "$image" >/dev/null

docker run --detach --name "$worker" --network "$network" \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777 \
  --volume "$data_volume:/data" --volume "$wiki_volume:/wiki" \
  --env KNOWLEDGE_OPS_DATA=/data \
  --env KNOWLEDGE_OPS_DB=/data/knowledge_ops.sqlite3 \
  --env KNOWLEDGE_OPS_WIKI=/wiki \
  --env KNOWLEDGE_OPS_OPENWIKI_STAGING=/data/openwiki/staging \
  --env KNOWLEDGE_OPS_WORKER_ID=container-smoke-worker \
  --env KNOWLEDGE_OPS_ENABLE_QDRANT=0 \
  --env KNOWLEDGE_OPS_ENABLE_FALKORDB=0 \
  --env KNOWLEDGE_OPS_ENABLE_CENTER_DEEP=0 \
  --env KNOWLEDGE_OPS_DELETION_TARGETS='{}' \
  "$image" knowledge-ops worker >/dev/null

for attempt in $(seq 1 30); do
  if docker exec "$api" python -c \
    'import json, urllib.request; value=json.load(urllib.request.urlopen("http://127.0.0.1:8765/ready")); raise SystemExit(0 if value["ok"] and value["checks"]["worker"] else 1)' \
    >/dev/null 2>&1; then
    break
  fi
  test "$attempt" -lt 30
  sleep 1
done

docker exec "$api" python -c \
  'import json, urllib.request; body=json.dumps({"job_id":"container-review","kind":"review_scan","source_ref":"wiki","interval_seconds":60,"enabled":True}).encode(); request=urllib.request.Request("http://127.0.0.1:8765/kb.refresh_job", data=body, headers={"content-type":"application/json"}); urllib.request.urlopen(request).read()'

for attempt in $(seq 1 30); do
  if docker exec "$api" python -c \
    'import json, urllib.request; value=json.load(urllib.request.urlopen("http://127.0.0.1:8765/kb.refresh_jobs")); raise SystemExit(0 if any(run["status"] == "completed" for run in value["runs"]) else 1)' \
    >/dev/null 2>&1; then
    break
  fi
  test "$attempt" -lt 30
  sleep 1
done

docker restart "$api" "$worker" >/dev/null
for attempt in $(seq 1 30); do
  if docker exec "$api" python -c \
    'import json, urllib.request; value=json.load(urllib.request.urlopen("http://127.0.0.1:8765/ready")); raise SystemExit(0 if value["ok"] and value["checks"]["worker"] else 1)' \
    >/dev/null 2>&1; then
    break
  fi
  test "$attempt" -lt 30
  sleep 1
done

docker exec "$api" knowledge-ops backup --out /tmp/container-smoke-backup.tar.gz >/dev/null
docker exec "$api" sh -c 'test "$(stat -c %a /tmp/container-smoke-backup.tar.gz)" = 600'
docker exec "$api" knowledge-ops restore-verify \
  /tmp/container-smoke-backup.tar.gz --target /tmp/container-smoke-restored >/dev/null

echo "container worker, restart, backup, restore, and reindex smoke passed"
