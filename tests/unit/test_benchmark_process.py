"""Accounting and correctness checks for the independent HTTP load scheduler."""
import asyncio
import json
import re

import httpx

from scripts.benchmark_process import run_benchmark, sample_for


def fake_handler(*, failures=None, delay=0, wrong_restore=False):
    seen, masks = [], {}
    failures = failures or {}

    async def handle(request):
        data = json.loads(request.content)
        seen.append(data)
        if delay:
            await asyncio.sleep(delay)
        effect = failures.get(len(seen))
        if effect == "timeout":
            raise httpx.ReadTimeout("test", request=request)
        if isinstance(effect, int):
            return httpx.Response(effect, json={"error": "test"})
        if effect == "malformed":
            return httpx.Response(200, json={})
        if effect == "incorrect":
            return httpx.Response(200, json={"result": data["payload"]})
        payload_id = data["payload_id"]
        if payload_id in masks:
            return httpx.Response(200, json={"result": "incorrect" if wrong_restore else masks[payload_id]})
        masks[payload_id] = data["payload"]
        text = re.sub(r"Клиент [^,]+", "Клиент ⟦PII:FULL_NAME:abc⟧", data["payload"])
        text = re.sub(r"\+7 903 \d{3}-\d{2}-\d{2}", "⟦PII:PHONE:def⟧", text)
        text = re.sub(r"sample-[a-z0-9-]+@example\.test", "⟦PII:EMAIL:ghi⟧", text)
        return httpx.Response(200, json={"result": text})
    return handle, seen


def run(handler, **options):
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
            return await run_benchmark(client, run_id="test", **options)
    return asyncio.run(execute())


def test_closed_counts_requests_once_and_validates_fresh_restoration():
    handler, seen = fake_handler()
    # This tests accounting, not whether a loaded host can finish in 30 ms.
    # The count cap still requires exactly eight requests.
    report = run(handler, mode="closed", duration=1, concurrency=1, count=8,
                 kinds=("private",), restore_every=1)
    assert len(seen) == report["counts"]["attempted"] == report["counts"]["scheduled"] == 8
    assert report["counts"]["successful_total"] == 8
    assert report["workload"]["request_mix"] == {"mask": 4, "restore": 4}
    assert len({item["payload_id"] for item in seen}) == 4
    assert len({seen[i]["payload"] for i in range(0, 8, 2)}) == 4
    assert report["latency_ms"]["all_http"]["count"] == 8


def test_all_failure_types_count_once_and_not_as_success():
    handler, seen = fake_handler(failures={1: 429, 2: 503, 3: "timeout", 4: "malformed", 5: "incorrect"})
    report = run(handler, mode="closed", duration=1, concurrency=1, count=6, kinds=("private",))
    counts = report["counts"]
    assert len(seen) == counts["attempted"] == 6
    assert counts["successful_total"] == 1
    assert counts["errors"] == 5
    assert counts["http_error"] == 2
    assert counts["timeout"] == counts["malformed_response"] == counts["correctness_failure"] == 1
    assert report["latency_ms"]["successful_http"]["count"] == 1
    assert report["latency_ms"]["all_http"]["count"] == 6


def test_failure_examples_keep_correctness_after_global_examples_fill():
    handler, _ = fake_handler(failures={**{i: 429 for i in range(1, 11)}, 11: "incorrect"})
    report = run(handler, mode="closed", duration=1, concurrency=1, count=12, kinds=("private",))
    assert [item["outcome"] for item in report["failure_examples"]] == ["http_error"] * 10
    grouped = report["failure_examples_by_outcome"]
    assert len(grouped["http_error"]) == 10
    assert grouped["correctness_failure"] == [{
        "slot": 10, "operation": "mask", "kind": "private", "status": 200,
        "outcome": "correctness_failure", "mismatch": "mask_oracle",
    }]
    assert grouped["timeout"] == grouped["transport_error"] == grouped["malformed_response"] == []
    assert all("payload" not in item and "result" not in item for examples in grouped.values() for item in examples)


def test_incorrect_restoration_is_not_a_success():
    handler, _ = fake_handler(wrong_restore=True)
    report = run(handler, mode="closed", duration=1, concurrency=1, count=2,
                 kinds=("private",), restore_every=1)
    assert report["counts"]["successful_total"] == 1
    assert report["counts"]["correctness_failure"] == 1


def test_open_overload_is_bounded_and_drain_not_counted_in_window():
    handler, seen = fake_handler(delay=.08)
    report = run(handler, mode="open", duration=.02, rps=500, concurrency=1,
                 max_lateness=1, kinds=("private",))
    counts = report["counts"]
    assert counts["scheduled"] == 10
    assert counts["scheduled"] == counts["attempted"] + counts["client_capacity_drops"] + counts["generator_late_drops"]
    assert len(seen) == counts["attempted"] == 1
    assert counts["completed_within_window"] == 0
    assert counts["completed_during_drain"] == 1
    assert report["rates"]["successful_within_window_rps"] == 0
    assert report["drain_seconds"] > 0
    assert report["rates"]["offered_rps"] == 500


def test_generator_late_slots_count_as_drops_not_http_errors():
    handler, seen = fake_handler()
    report = run(handler, mode="open", duration=.01, rps=1000, concurrency=1,
                 max_lateness=-1, kinds=("private",))
    assert report["counts"]["scheduled"] == report["counts"]["generator_late_drops"] == 10
    assert report["counts"]["attempted"] == report["counts"]["errors"] == len(seen) == 0


def test_mask_oracle_catches_partial_leak_and_damaged_public_content():
    sample = sample_for(0, "test", ("mixed",))
    result = sample.text
    for value in sample.private:
        result = result.replace(value, "⟦PII:FULL_NAME:token⟧")
    assert sample.accepts_mask(result)
    assert not sample.accepts_mask(result.replace("Альберт", "⟦PII:FULL_NAME:other⟧"))
    assert not sample.accepts_mask(result.replace("Клиент ⟦", "Клиент Иван ⟦"))
    assert not sample.accepts_mask(sample.text)
    public = sample_for(0, "test", ("public",))
    assert public.accepts_mask(public.text)
    assert not public.accepts_mask("all masked")


def test_required_mix_covers_all_seventeen_categories_with_independent_spans():
    from scripts.benchmark_process import required_cases
    samples = [sample_for(i, "required-fields", ("required",)) for i in range(len(required_cases()))]
    assert len({sample.kind for sample in samples}) == 17
    for sample in samples:
        result = sample.text
        for value in sample.private:
            result = result.replace(value, "⟦PII:TEST:abcdefghijklmnop⟧", 1)
        assert sample.accepts_mask(result)
        assert not sample.accepts_mask(sample.text)
