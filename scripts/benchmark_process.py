"""Real HTTP /process benchmark; no retries, repeated IDs, or cached masks.

Open mode schedules HTTP request slots at a fixed rate, dropping late/full slots
instead of hiding generator overload in an unbounded queue. Closed mode measures
throughput at fixed concurrency. Successful RPS includes only correct responses.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, deque
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import statistics
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
TOKEN = r"(?:⟦PII:[A-Z_]+:[A-Za-z0-9_-]+⟧)"
FAILURE_OUTCOMES = ("http_error", "malformed_response", "correctness_failure", "timeout", "transport_error")


@dataclass(frozen=True)
class Sample:
    kind: str
    text: str
    private: tuple[str, ...]

    def accepts_mask(self, result: str) -> bool:
        """Oracle is independent of detectors: outside private spans is exact."""
        cursor, parts = 0, []
        for value in self.private:
            start = self.text.index(value, cursor)
            parts += [re.escape(self.text[cursor:start]), rf"{TOKEN}(?:\s*{TOKEN})*"]
            cursor = start + len(value)
        parts.append(re.escape(self.text[cursor:]))
        return re.fullmatch("".join(parts), result) is not None


@lru_cache(maxsize=1)
def required_cases() -> tuple[dict, ...]:
    """Frozen synthetic field values; independent of the detector's output."""
    path = ROOT / "tests/fixtures/requirements_detection_cases.json"
    return tuple(json.loads(path.read_text(encoding="utf-8"))["cases"])


def sample_for(index: int, run_id: str, kinds: tuple[str, ...]) -> Sample:
    kind = kinds[index % len(kinds)]
    if kind == "required":
        case = required_cases()[(index // len(kinds)) % len(required_cases())]
        suffix = f"\nКонтрольная метка: qz{run_id}x{index}."
        return Sample(case["detector"], case["text"] + suffix, tuple(case["sensitive"]))
    names = ("Иван Иванович Петров", "Анна Сергеевна Смирнова", "Пётр Алексеевич Иванов")
    variant = index // len(kinds)
    name = names[variant % len(names)]
    phone = f"+7 903 {index % 1000:03d}-{index // 1000 % 100:02d}-{index // 100000 % 100:02d}"
    email = f"sample-{run_id}-{index}@example.test"
    private = f"Клиент {name}, телефон {phone}, email {email}."
    public = (
        "Альберт Эйнштейн разработал теорию относительности.",
        "В романе «Анна Каренина» Алексей Александрович Каренин — вымышленный персонаж.",
        "На уроке литературы обсуждали произведения Александра Сергеевича Пушкина.",
    )[variant % 3]
    suffix = f" Контрольная метка: qz{run_id}x{index}."
    if kind == "public":
        return Sample(kind, public + suffix, ())
    return Sample(kind, (public + " " if kind == "mixed" else "") + private + suffix,
                  (name, phone, email))


@dataclass
class Observation:
    operation: str
    kind: str
    characters: int
    scheduled: float
    started: float
    finished: float
    status: int
    outcome: str


def distribution(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "mean": None, "p50": None, "p95": None, "p99": None, "max": None}
    values = sorted(values)
    return {"count": len(values), "mean": statistics.fmean(values),
            **{f"p{p}": values[max(0, math.ceil(p / 100 * len(values)) - 1)] for p in (50, 95, 99)},
            "max": values[-1]}


async def run_benchmark(client: httpx.AsyncClient, *, mode="open", duration=10.0,
                        rps=1000.0, concurrency=64, max_lateness=0.05,
                        kinds=("private", "public", "mixed"), restore_every=0,
                        count=None, run_id=None) -> dict:
    if mode not in {"open", "closed"} or duration <= 0 or rps <= 0 or concurrency < 1:
        raise ValueError("Positive duration/rps/concurrency and open/closed mode required")
    if not kinds or set(kinds) - {"private", "public", "mixed", "required"} or restore_every < 0:
        raise ValueError("Invalid workload mix")
    if count is not None and count < 1:
        raise ValueError("Count must be positive")
    run_id = run_id or uuid.uuid4().hex[:12]
    observations: list[Observation] = []
    ready = deque()
    active: set[asyncio.Task] = set()
    start = time.perf_counter()
    deadline = start + duration
    counters = Counter()
    generation_lag = []
    failure_examples = []
    failure_examples_by_outcome = {outcome: [] for outcome in FAILURE_OUTCOMES}

    async def request(slot: int, scheduled: float) -> None:
        # Every fresh mask has a unique ID AND text. A restore reuses only its own ID.
        if restore_every and slot % (restore_every + 1) == restore_every and ready:
            sample, payload_id, payload = ready.popleft()
            operation = "restore"
        else:
            if restore_every and slot % (restore_every + 1) == restore_every:
                counters["restore_slot_without_ready_mask"] += 1
            sample = sample_for(slot, run_id, kinds)
            payload_id, payload = f"bench-{run_id}-{slot}", sample.text
            operation = "mask"
        begun = time.perf_counter()
        status, outcome, result = 0, "transport_error", None
        try:
            response = await client.post("/process", json={"payload": payload, "payload_id": payload_id})
            status = response.status_code
            if status != 200:
                outcome = "http_error"
            else:
                try:
                    data = response.json()
                except (ValueError, TypeError):
                    data = None
                if not isinstance(data, dict) or not isinstance(data.get("result"), str):
                    outcome = "malformed_response"
                else:
                    result = data["result"]
                    valid = result == sample.text if operation == "restore" else sample.accepts_mask(result)
                    outcome = "success" if valid else "correctness_failure"
        except httpx.TimeoutException:
            outcome = "timeout"
        except httpx.RequestError:
            outcome = "transport_error"
        ended = time.perf_counter()
        observations.append(Observation(operation, sample.kind, len(payload), scheduled, begun,
                                        ended, status, outcome))
        if outcome == "success" and operation == "mask" and restore_every:
            ready.append((sample, payload_id, result))
        if outcome != "success":
            # Synthetic request IDs only; do not retain payload/response bodies.
            example = {"slot": slot, "operation": operation, "kind": sample.kind,
                       "status": status, "outcome": outcome}
            if outcome == "correctness_failure":
                example["mismatch"] = "restore_roundtrip" if operation == "restore" else "mask_oracle"
            if len(failure_examples) < 10:
                failure_examples.append(example)
            examples = failure_examples_by_outcome.get(outcome)
            if examples is not None and len(examples) < 10:
                examples.append(example)

    if mode == "open":
        slots = math.ceil(duration * rps)
        if count is not None:
            slots = min(slots, count)
        for slot in range(slots):
            scheduled = start + slot / rps
            await asyncio.sleep(max(0.0, scheduled - time.perf_counter()))
            lag = max(0.0, time.perf_counter() - scheduled)
            generation_lag.append(lag * 1000)
            counters["scheduled"] += 1
            if lag > max_lateness or time.perf_counter() >= deadline:
                counters["generator_late_drops"] += 1
            elif len(active) >= concurrency:
                counters["client_capacity_drops"] += 1
            else:
                task = asyncio.create_task(request(slot, scheduled))
                active.add(task)
                task.add_done_callback(active.discard)
        # Preserve the configured measurement window even if --count ends scheduling early.
        await asyncio.sleep(max(0.0, deadline - time.perf_counter()))
        if active:
            await asyncio.gather(*active)
    else:
        async def worker():
            while time.perf_counter() < deadline:
                slot = counters["scheduled"]
                if count is not None and slot >= count:
                    return
                counters["scheduled"] += 1
                await request(slot, time.perf_counter())
        await asyncio.gather(*(worker() for _ in range(concurrency)))
        await asyncio.sleep(max(0.0, deadline - time.perf_counter()))
    finished = time.perf_counter()
    outcome_counts = Counter(o.outcome for o in observations)
    statuses = Counter(str(o.status) for o in observations)
    successful = [o for o in observations if o.outcome == "success"]
    within = [o for o in observations if o.finished <= deadline]
    successful_within = [o for o in successful if o.finished <= deadline]
    attempts = len(observations)
    drops = counters["generator_late_drops"] + counters["client_capacity_drops"]
    assert counters["scheduled"] == attempts + drops
    assert attempts == sum(outcome_counts.values())
    latency = lambda rows, origin: distribution([(o.finished - getattr(o, origin)) * 1000 for o in rows])
    return {
        "schema_version": 1, "run_id": run_id, "mode": mode, "measurement_seconds": duration,
        "total_seconds_including_drain": finished - start,
        "drain_seconds": max(0, finished - deadline), "configured_rps": rps if mode == "open" else None,
        "concurrency_limit": concurrency, "max_generator_lateness_ms": max_lateness * 1000,
        "workload": {"kinds": list(kinds), "restore_every": restore_every,
                     "description": "Unique synthetic text and payload_id per mask; no HTTP retries",
                     "request_mix": dict(Counter(o.operation for o in observations)),
                     "case_mix": dict(Counter(o.kind for o in observations)),
                     "successful_case_mix": dict(Counter(o.kind for o in successful)),
                     "successful_mask_case_mix": dict(Counter(o.kind for o in successful if o.operation == "mask")),
                     "successful_restore_case_mix": dict(Counter(o.kind for o in successful if o.operation == "restore")),
                     "request_characters": distribution([o.characters for o in observations])},
        "counts": {"scheduled": counters["scheduled"], "offered": counters["scheduled"],
                   "attempted": attempts, "started": attempts, "completed_total": attempts,
                   "started_within_window": sum(o.started < deadline for o in observations),
                   "completed_within_window": len(within),
                   "completed_during_drain": attempts - len(within),
                   "successful_total": len(successful), "successful_within_window": len(successful_within),
                   "errors": attempts - len(successful), **dict(outcome_counts),
                   "generator_late_drops": counters["generator_late_drops"],
                   "client_capacity_drops": counters["client_capacity_drops"],
                   "restore_slot_without_ready_mask": counters["restore_slot_without_ready_mask"],
                   "masks_waiting_for_restore": len(ready)},
        "status_counts": dict(statuses),
        "rates": {"offered_rps": counters["scheduled"] / duration,
                  "started_within_window_rps": sum(o.started < deadline for o in observations) / duration,
                  "completed_within_window_rps": len(within) / duration,
                  "successful_within_window_rps": len(successful_within) / duration,
                  "successful_including_drain_rps": len(successful) / (finished - start)},
        "latency_ms": {"successful_http": latency(successful, "started"),
                       "successful_from_scheduled": latency(successful, "scheduled"),
                       "all_http": latency(observations, "started"),
                       "all_from_scheduled": latency(observations, "scheduled"),
                       "client_dispatch_delay": distribution([(o.started - o.scheduled) * 1000 for o in observations]),
                       "generator_scheduling_lag": distribution(generation_lag),
                       "by_operation": {op: latency([o for o in successful if o.operation == op], "started")
                                        for op in ("mask", "restore")}},
        "failure_examples": failure_examples,
        "failure_examples_by_outcome": failure_examples_by_outcome,
        "accounting_reconciled": True,
        "notes": ["Successful RPS excludes HTTP errors, malformed responses, and incorrect output.",
                  "HTTP duration includes connection-pool wait; scheduled duration also includes dispatch delay.",
                  "Results describe this short synthetic mix, not representative accuracy or 100k-token capacity.",
                  "Model/config hashes describe the generator checkout, not proof of remote server identity."]}


def local_metadata() -> dict:
    paths = ("configs/detectors.yaml", "configs/semantic-models.json", "configs/consumers.yaml")
    manifest = json.loads((ROOT / paths[1]).read_text())
    return {"generator": {"platform": platform.platform(), "python": platform.python_version(),
                          "cpu_count": os.cpu_count()},
            "checkout_config_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths},
            "configured_models": {name: {"repository": m["repository"], "revision": m["revision"],
                                          "weight_sha256": m["files"][m["weights"]]["sha256"]}
                                  for name, m in manifest["models"].items()}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--mode", choices=("open", "closed"), default="open")
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--rps", type=float, default=1000)
    parser.add_argument("--concurrency", type=int, default=64)
    parser.add_argument("--max-lateness-ms", type=float, default=50)
    parser.add_argument("--mix", default="private,public,mixed", help="Comma-separated private,public,mixed,required (all 17 types); repetitions give weights")
    parser.add_argument("--restore-every", type=int, default=0, help="Offer a ready restoration every N mask slots; 0 disables")
    parser.add_argument("--count", type=int)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--server-metadata", type=Path, help="JSON of measured server hardware/settings")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/process_benchmark.json")
    args = parser.parse_args()

    async def execute():
        limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency)
        async with httpx.AsyncClient(base_url=args.base_url, limits=limits, timeout=args.timeout,
                                     trust_env=False) as client:
            warm_id = uuid.uuid4().hex[:12]
            for i in range(args.warmup):
                sample = sample_for(i, warm_id, tuple(args.mix.split(",")))
                response = await client.post("/process", json={"payload": sample.text, "payload_id": f"warm-{warm_id}-{i}"})
                if response.status_code != 200 or not sample.accepts_mask(response.json().get("result", "")):
                    raise RuntimeError(f"Warmup correctness failed on case {i}: HTTP {response.status_code}")
            return await run_benchmark(client, mode=args.mode, duration=args.duration, rps=args.rps,
                                       concurrency=args.concurrency, max_lateness=args.max_lateness_ms / 1000,
                                       kinds=tuple(args.mix.split(",")), restore_every=args.restore_every, count=args.count)
    report = asyncio.run(execute())
    report["metadata"] = local_metadata()
    report["metadata"]["server"] = json.loads(args.server_metadata.read_text()) if args.server_metadata else {"verified": False}
    report["warmup_requests_excluded"] = args.warmup
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "counts": report["counts"], "rates": report["rates"],
                      "successful_http_ms": report["latency_ms"]["successful_http"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
