"""Raw JSON request-body cap applied before FastAPI parses a request."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

_BODY_TOO_LARGE = b'{"detail":{"code":"too_large","message":"request body exceeds the size limit"}}'
_TARGETS = frozenset({"/process", "/config/update"})
_PREFIXES = ("/demo/", "/trust-lab/")


class RawJSONBodyLimit:
    """ASGI middleware that bounds raw JSON bytes and replays an accepted body."""

    def __init__(self, app: Callable[..., Awaitable[None]], max_body_bytes: int, on_reject: Callable[[str], None] | None = None) -> None:
        if max_body_bytes <= 0:
            raise ValueError("raw JSON body limit must be positive")
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.on_reject = on_reject

    async def __call__(self, scope: dict[str, Any], receive: Callable[[], Awaitable[dict]], send: Callable[[dict], Awaitable[None]]) -> None:
        if not self._targets(scope):
            await self.app(scope, receive, send)
            return
        if self._declared_too_large(scope):
            await self._reject(scope, send)
            return

        buffered = bytearray()
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            if len(chunk) > self.max_body_bytes - size:
                await self._reject(scope, send)
                return
            buffered.extend(chunk)
            size += len(chunk)
            if not message.get("more_body", False):
                break

        body = bytes(buffered)
        del buffered
        replayed = False

        async def replay_receive() -> dict:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)

    def _targets(self, scope: dict[str, Any]) -> bool:
        path = scope.get("path", "")
        return scope.get("type") == "http" and scope.get("method") == "POST" and (
            path in _TARGETS or path.startswith(_PREFIXES)
        )

    def _declared_too_large(self, scope: dict[str, Any]) -> bool:
        for name, value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            try:
                if int(value) > self.max_body_bytes:
                    return True
            except ValueError:
                continue
        return False

    async def _reject(self, scope: dict, send: Callable[[dict], Awaitable[None]]) -> None:
        if self.on_reject is not None:
            self.on_reject(scope.get("path", ""))
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_BODY_TOO_LARGE)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": _BODY_TOO_LARGE})
