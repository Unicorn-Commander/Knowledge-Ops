# Security policy

## Reporting a vulnerability

Please do not report security vulnerabilities through public GitHub issues or pull requests.

Report them privately through GitHub Security Advisories on [github.com/Unicorn-Commander/Knowledge-Ops](https://github.com/Unicorn-Commander/Knowledge-Ops/security). Include what you can:

- A description of the issue and its impact
- Steps to reproduce, including the version or commit
- Any suggested fix

Do not include live bearer tokens, signing keys, or private documents. If a token appeared in a report by mistake, say so and rotate it.

This repository has no tagged release yet. Fixes land on `codex/knowledge-openwiki-hardening` until a release is tagged.

## Scope

This policy covers the Knowledge-Ops code in this repository: the API, the Observatory UI, the MCP server, the compose file, and the container image built from the Dockerfile. Report vulnerabilities in Python, FastAPI, or other dependencies to their maintainers as well when they affect a deployment.

## What the service already enforces

API routes under `/kb.*` require a verified bearer token unless `KNOWLEDGE_OPS_API_REQUIRE_AUTH` is explicitly off. Leave that off only while the port is bound to loopback. `POST /mcp` checks a bearer JWT even when API auth is off. The standalone compose publishes `127.0.0.1:8765` and keeps the issuer on the compose network.

The bootstrap admin token is written mode `0600` on a volume mounted only by the issuer container. Do not commit it, and do not paste it into an issue.

OpenWiki imports are untrusted until an administrator approves them. See [OpenWiki Governance](docs/OPENWIKI-GOVERNANCE.md).
