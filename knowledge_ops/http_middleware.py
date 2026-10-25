from __future__ import annotations

from collections import defaultdict, deque
import hashlib
import json
import threading
import time
from typing import Any

_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
    (b"content-security-policy", b"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"),
]

# Static UI files carry an ETag but no Cache-Control, so browsers cached them heuristically
# and kept running a replaced shell.js for hours after a deploy. Revalidate on every load;
# an unchanged file costs a 304.
_REVALIDATE_PREFIXES = ("/ui/", "/landing/")


class RequestGuardMiddleware:
    """Bound request bodies and apply a small in-process per-client rate limit."""

    def __init__(self, app: Any, max_bytes: int, per_minute: int):
        self.app = app
        self.max_bytes = max(1, max_bytes)
        self.per_minute = max(1, per_minute)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    async def _reject(self, send: Any, status: int, message: str, extra_headers: list[tuple[bytes, bytes]] | None = None) -> None:
        body = json.dumps({"error": {"type": "request_rejected", "message": message}}).encode()
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        headers.extend(_SECURITY_HEADERS)
        headers.extend(extra_headers or [])
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""
        client = scope.get("client") or ("unknown", 0)
        headers = {key.lower(): value for key, value in scope.get("headers") or []}
        token = headers.get(b"authorization")
        caller = hashlib.sha256(token).hexdigest()[:24] if token else str(client[0])
        key = f"{caller}:{path.split('/', 2)[1] if '/' in path else path}"
        if path not in {"/health", "/ready"} and not path.startswith("/ui/"):
            current = time.monotonic()
            with self._lock:
                window = self._hits[key]
                while window and current - window[0] >= 60:
                    window.popleft()
                if len(window) >= self.per_minute:
                    await self._reject(send, 429, "rate limit exceeded", [(b"retry-after", b"60")])
                    return
                window.append(current)

        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    await self._reject(send, 413, "request body is too large")
                    return
            except ValueError:
                await self._reject(send, 400, "invalid content-length")
                return

        if scope.get("method") in {"POST", "PUT", "PATCH"}:
            messages: list[dict] = []
            size = 0
            while True:
                message = await receive()
                messages.append(message)
                if message.get("type") == "http.request":
                    size += len(message.get("body") or b"")
                    if size > self.max_bytes:
                        await self._reject(send, 413, "request body is too large")
                        return
                    if not message.get("more_body"):
                        break
                elif message.get("type") == "http.disconnect":
                    return
            iterator = iter(messages)

            async def replay() -> dict:
                try:
                    return next(iterator)
                except StopIteration:
                    return {"type": "http.request", "body": b"", "more_body": False}

            receive = replay

        async def secure_send(message: dict) -> None:
            if message.get("type") == "http.response.start":
                response_headers = list(message.get("headers") or [])
                response_headers.extend(_SECURITY_HEADERS)
                if path.startswith(_REVALIDATE_PREFIXES) and not any(
                    name.lower() == b"cache-control" for name, _ in response_headers
                ):
                    response_headers.append((b"cache-control", b"no-cache"))
                message["headers"] = response_headers
            await send(message)

        await self.app(scope, receive, secure_send)
