#!/bin/sh
# The Docker daemon may be outside the CI job. Transfer files through Docker's
# archive API instead of assuming job paths exist on the daemon host.
set -eu
if [ "$#" -lt 3 ]; then
  echo 'usage: scan_image_archive.sh IMAGE_ARCHIVE REPORT TRIVY_ARGS...' >&2
  exit 2
fi
archive=$1
report=$2
shift 2
test -s "$archive"
scanner=''
cleanup() {
  if [ -n "$scanner" ]; then docker rm -f "$scanner" >/dev/null; fi
}
trap cleanup EXIT HUP INT TERM
scanner=$(docker create \
  aquasec/trivy@sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f \
  image --input /tmp/image.tar --output /tmp/report "$@")
docker cp "$archive" "$scanner:/tmp/image.tar"
# docker start --attach output/exit behavior differs by daemon version; inspect
# the container's recorded exit status so a finding cannot turn into a pass.
docker start --attach "$scanner" || true
test "$(docker inspect --format '{{.State.Status}}' "$scanner")" = exited
status=$(docker inspect --format '{{.State.ExitCode}}' "$scanner")
docker cp "$scanner:/tmp/report" "$report"
if [ "$status" -ne 0 ]; then cat "$report" >&2; fi
exit "$status"
