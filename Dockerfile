# Knowledge-Ops — Magic Unicorn federated grounding layer
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    KNOWLEDGE_OPS_HOME=/opt/knowledge-ops \
    KNOWLEDGE_OPS_DATA=/data \
    KNOWLEDGE_OPS_DB=/data/knowledge_ops.sqlite3 \
    KNOWLEDGE_OPS_WIKI=/opt/knowledge-ops/wiki

WORKDIR /opt/knowledge-ops

# curl is used by the container HEALTHCHECK.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install deps first for layer caching. Ship MCP + live (redis) so the same
# image can run the REST API, the stdio MCP server, and the live adapters.
COPY pyproject.toml README.md ./
COPY knowledge_ops ./knowledge_ops
RUN pip install --upgrade pip && pip install '.[all]'

# App assets: wiki seed content + federation manifest.
COPY wiki ./wiki
COPY .well-known ./.well-known

# Data (sqlite) lives on a mounted volume so it survives restarts/redeploys.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8765
STOPSIGNAL SIGINT

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8765/health || exit 1

CMD ["python", "-m", "uvicorn", "knowledge_ops.api:app", "--host", "0.0.0.0", "--port", "8765"]
