"""Tests for the Guide proxy (/kb.guide.info, /kb.guide.agents, /kb.guide.chat).

Fully offline: Brigade is an ``httpx.MockTransport`` injected through the
``guide_api._transport`` seam, so the real request path (URL join, headers,
JSON encoding, error mapping) runs with zero network. Covers /kb.guide.info in
both configured states, the agents proxy + shape tolerance + 503 when
unconfigured, the chat happy path (payload split, identity header, path
quoting), reply-shape tolerance, the 402→guide-billing / other→guide-upstream
/ timeout→504 mappings, default-agent resolution by name, the read:knowledge
403, and — everywhere — that the Brigade key never appears in a response body
or a captured log line.
"""
from __future__ import annotations

import json
import logging

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import knowledge_ops.config as config
import knowledge_ops.guide_api as guide_api
from knowledge_ops.authorization import AuthorizationError, Principal

KEY = "REMOVED"
SUBJECT = "aaron@magicunicorn.tech"


def _reader() -> Principal:
    return Principal(
        subject=SUBJECT,
        scopes=frozenset({"read:knowledge"}),
        identities=frozenset({SUBJECT}),
    )


def _client(principal: Principal | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(guide_api.router)
    app.dependency_overrides[guide_api._current_principal] = (
        lambda: principal if principal is not None else _reader()
    )

    # Mirrors knowledge_ops.api's AuthorizationError handler (the router is
    # mounted there in production; this test app is standalone on purpose).
    @app.exception_handler(AuthorizationError)
    async def _forbidden(request, exc):
        return JSONResponse(
            {"error": {"type": "forbidden", "message": str(exc)}}, status_code=403
        )

    return TestClient(app)


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setattr(config.settings, "brigade_url", "http://brigade.test:8000")
    monkeypatch.setattr(config.settings, "brigade_key", KEY)
    monkeypatch.setattr(config.settings, "brigade_agent", "")


def _install_transport(monkeypatch, handler) -> list[httpx.Request]:
    """Route guide_api's httpx clients into a MockTransport; capture requests."""
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    monkeypatch.setattr(guide_api, "_transport", lambda: httpx.MockTransport(_handler))
    return captured


def _messages():
    return [
        {"role": "system", "content": "be kind"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "what is Knowledge-Ops?"},
    ]


# ---------------------------------------------------------------- kb.guide.info
def test_info_unconfigured(monkeypatch):
    monkeypatch.setattr(config.settings, "brigade_key", "   ")  # whitespace = unset
    monkeypatch.setattr(config.settings, "brigade_agent", "")
    res = _client().get("/kb.guide.info")
    assert res.status_code == 200
    assert res.json() == {"configured": False, "default_agent": None}


def test_info_configured_with_default_agent(configured, monkeypatch):
    monkeypatch.setattr(config.settings, "brigade_agent", "Scout")
    res = _client().get("/kb.guide.info")
    assert res.status_code == 200
    assert res.json() == {"configured": True, "default_agent": "Scout"}
    assert KEY not in res.text  # the key itself is never surfaced


# ---------------------------------------------------------------- kb.guide.agents
@pytest.mark.parametrize("wrap", [
    lambda rows: {"agents": rows},
    lambda rows: {"data": rows},
    lambda rows: rows,  # bare list upstream
])
def test_agents_proxy_maps_shape_tolerantly(configured, monkeypatch, wrap):
    rows = [
        {"id": "agt-1", "name": "Scout", "description": "Finds things"},
        {"agent_id": "agt-2", "title": "Quill", "summary": "Writes things"},
        {"slug": "agt-3"},
        "bare-agent",
        {"noise": True},  # unidentifiable -> skipped
    ]
    captured = _install_transport(
        monkeypatch, lambda request: httpx.Response(200, json=wrap(rows))
    )

    res = _client().get("/kb.guide.agents")

    assert res.status_code == 200
    assert res.json() == {"agents": [
        {"id": "agt-1", "name": "Scout", "description": "Finds things"},
        {"id": "agt-2", "name": "Quill", "description": "Writes things"},
        {"id": "agt-3", "name": "agt-3", "description": ""},
        {"id": "bare-agent", "name": "bare-agent", "description": ""},
    ]}
    (request,) = captured
    assert request.method == "GET"
    assert str(request.url) == "http://brigade.test:8000/api/v1/agents"
    assert request.headers["X-API-Key"] == KEY  # upstream header, never the body
    assert request.headers["User-Agent"].startswith("knowledge-ops/")  # Brigade edge 403s default UAs


def test_agents_not_configured_503(monkeypatch):
    monkeypatch.setattr(config.settings, "brigade_key", "")
    captured = _install_transport(monkeypatch, lambda request: httpx.Response(200, json=[]))
    res = _client().get("/kb.guide.agents")
    assert res.status_code == 503
    assert res.json() == {"detail": "guide-not-configured"}
    assert captured == []  # no upstream traffic without a key


def test_agents_upstream_error_maps_to_502(configured, monkeypatch):
    _install_transport(monkeypatch, lambda request: httpx.Response(500, text="boom"))
    res = _client().get("/kb.guide.agents")
    assert res.status_code == 502
    assert res.json() == {"detail": "guide-upstream"}


# ---------------------------------------------------------------- kb.guide.chat
def test_chat_happy_path_payload_and_identity_header(configured, monkeypatch):
    captured = _install_transport(
        monkeypatch, lambda request: httpx.Response(200, json={"reply": "KO is your observatory."})
    )

    res = _client().post("/kb.guide.chat", json={"agent_id": "agt-1", "messages": _messages()})

    assert res.status_code == 200
    assert res.json() == {"reply": "KO is your observatory.", "agent_id": "agt-1"}
    (request,) = captured  # explicit agent_id: no resolution round-trip
    assert request.method == "POST"
    assert str(request.url) == "http://brigade.test:8000/api/v1/agents/agt-1/chat"
    assert request.headers["X-API-Key"] == KEY
    assert request.headers["X-Forwarded-User"] == SUBJECT  # principal identity forwarded
    body = json.loads(request.content)
    assert body == {
        "message": "what is Knowledge-Ops?",  # last user message is the prompt
        "history": [
            {"role": "system", "content": "be kind"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
    }


def test_chat_agent_id_is_path_quoted(configured, monkeypatch):
    captured = _install_transport(
        monkeypatch, lambda request: httpx.Response(200, json={"reply": "ok"})
    )
    res = _client().post(
        "/kb.guide.chat",
        json={"agent_id": "../admin/keys", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert res.status_code == 200
    (request,) = captured
    # Caller input cannot traverse out of /api/v1/agents/. Assert the wire
    # form: httpx's .path property percent-DEcodes for display, raw_path is
    # what the upstream server actually receives.
    assert request.url.raw_path == b"/api/v1/agents/..%2Fadmin%2Fkeys/chat"


@pytest.mark.parametrize("upstream", [
    {"reply": "hi there"},
    {"response": "hi there"},
    {"message": "hi there"},
    {"message": {"content": "hi there"}},
    {"choices": [{"message": {"content": "hi there"}}]},
])
def test_chat_reply_shape_tolerance(configured, monkeypatch, upstream):
    _install_transport(monkeypatch, lambda request: httpx.Response(200, json=upstream))
    res = _client().post(
        "/kb.guide.chat", json={"agent_id": "agt-1", "messages": [{"role": "user", "content": "q"}]}
    )
    assert res.status_code == 200
    assert res.json() == {"reply": "hi there", "agent_id": "agt-1"}


def test_chat_unrecognized_reply_shape_is_502(configured, monkeypatch):
    _install_transport(monkeypatch, lambda request: httpx.Response(200, json={"status": "ok"}))
    res = _client().post(
        "/kb.guide.chat", json={"agent_id": "agt-1", "messages": [{"role": "user", "content": "q"}]}
    )
    assert res.status_code == 502
    assert res.json() == {"detail": "guide-upstream"}


def test_chat_upstream_402_maps_to_guide_billing(configured, monkeypatch):
    _install_transport(
        monkeypatch, lambda request: httpx.Response(402, json={"detail": "payment required"})
    )
    res = _client().post(
        "/kb.guide.chat", json={"agent_id": "agt-1", "messages": [{"role": "user", "content": "q"}]}
    )
    assert res.status_code == 502
    assert res.json() == {"detail": "guide-billing"}


def test_chat_upstream_timeout_maps_to_504(configured, monkeypatch):
    def _slow(request):
        raise httpx.ReadTimeout("upstream slow")

    _install_transport(monkeypatch, _slow)
    res = _client().post(
        "/kb.guide.chat", json={"agent_id": "agt-1", "messages": [{"role": "user", "content": "q"}]}
    )
    assert res.status_code == 504
    assert res.json() == {"detail": "guide-timeout"}


def test_chat_not_configured_503(monkeypatch):
    monkeypatch.setattr(config.settings, "brigade_key", "")
    res = _client().post(
        "/kb.guide.chat", json={"agent_id": "agt-1", "messages": [{"role": "user", "content": "q"}]}
    )
    assert res.status_code == 503
    assert res.json() == {"detail": "guide-not-configured"}


def test_chat_requires_a_user_message_and_an_agent(configured, monkeypatch):
    _install_transport(monkeypatch, lambda request: httpx.Response(200, json={"reply": "x"}))
    client = _client()
    # No user-role message anywhere -> nothing to send upstream.
    res = client.post(
        "/kb.guide.chat",
        json={"agent_id": "agt-1", "messages": [{"role": "assistant", "content": "hi"}]},
    )
    assert res.status_code == 400
    assert res.json() == {"detail": "guide-no-user-message"}
    # No agent_id in the body and no default configured.
    res = client.post("/kb.guide.chat", json={"messages": [{"role": "user", "content": "q"}]})
    assert res.status_code == 400
    assert res.json() == {"detail": "guide-no-agent"}
    # Empty messages fail pydantic validation before any guide logic runs.
    assert client.post("/kb.guide.chat", json={"messages": []}).status_code == 422


def test_chat_default_agent_name_resolves_via_agents_list(configured, monkeypatch):
    monkeypatch.setattr(config.settings, "brigade_agent", "Scout")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/agents":
            return httpx.Response(200, json={"agents": [
                {"id": "agt-7", "name": "Scout", "description": "Finds things"},
            ]})
        return httpx.Response(200, json={"reply": "found it"})

    captured = _install_transport(monkeypatch, handler)
    res = _client().post("/kb.guide.chat", json={"messages": [{"role": "user", "content": "q"}]})

    assert res.status_code == 200
    assert res.json() == {"reply": "found it", "agent_id": "agt-7"}  # name -> id
    assert [r.url.path for r in captured] == ["/api/v1/agents", "/api/v1/agents/agt-7/chat"]


def test_chat_default_agent_survives_failed_resolution(configured, monkeypatch):
    monkeypatch.setattr(config.settings, "brigade_agent", "agt-9")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/agents":
            return httpx.Response(500, text="listing down")  # resolution degrades
        return httpx.Response(200, json={"reply": "still here"})

    _install_transport(monkeypatch, handler)
    res = _client().post("/kb.guide.chat", json={"messages": [{"role": "user", "content": "q"}]})
    assert res.status_code == 200
    assert res.json() == {"reply": "still here", "agent_id": "agt-9"}


# ---------------------------------------------------------------- authz + secrecy
def test_guide_requires_read_scope_403(configured, monkeypatch):
    _install_transport(monkeypatch, lambda request: httpx.Response(200, json=[]))
    scopeless = Principal(
        subject="scopeless", scopes=frozenset(), identities=frozenset({"scopeless"})
    )
    client = _client(scopeless)
    for res in (
        client.get("/kb.guide.info"),
        client.get("/kb.guide.agents"),
        client.post(
            "/kb.guide.chat", json={"agent_id": "a", "messages": [{"role": "user", "content": "q"}]}
        ),
    ):
        assert res.status_code == 403
        assert res.json()["error"]["type"] == "forbidden"


def test_key_never_appears_in_responses_or_logs(configured, monkeypatch, caplog):
    """The Brigade key reaches upstream headers and nowhere else — not one
    response body, not one log line, across happy AND failing paths."""
    monkeypatch.setattr(config.settings, "brigade_agent", "Scout")
    state = {"fail": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            raise httpx.ReadTimeout("upstream slow")
        if request.url.path == "/api/v1/agents":
            return httpx.Response(200, json={"agents": [{"id": "agt-7", "name": "Scout"}]})
        return httpx.Response(402, json={"detail": "payment required"})

    captured = _install_transport(monkeypatch, handler)
    client = _client()
    caplog.set_level(logging.DEBUG)

    responses = [
        client.get("/kb.guide.info"),
        client.get("/kb.guide.agents"),
        client.post("/kb.guide.chat", json={"messages": [{"role": "user", "content": "q"}]}),  # 402 path
    ]
    state["fail"] = True
    responses.append(
        client.post(
            "/kb.guide.chat", json={"agent_id": "a", "messages": [{"role": "user", "content": "q"}]}
        )  # timeout path
    )

    assert {r.status_code for r in responses} == {200, 502, 504}
    for res in responses:
        assert KEY not in res.text
    assert KEY not in caplog.text
    assert captured and all(r.headers["X-API-Key"] == KEY for r in captured)  # went upstream only
