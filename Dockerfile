# Knowledge-Ops — Magic Unicorn federated grounding layer
FROM python:3.12.13-alpine3.23@sha256:601d3d3797e90e2534782e69c85fafb7971b43f24c7b1b079b7e48dd435e458d

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
