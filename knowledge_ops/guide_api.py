"""Guide endpoints: /kb.guide.info, /kb.guide.agents, /kb.guide.chat.

A small authenticated proxy to Unicorn Brigade so entitled Knowledge-Ops users
can chat with Brigade agents without ever holding a Brigade credential. The
same principal dependency as /kb.graph gates every route (401 = no/invalid
token via the session path, 403 = authenticated but missing ``read:knowledge``).

Security posture: ``KNOWLEDGE_OPS_BRIGADE_KEY`` is a secret. It travels to
Brigade as ``X-API-Key`` only — it is never echoed into a response body and
never logged (upstream failures log the exception class name and HTTP status,
nothing else). The caller's verified identity is forwarded upstream as
``X-Forwarded-User`` so Brigade can attribute the conversation. ``agent_id``
is caller input; it is percent-encoded into the upstream path so it can never
traverse out of ``/api/v1/agents/``.

Upstream error contract (stable strings the frontend keys on):
``503 guide-not-configured`` (no key), ``502 guide-billing`` (upstream 402),
``502 guide-upstream`` (any other upstream failure/undecodable reply),
``504 guide-timeout`` (upstream slower than the 60s chat / 15s list budget).
"""
from __future__ import annotations

import logging
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .authorization import Principal
from .brigade_auth import _USER_AGENT  # Brigade's edge 403s default UAs; reuse the real one
from .config import settings

logger = logging.getLogger("knowledge_ops.guide")

router = APIRouter()

_AGENTS_TIMEOUT = 15.0
_CHAT_TIMEOUT = 60.0


def _current_principal(request: Request) -> Principal:
    # Late import: api.py includes this router, so a module-level import of the
    # dependency would be circular. The lookup also honors test monkeypatching.
    from .api import current_principal

    return current_principal(request)


GuidePrincipal = Annotated[Principal, Depends(_current_principal)]


# ---------------------------------------------------------------- config plumbing
def _brigade_base() -> str:
    return settings.brigade_url.rstrip("/")


def _configured() -> bool:
    return bool(settings.brigade_key.strip())


def _require_configured() -> None:
    if not _configured():
        raise HTTPException(status_code=503, detail="guide-not-configured")


def _upstream_headers() -> dict[str, str]:
    """Auth headers for Brigade. Never log or echo this dict — it holds the key."""
    return {
        "X-API-Key": settings.brigade_key.strip(),
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
    }


def _transport() -> httpx.AsyncBaseTransport | None:
    """Test seam: tests monkeypatch this to an ``httpx.MockTransport`` so the
    real request path (URL join, headers, JSON encoding) runs without network."""
    return None


# ---------------------------------------------------------------- upstream calls
async def _call_upstream(call, url: str, **kwargs) -> httpx.Response:
    """One Brigade round-trip with the Guide's uniform error mapping.

    Order matters: ``TimeoutException`` subclasses ``HTTPError``, so it is
    caught first. Log lines carry only the exception class / status code —
    never headers, bodies, or the key.
    """
    try:
        res = await call(url, **kwargs)
    except httpx.TimeoutException as exc:
        logger.warning("guide upstream timeout error=%s", type(exc).__name__)
        raise HTTPException(status_code=504, detail="guide-timeout") from exc
    except httpx.HTTPError as exc:
        logger.warning("guide upstream unreachable error=%s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="guide-upstream") from exc
    if res.status_code == 402:
        logger.warning("guide upstream billing status=402")
        raise HTTPException(status_code=502, detail="guide-billing")
    if res.status_code >= 400:
        logger.warning("guide upstream error status=%s", res.status_code)
        raise HTTPException(status_code=502, detail="guide-upstream")
    return res


def _decode_json(res: httpx.Response) -> Any:
    try:
        return res.json()
    except ValueError as exc:
        logger.warning("guide upstream sent undecodable json")
        raise HTTPException(status_code=502, detail="guide-upstream") from exc


# ---------------------------------------------------------------- shape tolerance
def _agent_rows(data: Any) -> list:
    """The agent list wherever the upstream shape put it."""
    if isinstance(data, dict):
        for key in ("agents", "data", "items", "result", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        return []
    return data if isinstance(data, list) else []


def _agent_entry(row: Any) -> dict | None:
    """Normalize one upstream agent row to {id, name, description} (or skip it)."""
    if isinstance(row, str):
        return {"id": row, "name": row, "description": ""} if row else None
    if not isinstance(row, dict):
        return None
    agent_id = row.get("id") or row.get("agent_id") or row.get("slug") or row.get("name")
    if agent_id in (None, ""):
        return None
    name = row.get("name") or row.get("title") or row.get("display_name") or agent_id
    description = row.get("description") or row.get("summary") or row.get("about") or ""
    return {"id": str(agent_id), "name": str(name), "description": str(description)}


def _extract_reply(data: Any) -> str | None:
    """The assistant text wherever the upstream shape put it.

    Accepts ``reply`` / ``response`` / ``message`` / ``content`` (string or
    ``{content}`` dict — Brigade's chat route answers ``{"content": ...}``)
    and the OpenAI-style ``choices[0].message.content``.
    """
    if isinstance(data, str):
        return data if data.strip() else None
    if not isinstance(data, dict):
        return None
    for key in ("reply", "response", "message", "content"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            content = value.get("content")
            if isinstance(content, str) and content.strip():
                return content
    choices = data.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content
    return None


# ---------------------------------------------------------------- default agent
async def _resolve_default_agent(client: httpx.AsyncClient) -> str | None:
    """KNOWLEDGE_OPS_BRIGADE_AGENT as an upstream agent id.

    The configured value may already be an id (verbatim match wins) or a
    display name (resolved case-insensitively via the agents list). A failed
    or unhelpful listing degrades to the raw configured value — resolution can
    improve a chat, never block one.
    """
    configured = settings.brigade_agent.strip()
    if not configured:
        return None
    try:
        res = await client.get(f"{_brigade_base()}/api/v1/agents", headers=_upstream_headers())
        res.raise_for_status()
        rows = [entry for entry in map(_agent_entry, _agent_rows(res.json())) if entry]
    except Exception as exc:
        logger.debug("guide default-agent resolution skipped error=%s", type(exc).__name__)
        return configured
    if any(row["id"] == configured for row in rows):
        return configured
    lowered = configured.casefold()
    for row in rows:
        if row["name"].strip().casefold() == lowered:
            return row["id"]
    return configured


# ---------------------------------------------------------------- request bodies
class GuideChatMessage(BaseModel):
    role: str = Field(min_length=1, max_length=32)
    content: str = Field(max_length=32_000)


class GuideChatRequest(BaseModel):
    agent_id: str | None = Field(default=None, max_length=200)
    messages: list[GuideChatMessage] = Field(min_length=1, max_length=200)


# ---------------------------------------------------------------- routes
@router.get("/kb.guide.info")
def kb_guide_info(principal: GuidePrincipal) -> dict:
    """Whether the Guide is wired up, and the configured default agent.

    Cheap and local — no upstream call, and (like every response here) never
    the key itself.
    """
    principal.require("read:knowledge")
    return {
        "configured": _configured(),
        "default_agent": settings.brigade_agent.strip() or None,
    }


@router.get("/kb.guide.agents")
async def kb_guide_agents(principal: GuidePrincipal) -> dict:
    """The Brigade agent roster, normalized to [{id, name, description}]."""
    principal.require("read:knowledge")
    _require_configured()
    async with httpx.AsyncClient(timeout=_AGENTS_TIMEOUT, transport=_transport()) as client:
        res = await _call_upstream(
            client.get, f"{_brigade_base()}/api/v1/agents", headers=_upstream_headers()
        )
        rows = _agent_rows(_decode_json(res))
    agents = [entry for entry in map(_agent_entry, rows) if entry]
    allow = [a.strip() for a in settings.brigade_agents.split(",") if a.strip()]
    if allow:
        allowed = {a.lower() for a in allow}
        filtered = [a for a in agents if str(a.get("id", "")).lower() in allowed
                    or str(a.get("name", "")).lower() in allowed]
        if filtered:
            agents = filtered
        elif not agents:
            # Brigade's list endpoint doesn't reflect key_names grants (returns
            # empty for app keys) — the configured allowlist IS the roster.
            agents = [{"id": a, "name": a, "description": ""} for a in allow]
    return {"agents": agents}


@router.post("/kb.guide.chat")
async def kb_guide_chat(principal: GuidePrincipal, body: GuideChatRequest) -> dict:
    """One chat turn with a Brigade agent on behalf of the caller.

    The upstream payload is the tolerant ``{"message", "history"}`` form: the
    last user message is the prompt, everything before it is history.
    """
    principal.require("read:knowledge")
    _require_configured()

    last_user = next(
        (i for i in range(len(body.messages) - 1, -1, -1) if body.messages[i].role == "user"),
        None,
    )
    if last_user is None:
        raise HTTPException(status_code=400, detail="guide-no-user-message")
    message = body.messages[last_user].content
    history = [{"role": m.role, "content": m.content} for m in body.messages[:last_user]]

    async with httpx.AsyncClient(timeout=_CHAT_TIMEOUT, transport=_transport()) as client:
        agent_id = (body.agent_id or "").strip() or await _resolve_default_agent(client)
        if not agent_id:
            raise HTTPException(status_code=400, detail="guide-no-agent")
        res = await _call_upstream(
            client.post,
            # quote(safe="") so caller-supplied ids cannot traverse the path.
            f"{_brigade_base()}/api/v1/agents/{quote(agent_id, safe='')}/chat",
            headers={**_upstream_headers(), "X-Forwarded-User": principal.subject},
            json={"message": message, "history": history},
        )
        data = _decode_json(res)

    reply = _extract_reply(data)
    if reply is None:
        logger.warning("guide upstream reply shape unrecognized")
        raise HTTPException(status_code=502, detail="guide-upstream")
    return {"reply": reply, "agent_id": agent_id}
