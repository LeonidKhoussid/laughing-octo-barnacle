"""Overload must not starve committed restores or bypass model protection."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Event
from types import SimpleNamespace

import anyio
import httpx
import pytest
from fastapi import FastAPI

from app.api.autocheck import build_autocheck_router
from app.core.engine import Engine
from app.detectors.structured import EmailDetector
from app.nlp.model_runtime import ModelUnavailable
from app.observability.logs import SafeLogger
from app.observability.metrics import Metrics
from app.policies.schema import ConsumerPolicy
from app.security.limits import BackpressureError, Limits
from app.vault.fingerprint import Fingerprinter
from app.vault.lifecycle import Lifecycle
from app.vault.memory import MemoryVault
from tests.unit.test_model_runtime import fake_runtime


def test_committed_restore_survives_full_mask_lane_and_worker_pool():
    async def exercise():
        workers = anyio.to_thread.current_default_thread_limiter()
        old_tokens = workers.total_tokens
        workers.total_tokens = 1
        policy = ConsumerPolicy(namespace="public", authentication="competition_exception")
        engine = Engine([EmailDetector()])
        lifecycle = Lifecycle(MemoryVault(), engine, Fingerprinter(b"test" * 8), "test")
        original = "Email: fixture@example.test"
        masked = lifecycle.process("public", "committed", original, policy).masked_text
        # A record in a different namespace must never be exposed by this lane.
        foreign = lifecycle.process("protected", "foreign", original, policy).masked_text
        entered, release = asyncio.Event(), Event()
        loop = asyncio.get_running_loop()
        mask = engine.mask

        def slow_mask(*args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return mask(*args, **kwargs)

        engine.mask = slow_mask
        limits, metrics = Limits(max_in_flight=1), Metrics()
        app = FastAPI()
        app.include_router(build_autocheck_router(
            engine, lifecycle._vault,
            SimpleNamespace(policy_version="test", get_consumer=lambda _: policy),
            SafeLogger("overload-isolation"), metrics, lifecycle=lifecycle, limits=limits,
        ))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async def post(identity, text):
                return await client.post("/process", json={"payload_id": identity, "payload": text})

            pending = asyncio.create_task(post("slow", original))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                for payload, expected in ((masked, original), (original, masked), (masked, original)):
                    response = await asyncio.wait_for(post("committed", payload), .5)
                    assert response.status_code == 200
                    assert response.json() == {"result": expected}
                assert (await post("committed", "unrelated")).status_code == 409
                for identity, payload in (("fresh", original), ("slow", original), ("foreign", foreign)):
                    rejected = await asyncio.wait_for(post(identity, payload), .5)
                    assert rejected.status_code == 429
                    assert rejected.headers["Retry-After"] == "1"
                    assert original not in rejected.text
                assert limits._in_flight == 1
                assert metrics.snapshot()["counters"]["pii_process_overload_total/reason=admission"] == 3
            finally:
                release.set()
                await pending
                workers.total_tokens = old_tokens
        assert limits._in_flight == 0

    asyncio.run(exercise())


def test_context_capacity_rejects_without_running_model_and_releases_after_failure():
    runtime = fake_runtime()
    runtime._context_slots = BoundedSemaphore(1)
    entered, release = Event(), Event()
    session = runtime._sessions["context"]
    original_run = session.run

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original_run(*args, **kwargs)

    session.run = blocked
    pairs = [("premise", "hypothesis")]
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(runtime.nli, pairs)
        try:
            assert entered.wait(2)
            with pytest.raises(BackpressureError) as exc:
                runtime.nli(pairs)
            assert exc.value.reason == "context"
            # NER remains usable while the expensive context lane is full.
            runtime.ner("Александр Сергеевич Пушкин")
        finally:
            release.set()
        assert len(pending.result()) == 1
    def fail(*args):
        raise ValueError("private-error-canary")
    session.run = fail
    with pytest.raises(ModelUnavailable):
        runtime.nli(pairs)
    session.run = original_run
    assert len(runtime.nli(pairs)) == 1


def test_context_rejection_is_retryable_and_does_not_probe_replay_lane(caplog):
    async def exercise():
        metrics = Metrics()
        policy = ConsumerPolicy(namespace="public", authentication="competition_exception")
        def reject(*args):
            raise BackpressureError("private-error-canary", reason="context")
        def unexpected_probe(*args):
            pytest.fail("Context overload must not enter the completed-record lane")
        app = FastAPI()
        app.include_router(build_autocheck_router(
            None, None, SimpleNamespace(policy_version="test", get_consumer=lambda _: policy),
            SafeLogger("context-overload-test"), metrics,
            lifecycle=SimpleNamespace(process=reject, process_existing=unexpected_probe), limits=Limits(),
        ))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/process", json={"payload_id": "x", "payload": "synthetic"})
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "1"
        assert "private-error-canary" not in response.text
        assert metrics.snapshot()["counters"]["pii_process_overload_total/reason=context"] == 1
    asyncio.run(exercise())
    assert '"overload_reason": "context"' in caplog.text
    assert "private-error-canary" not in caplog.text
