# Knowledge-Ops — Magic Unicorn federated grounding layer
FROM python:3.12.14-alpine3.23@sha256:167bc85084c9df34480efc26b4528fb68feaa8a79183b5658952137025b6f061

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    KNOWLEDGE_OPS_HOME=/opt/knowledge-ops \
    KNOWLEDGE_OPS_DATA=/data \
    KNOWLEDGE_OPS_DB=/data/knowledge_ops.sqlite3 \
    KNOWLEDGE_OPS_WIKI=/wiki

WORKDIR /opt/knowledge-ops

# Install deps first for layer caching. Ship MCP + live (redis) so the same
# image can run the REST API, the stdio MCP server, and the live adapters.
COPY --chown=10001:10001 pyproject.toml README.md requirements.lock ./
RUN pip install pip==26.1.2 \
    && pip install --require-hashes --requirement requirements.lock
COPY --chown=10001:10001 knowledge_ops ./knowledge_ops
RUN pip install --no-deps .

# App assets: wiki seed content + federation manifest.
# WARNING: everything under wiki/ is copied to the /wiki VOLUME and ingested into
# EVERY fresh instance by seed_wiki_from_disk() (knowledge_ops/api.py, every startup).
# That includes every customer's per-org instance. Ship nothing internal here.
# See wiki/README.txt and P-00099 finding 3 (2026-09-15).
COPY --chown=10001:10001 wiki ./wiki
COPY --chown=10001:10001 .well-known ./.well-known

# Build-time guard (P-00099 finding 3, 2026-09-15). wiki/ is a SHIPPING channel: it
# is copied onto the /wiki VOLUME below and ingested into every instance's KB. The
# 2026-09-15 finding was NOT that the repo held internal docs (it did) but that a
# build could carry them to a customer host with nobody typing anything. A deploy
# tree that legitimately holds the internal corpus on disk (bigboy) is exactly that
# accident. So: an image with seed markdown in it can only be produced by asking for
# it out loud. Default refuses and the build goes red.
ARG KNOWLEDGE_OPS_SEED_CORPUS=none
RUN set -eu; \
    found="$(find /opt/knowledge-ops/wiki -name '*.md' | sort)"; \
    if [ -n "$found" ] && [ "$KNOWLEDGE_OPS_SEED_CORPUS" != "internal" ]; then \
        echo "================ BUILD REFUSED ================" >&2; \
        echo "Seed markdown is present under wiki/ but this build did not pass" >&2; \
        echo "  --build-arg KNOWLEDGE_OPS_SEED_CORPUS=internal" >&2; \
        echo "Everything under wiki/ lands in EVERY instance's knowledge base as" >&2; \
        echo "scope=global owner=magic-unicorn (see wiki/README.txt). Offending files:" >&2; \
        echo "$found" >&2; \
        exit 1; \
    fi; \
    printf 'seed corpus edition=%s files=%s\n' "$KNOWLEDGE_OPS_SEED_CORPUS" \
        "$(printf '%s' "$found" | grep -c . || true)"

# Inspectable edition marker, so a running container can be checked with
# `docker inspect --format '{{index .Config.Labels "org.magicunicorn.knowledge-ops.seed-corpus"}}'`
# instead of trusting a tag name like "-noseed".
LABEL org.magicunicorn.knowledge-ops.seed-corpus="${KNOWLEDGE_OPS_SEED_CORPUS}"

# Data (sqlite) lives on a mounted volume so it survives restarts/redeploys.
RUN addgroup -S -g 10001 knowledgeops \
    && adduser -S -D -H -u 10001 -G knowledgeops -s /sbin/nologin knowledgeops \
    && mkdir -p /data /wiki \
    && cp -a /opt/knowledge-ops/wiki/. /wiki/ \
    && chown -R 10001:10001 /data /wiki /opt/knowledge-ops
VOLUME ["/data", "/wiki"]
USER 10001:10001

EXPOSE 8765
STOPSIGNAL SIGINT

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=5).read()"]

CMD ["python", "-m", "uvicorn", "knowledge_ops.api:app", "--host", "0.0.0.0", "--port", "8765"]
