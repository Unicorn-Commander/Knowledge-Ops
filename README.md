# Knowledge-Ops

Magic Unicorn federated grounding layer. Agents call `kb.search` / `kb.ground`; the
service federates across org_memory, wiki, file_index, Qdrant, FalkorDB and Center
Deep, enforces scope, and returns cited passages.

## Run it

```bash
# Docker (preferred — persistent, restartable, self-seeds the wiki):
docker compose up -d --build            # http://127.0.0.1:8765

# Or directly:
python3 -m uvicorn knowledge_ops.api:app --host 127.0.0.1 --port 8765

# CLI demo:
python3 -m knowledge_ops.cli demo
```

Routes are **dot-style** — e.g. `POST /kb.ground` (not `/kb/ground`). Health at
`/health`, OpenAPI at `/docs`, federation manifest at `/.well-known/mcps.json`.

```bash
curl -s -X POST http://127.0.0.1:8765/kb.ground \
  -H "Content-Type: application/json" \
  -d '{"query":"What is Magic Unicorn?","scope":"global","k":6}'
```

See [KNOWLEDGE-OPS.md](KNOWLEDGE-OPS.md) for the federation map, REST routes, deploy
(Docker/systemd), live-adapter config, and Brigade registration.

## Tests

```bash
python3 -m pytest -q
```
