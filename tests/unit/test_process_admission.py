"""HTTP admission must happen before the shared synchronous worker queue."""
import asyncio
import threading
from types import SimpleNamespace

import anyio
import httpx
import pytest
from fastapi import FastAPI

from app.api.autocheck import build_autocheck_router
from app.nlp.model_runtime import ModelUnavailable
from app.observability.logs import SafeLogger
from app.observability.metrics import Metrics
from app.policies.schema import ConsumerPolicy
from app.security.limits import Limits
from app.vault.lifecycle import ConflictError


CANARY = "admission-private-canary@example.test"


def application(process, limits):
    policy = ConsumerPolicy(name="autocheck", namespace="isolated-autocheck", authentication="competition_exception")
    store = SimpleNamespace(policy_version="test", get_consumer=lambda name: policy)
    app = FastAPI()
    app.include_router(build_autocheck_router(
        None, None, store, SafeLogger("admission-test"), Metrics(),
        lifecycle=SimpleNamespace(process=process), limits=limits,
    ))
    return app


def post(client, identity):
    return client.post("/process", json={"payload_id": identity, "payload": CANARY})


def test_overload_rejected_before_saturated_worker_pool_and_slots_reused():
    async def exercise():
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous_tokens = limiter.total_tokens
        limiter.total_tokens = 2
        entered = asyncio.Event()
        release = threading.Event()
        lock = threading.Lock()
        loop = asyncio.get_running_loop()
        calls = []
        limits = Limits(max_in_flight=2)

        def process(namespace, identity, text, policy):
            with lock:
                calls.append(identity)
                if len(calls) == 2:
                    loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), "test worker was not released"
            return SimpleNamespace(masked_text="protected", mapping={})

        app = application(process, limits)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            pending = [asyncio.create_task(post(client, str(i))) for i in range(2)]
            try:
                await asyncio.wait_for(entered.wait(), 2)
                assert limits._in_flight == 2
                response = await asyncio.wait_for(post(client, "overload"), 0.5)
                assert response.status_code == 429
                assert response.headers["Retry-After"] == "1"
                assert CANARY not in response.text
                assert "overload" not in calls
                assert limits._in_flight == 2
            finally:
                release.set()
                completed = await asyncio.gather(*pending)
                limiter.total_tokens = previous_tokens
            assert all(response.status_code == 200 for response in completed)
            assert limits._in_flight == 0
            assert (await post(client, "next")).status_code == 200
            assert limits._in_flight == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("error,status", [(ModelUnavailable(), 503), (ConflictError(CANARY), 409), (ValueError(CANARY), 422)])
def test_failure_releases_admission_without_input_reflection(error, status):
    async def exercise():
        calls = 0
        limits = Limits(max_in_flight=1)

        def process(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise error
            return SimpleNamespace(masked_text="protected", mapping={})

        app = application(process, limits)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await post(client, "failure")
            assert response.status_code == status
            assert CANARY not in response.text
            assert limits._in_flight == 0
            assert (await post(client, "retry")).status_code == 200
            assert limits._in_flight == 0

    asyncio.run(exercise())


def test_deadline_includes_wait_for_worker():
    async def exercise():
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous_tokens = limiter.total_tokens
        limiter.total_tokens = 1
        worker_busy = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        limits = Limits(max_in_flight=1, max_processing_seconds=0.01)
        calls = []

        def occupy_worker():
            loop.call_soon_threadsafe(worker_busy.set)
            assert release.wait(5)

        def process(*args):
            calls.append(True)
            return SimpleNamespace(masked_text="protected", mapping={})

        holder = asyncio.create_task(anyio.to_thread.run_sync(occupy_worker))
        request = None
        app = application(process, limits)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            try:
                await asyncio.wait_for(worker_busy.wait(), 2)
                request = asyncio.create_task(post(client, "queued"))
                await asyncio.sleep(0.05)
                response = await asyncio.wait_for(request, 0.5)
                assert limits._in_flight == 0
                assert not calls
                release.set()
                assert response.status_code == 429
                assert response.headers["Retry-After"] == "1"
                assert CANARY not in response.text
                assert limits._in_flight == 0
            finally:
                release.set()
                await holder
                if request is not None:
                    await request
                limiter.total_tokens = previous_tokens

    asyncio.run(exercise())


def test_anyio_cancellation_does_not_release_slot_before_worker_finishes():
    async def exercise():
        entered = asyncio.Event()
        release = threading.Event()
        done = asyncio.Event()
        loop = asyncio.get_running_loop()
        limits = Limits(max_in_flight=1)
        scope = anyio.CancelScope()

        def process(*args):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return SimpleNamespace(masked_text="protected", mapping={})

        app = application(process, limits)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async def pending_request():
                with scope:
                    await post(client, "cancelled")
                done.set()

            async with anyio.create_task_group() as group:
                group.start_soon(pending_request)
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    scope.cancel()
                    await asyncio.sleep(0.01)
                    assert not done.is_set()
                    assert limits._in_flight == 1
                    assert (await post(client, "overload")).status_code == 429
                finally:
                    release.set()
                await asyncio.wait_for(done.wait(), 2)
            assert limits._in_flight == 0

    asyncio.run(exercise())


def test_running_deadline_returns_before_worker_finishes_and_preserves_capacity():
    async def exercise():
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        limits = Limits(max_in_flight=1, max_processing_seconds=0.1)
        committed = []

        def process(namespace, identity, text, policy):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            committed.append(identity)
            return SimpleNamespace(masked_text="protected", mapping={})

        app = application(process, limits)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            pending = asyncio.create_task(post(client, "timed-out"))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                response = await asyncio.wait_for(pending, 0.5)
                assert response.status_code == 429
                assert response.headers["Retry-After"] == "1"
                assert CANARY not in response.text
                assert limits._in_flight == 1
                assert not committed
                assert (await post(client, "overload")).status_code == 429
            finally:
                release.set()
                await wait_for_in_flight(limits, 0)
            assert committed == ["timed-out"]
            assert (await post(client, "recovered")).status_code == 200

    asyncio.run(exercise())


async def wait_for_in_flight(limits, expected):
    async def wait():
        while limits._in_flight != expected:
            await asyncio.sleep(0)
    await asyncio.wait_for(wait(), 2)


def test_native_cancellation_retains_slot_until_running_worker_finishes():
    async def exercise():
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        limits = Limits(max_in_flight=1)
        calls = []

        def process(*args):
            calls.append(True)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return SimpleNamespace(masked_text="protected", mapping={})

        app = application(process, limits)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            pending = asyncio.create_task(post(client, "native-cancel"))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                assert limits._in_flight == 1
                rejected = await asyncio.wait_for(post(client, "overload"), 0.5)
                assert rejected.status_code == 429
                assert rejected.headers["Retry-After"] == "1"
                assert calls == [True]
            finally:
                release.set()
                await wait_for_in_flight(limits, 0)
            assert (await post(client, "recovered")).status_code == 200
            assert limits._in_flight == 0

    asyncio.run(exercise())


def test_native_cancellation_while_waiting_for_worker_releases_slot():
    async def exercise():
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous_tokens = limiter.total_tokens
        limiter.total_tokens = 1
        busy = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        limits = Limits(max_in_flight=1)
        calls = []

        def occupy_worker():
            loop.call_soon_threadsafe(busy.set)
            assert release.wait(5)

        def process(*args):
            calls.append(True)
            return SimpleNamespace(masked_text="protected", mapping={})

        holder = asyncio.create_task(anyio.to_thread.run_sync(occupy_worker))
        app = application(process, limits)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            try:
                await asyncio.wait_for(busy.wait(), 2)
                pending = asyncio.create_task(post(client, "queued-cancel"))
                await wait_for_in_flight(limits, 1)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                assert limits._in_flight == 0
                assert not calls
            finally:
                release.set()
                await holder
                limiter.total_tokens = previous_tokens
            assert (await post(client, "recovered")).status_code == 200
            assert calls == [True]
            assert limits._in_flight == 0

    asyncio.run(exercise())


def test_cancelled_scheduled_worker_cannot_start_later(monkeypatch):
    async def exercise():
        scheduled = asyncio.Event()
        wait = asyncio.Event()
        callbacks = []
        calls = []
        limits = Limits(max_in_flight=1)

        async def schedule_without_starting(callback):
            callbacks.append(callback)
            scheduled.set()
            await wait.wait()
            return callback()

        def process(*args):
            calls.append(True)
            return SimpleNamespace(masked_text="protected", mapping={})

        monkeypatch.setattr("app.api.autocheck.run_in_threadpool", schedule_without_starting)
        app = application(process, limits)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            pending = asyncio.create_task(post(client, "scheduled-cancel"))
            await asyncio.wait_for(scheduled.wait(), 2)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert limits._in_flight == 0
            # A thread can have received work just before its awaiting task was
            # cancelled. Starting that callback later must not start inference.
            assert await asyncio.to_thread(callbacks[0]) is None
            assert not calls
            assert limits._in_flight == 0

    asyncio.run(exercise())


def test_dispatch_failure_releases_admission(monkeypatch):
    async def exercise():
        limits = Limits(max_in_flight=1)
        calls = []

        async def failed_dispatch(callback):
            raise RuntimeError(CANARY)

        def process(*args):
            calls.append(True)

        monkeypatch.setattr("app.api.autocheck.run_in_threadpool", failed_dispatch)
        app = application(process, limits)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await post(client, "dispatch-failed")
            assert response.status_code == 422
            assert CANARY not in response.text
            assert limits._in_flight == 0
            assert not calls

    asyncio.run(exercise())


def test_overload_metric_preserves_status_label():
    metrics = Metrics()
    metrics.inc("pii_requests_total", operation="process", status="overload")
    assert metrics.snapshot()["counters"] == {
        "pii_requests_total/operation=process/status=overload": 1,
    }
