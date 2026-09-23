"""Raw JSON ASGI body cap runs before request-model parsing."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

import pytest

from app.security.body_limit import RawJSONBodyLimit


async def _call(
    messages: list[dict], *, path: str = "/process", headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[list[dict], list[bytes]]:
    seen: list[bytes] = []
    sent: list[dict] = []

    async def receive() -> dict:
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    async def downstream(scope: dict, replay_receive: Callable[[], Awaitable[dict]], send: Callable[[dict], Awaitable[None]]) -> None:
        while True:
            message = await replay_receive()
            if message["type"] == "http.disconnect":
                return
            seen.append(message.get("body", b""))
            if not message.get("more_body", False):
                return

    wrapped = RawJSONBodyLimit(downstream, 10)
    await wrapped(
        {"type": "http", "method": "POST", "path": path, "headers": headers or []}, receive, send,
    )
    return sent, seen


@pytest.mark.parametrize("path", ["/process", "/demo/mask", "/trust-lab/inspect", "/config/update"])
def test_declared_over_limit_is_rejected_before_receive_or_downstream(path: str) -> None:
    async def exercise() -> None:
        sent, seen = await _call(
            [{"type": "http.request", "body": b"secret", "more_body": False}],
            path=path, headers=[(b"content-length", b"11")],
        )
        assert seen == []
        assert sent[0]["status"] == 413
        assert b"secret" not in sent[1]["body"]

    asyncio.run(exercise())


def test_untargeted_path_is_not_buffered() -> None:
    async def exercise() -> None:
        sent, seen = await _call([
            {"type": "http.request", "body": b"12345678901", "more_body": False},
        ], path="/health")
        assert sent == []
        assert seen == [b"12345678901"]

    asyncio.run(exercise())


def test_streaming_without_content_length_is_bounded() -> None:
    async def exercise() -> None:
        sent, seen = await _call([
            {"type": "http.request", "body": b"12345", "more_body": True},
            {"type": "http.request", "body": b"67890", "more_body": True},
            {"type": "http.request", "body": b"x", "more_body": False},
        ])
        assert seen == []
        assert sent[0]["status"] == 413

    asyncio.run(exercise())


def test_exact_raw_limit_is_accepted_and_replayed() -> None:
    async def exercise() -> None:
        sent, seen = await _call([
            {"type": "http.request", "body": b"1234", "more_body": True},
            {"type": "http.request", "body": b"567890", "more_body": False},
        ], headers=[(b"content-length", b"10")])
        assert sent == []
        assert seen == [b"1234567890"]

    asyncio.run(exercise())


def test_escaped_unicode_json_is_counted_as_raw_bytes_and_decodes_after_replay() -> None:
    async def exercise() -> None:
        body = json.dumps({"payload": "Привет"}).encode("ascii")
        decoded: list[str] = []

        async def receive() -> dict:
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict) -> None:
            raise AssertionError(message)

        async def downstream(scope: dict, replay_receive: Callable[[], Awaitable[dict]], send: Callable[[dict], Awaitable[None]]) -> None:
            message = await replay_receive()
            decoded.append(json.loads(message["body"])["payload"])

        wrapped = RawJSONBodyLimit(downstream, len(body))
        await wrapped({"type": "http", "method": "POST", "path": "/process", "headers": []}, receive, send)
        assert decoded == ["Привет"]

    asyncio.run(exercise())


def test_disconnect_does_not_invoke_downstream_or_send_response() -> None:
    async def exercise() -> None:
        sent, seen = await _call([{"type": "http.disconnect"}])
        assert sent == []
        assert seen == []

    asyncio.run(exercise())
