# Contributing to Knowledge-Ops

Knowledge-Ops is licensed under the [Apache License 2.0](LICENSE). Contributions are made under the Apache License 2.0, the same licence as the repository. By opening a pull request you confirm you wrote the change or have the right to submit it under that licence.

## Setup

Python 3.11 or newer. CI and the container image use 3.12.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[all,test]'
```

To match the image, install the hashed lock instead, then the package with `--no-deps`. The commands are in the README under [Run from source](README.md#run-from-source) and [Development](README.md#development).

Run the checks CI runs:

```bash
python -m pytest -q
python -m knowledge_ops.cli evaluate-memory
ruff check knowledge_ops tests
```

The UI has no build step. `python3 dev/mock_api.py` serves it with canned JSON.

## Branches

There is no `main` branch. The integration branch is `codex/knowledge-openwiki-hardening`. Open pull requests against that branch. Keep commits focused. Do not force-push shared history.

## Secrets and the wiki seed

Do not commit `.env` files, bearer tokens, private keys, or customer data. `.env.example` is names and local defaults only.

Do not add Markdown under `wiki/` unless you mean to ship it inside the image. The Dockerfile refuses the build when any `*.md` is there, unless the build passes `--build-arg KNOWLEDGE_OPS_SEED_CORPUS=internal`. Startup ingestion is a second switch, `KNOWLEDGE_OPS_SEED_INTERNAL_CORPUS=1`. Both default to off. `wiki/README.txt` explains why.

## What to read

- [README](README.md) for how to run the service
- [OpenWiki Governance](docs/OPENWIKI-GOVERNANCE.md)
- [Governed Memory Lifecycle](docs/MEMORY-LIFECYCLE.md)
- [Operations Runbook](docs/OPERATIONS-RUNBOOK.md)
