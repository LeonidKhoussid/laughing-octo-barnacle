"""Legacy diagnostics for the AlfaGen PII Gateway.

The process/process_sustained profiles are sequential and ignore --workers.
Use scripts/benchmark_process.py for concurrent or open-loop /process load.

Usage:
    .venv/bin/python scripts/loadtest.py --profile smoke
    .venv/bin/python scripts/loadtest.py --profile uniform --rps 330 --duration 10
    .venv/bin/python scripts/loadtest.py --profile ramp
    .venv/bin/python scripts/loadtest.py --profile sustained --rps 1000 --duration 15
    .venv/bin/python scripts/loadtest.py --profile mix
    .venv/bin/python scripts/loadtest.py --profile large
    .venv/bin/python scripts/loadtest.py --profile retries

The tool can start a local uvicorn server automatically (--start-server) or
connect to an already-running one (--base-url). It uses keep-alive connections
and distinguishes closed-model load (fixed concurrency, measures max throughput)
from fixed-intensity load (rate-limited, measures latency under offered load).

The load generator is designed not to become the bottleneck: each worker owns a
dedicated keep-alive httpx.Client, and the rate limiter uses a monotonic clock.

Results are aggregated across ALL observations (never averaging per-worker
percentiles) and saved to artifacts/load_report.json.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"
ARTIFACTS.mkdir(exist_ok=True)

DEFAULT_BASE = "http://127.0.0.1:8000"
DEFAULT_KEY = os.environ.get("SUPPORT_DEMO_API_KEY", "test-support-key")

# Approximate tokenizer: ~4 chars/token, marked `estimated` (R61).
CHARS_PER_TOKEN = 4.0

# A short synthetic sample with several PII types.
SAMPLE = (
    "Клиент Иван Иванович Петров, дата рождения 12 апреля 1990 года, "
    "паспорт серия 0318 номер 123456, email ivan.petrov@example.com, "
    "телефон +7 912 345-67-89, карта 4276189074144957."
)

SAMPLE2 = (
    "Клиент Анна Сергеевна Смирнова, дата рождения 25.03.1985, "
    "адрес г. Москва, ул. Тверская, д. 15, кв. 42, ИНН 7707083893, "
    "email anna.smirnova@example.com."
)

SAMPLE3 = (
    "Клиент Пётр Алексеевич Иванов, паспорт 4503 123456, выдан ОВД района, "
    "дата выдачи 20.07.2010, телефон +7 916 555-12-34, "
    "карта 4276 1234 5678 9012, CVV 123, PIN 4321."
)

LARGE_FILLER = (
    "Это обычный текст документа без персональных данных. "
    "Здесь описывается некоторая деловая информация и контекст. "
)


def approx_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


def build_large_text(target_tokens: int = 100_000) -> str:
    parts = [SAMPLE]
    while approx_tokens("".join(parts)) < target_tokens:
        parts.append(LARGE_FILLER)
    parts.append("дата рождения 12 апреля 1990 года, адрес г. Москва, ул. Тверская, д. 15, кв. 42.")
    return "".join(parts)


@dataclass
class Observation:
    operation: str
    latency: float
    status: int
    ok: bool
    length: int = 0


@dataclass
class ProfileResult:
    name: str
    attempts: int = 0
    successes: int = 0
    errors: int = 0
    correctness_failures: int = 0
    status_429: int = 0
    status_5xx: int = 0
    timeouts: int = 0
    duration: float = 0.0
    offered_rps_value: float = 0.0
    load_model: str = "legacy_profile"
    configured_mask_start_rate: float | None = None
    mask_obs: list[float] = field(default_factory=list)
    unmask_obs: list[float] = field(default_factory=list)
    process_obs: list[float] = field(default_factory=list)
    length_mix: dict = field(default_factory=dict)
    warm: bool = False
    notes: list[str] = field(default_factory=list)

    def completed_rps(self) -> float:
        return self.successes / self.duration if self.duration > 0 else 0.0

    def attempted_rps(self) -> float:
        return self.attempts / self.duration if self.duration > 0 else 0.0

    def _percentiles(self, vals: list[float]) -> dict:
        if not vals:
            return {"count": 0, "mean": None, "p50": None, "p95": None, "p99": None}
        sv = sorted(vals)
        n = len(sv)
        return {
            "count": n,
            "mean": statistics.fmean(sv),
            "p50": sv[min(n - 1, int(0.50 * n))],
            "p95": sv[min(n - 1, int(0.95 * n))],
            "p99": sv[min(n - 1, int(0.99 * n))],
        }

    def summary(self) -> dict:
        return {
            "profile": self.name,
            "attempts": self.attempts,
            "successes": self.successes,
            "errors": self.errors,
            "correctness_failures": self.correctness_failures,
            "status_429": self.status_429,
            "status_5xx": self.status_5xx,
            "timeouts": self.timeouts,
            "duration_seconds": round(self.duration, 3),
            "offered_rps": None if self.load_model == "legacy_sequential" else round(self.offered_rps_value, 2),
            "load_model": self.load_model,
            "configured_mask_start_rate": self.configured_mask_start_rate,
            "attempted_rps": round(self.attempted_rps(), 2),
            "completed_rps": round(self.completed_rps(), 2),
            "mask_latency": self._percentiles(self.mask_obs),
            "unmask_latency": self._percentiles(self.unmask_obs),
            "process_latency": self._percentiles(self.process_obs),
            "length_mix": self.length_mix,
            "warm": self.warm,
            "notes": self.notes,
        }


class RateLimiter:
    """Fixed-intensity token bucket using a monotonic clock."""

    def __init__(self, rate_per_sec: float) -> None:
        self._rate = rate_per_sec
        self._interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self._next = time.monotonic()
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                delay = self._next - now
                self._next += self._interval
            else:
                delay = 0.0
                self._next = now + self._interval
        if delay > 0:
            time.sleep(delay)


def _merge_length_mix(mixes: list[dict]) -> dict:
    """Sum per-step length_mix buckets into one aggregate."""
    merged: dict = {}
    for m in mixes:
        for k, v in m.items():
            merged[k] = merged.get(k, 0) + v
    return merged


def _bucket_lengths(obs: list[Observation]) -> dict:
    """Bucket mask observations by approximate token length for reporting."""
    buckets = {"short": 0, "20k": 0, "large_100k": 0}
    for o in obs:
        if o.operation != "mask":
            continue
        if o.length >= 50_000:
            buckets["large_100k"] += 1
        elif o.length >= 5_000:
            buckets["20k"] += 1
        else:
            buckets["short"] += 1
    return {k: v for k, v in buckets.items() if v > 0}


class LoadGenerator:
    """Runs a profile against the service using keep-alive httpx clients."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        seed: int,
        workers: int,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.seed = seed
        self.workers = workers
        self.timeout = timeout
        self.rng = random.Random(seed)
        self._stop = threading.Event()
        self._obs: list[Observation] = []
        self._obs_lock = threading.Lock()
        self._contexts: list[tuple[str, str]] = []  # (masked, context_id)
        self._ctx_lock = threading.Lock()

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"X-API-Key": self.api_key},
        )

    def _record(self, op: str, latency: float, status: int, ok: bool, length: int = 0) -> None:
        with self._obs_lock:
            self._obs.append(Observation(op, latency, status, ok, length))

    def _mask(self, client: httpx.Client, text: str, context_id: str | None = None) -> tuple[int, str, str]:
        start = time.monotonic()
        body = {"text": text, "consumer": "support_demo"}
        if context_id is not None:
            body["context_id"] = context_id
        try:
            r = client.post(
                "/demo/mask",
                json=body,
            )
            latency = time.monotonic() - start
            ok = r.status_code == 200
            masked = r.json().get("masked_text", "") if ok else ""
            ctx = r.json().get("context_id", "") if ok else ""
            self._record("mask", latency, r.status_code, ok, approx_tokens(text))
            return r.status_code, masked, ctx
        except httpx.TimeoutException:
            self._record("mask", time.monotonic() - start, 0, False, approx_tokens(text))
            return 0, "", ""
        except Exception:
            self._record("mask", time.monotonic() - start, 0, False, approx_tokens(text))
            return 0, "", ""

    def _unmask(self, client: httpx.Client, masked: str, ctx: str) -> tuple[int, str]:
        start = time.monotonic()
        try:
            r = client.post(
                "/demo/unmask",
                json={"masked_text": masked, "consumer": "support_demo", "context_id": ctx},
            )
            latency = time.monotonic() - start
            ok = r.status_code == 200
            original = r.json().get("original_text", "") if ok else ""
            self._record("unmask", latency, r.status_code, ok)
            return r.status_code, original
        except httpx.TimeoutException:
            self._record("unmask", time.monotonic() - start, 0, False)
            return 0, ""
        except Exception:
            self._record("unmask", time.monotonic() - start, 0, False)
            return 0, ""

    def _process(self, client: httpx.Client, payload: str, payload_id: str) -> tuple[int, str]:
        """Official /process contract: {payload, payload_id} -> {result}.

        One endpoint handles both mask and demask, correlated by payload_id.
        A 200 without a valid 'result' is NOT a successful valid response.
        """
        start = time.monotonic()
        try:
            r = client.post(
                "/process",
                json={"payload": payload, "payload_id": payload_id},
            )
            latency = time.monotonic() - start
            ok = r.status_code == 200
            result = ""
            if ok:
                try:
                    body = r.json()
                except Exception:
                    body = {}
                result = body.get("result", "") if isinstance(body, dict) else ""
                # A 200 with a missing/empty result is not a valid success.
                if not isinstance(result, str) or result == "":
                    ok = False
            self._record("process", latency, r.status_code, ok, approx_tokens(payload))
            return r.status_code, result
        except httpx.TimeoutException:
            self._record("process", time.monotonic() - start, 0, False, approx_tokens(payload))
            return 0, ""
        except Exception:
            self._record("process", time.monotonic() - start, 0, False, approx_tokens(payload))
            return 0, ""

    def _prepare_contexts(self, n: int) -> None:
        """Pre-create mask contexts so unmask-heavy profiles don't measure
        'context missing' errors as work (section 14.5.5)."""
        with self._client() as client:
            for _ in range(n):
                _, masked, ctx = self._mask(client, SAMPLE)
                if masked and ctx:
                    with self._ctx_lock:
                        self._contexts.append((masked, ctx))

    def _run_worker_closed(self, fn, count_per_worker: int) -> None:
        with self._client() as client:
            for _ in range(count_per_worker):
                if self._stop.is_set():
                    return
                fn(client)

    def _run_worker_fixed(self, fn, limiter: RateLimiter, duration: float) -> None:
        with self._client() as client:
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                if self._stop.is_set():
                    return
                limiter.wait()
                fn(client)

    def run_closed(self, name: str, fn, count_per_worker: int, warm: bool = False) -> ProfileResult:
        res = ProfileResult(name=name, warm=warm)
        start = time.monotonic()
        threads = [
            threading.Thread(target=self._run_worker_closed, args=(fn, count_per_worker))
            for _ in range(self.workers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        res.duration = time.monotonic() - start
        self._finalize(res)
        return res

    def run_fixed(self, name: str, fn, rps: float, duration: float, warm: bool = False) -> ProfileResult:
        res = ProfileResult(name=name, warm=warm)
        limiter = RateLimiter(rps)
        start = time.monotonic()
        threads = [
            threading.Thread(target=self._run_worker_fixed, args=(fn, limiter, duration))
            for _ in range(self.workers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        res.duration = time.monotonic() - start
        res.offered_rps_value = rps
        self._finalize(res)
        return res

    def _finalize(self, res: ProfileResult) -> None:
        with self._obs_lock:
            obs = list(self._obs)
            self._obs.clear()
        for o in obs:
            res.attempts += 1
            if o.ok:
                res.successes += 1
            else:
                res.errors += 1
            if o.status == 429:
                res.status_429 += 1
            if o.status >= 500:
                res.status_5xx += 1
            if o.status == 0:
                res.timeouts += 1
            if o.operation == "mask":
                res.mask_obs.append(o.latency)
            elif o.operation == "process":
                res.process_obs.append(o.latency)
            else:
                res.unmask_obs.append(o.latency)
        res.length_mix = _bucket_lengths(obs)

    # -- profile operations ---------------------------------------------------

    def _op_mask(self, client: httpx.Client) -> None:
        self._mask(client, SAMPLE)

    def _op_unmask(self, client: httpx.Client) -> None:
        with self._ctx_lock:
            if not self._contexts:
                return
            masked, ctx = self._contexts[self.rng.randrange(len(self._contexts))]
        self._unmask(client, masked, ctx)

    def _op_mixed(self, client: httpx.Client) -> None:
        if self.rng.random() < 0.5:
            self._op_mask(client)
        else:
            self._op_unmask(client)

    def _op_large(self, client: httpx.Client) -> None:
        self._mask(client, build_large_text())

    def _op_mixed_length(self, client: httpx.Client) -> None:
        if self.rng.random() < 0.9:
            self._op_mask(client)
        else:
            self._mask(client, build_large_text(20_000))


def _env_info() -> dict:
    info = {
        "cpu_count": os.cpu_count(),
        "python": sys.version.split()[0],
    }
    try:
        import platform
        info["platform"] = platform.platform()
    except Exception:
        pass
    try:
        # macOS sysctl for physical memory.
        out = subprocess.run(
            ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5
        )
        if out.returncode == 0:
            info["ram_bytes"] = int(out.stdout.strip())
    except Exception:
        pass
    return info


def _start_server(port: int) -> subprocess.Popen:
    env = dict(os.environ)
    env.setdefault("SUPPORT_DEMO_API_KEY", "test-support-key")
    env.setdefault("PII_VAULT_BACKEND", "memory")
    env.setdefault("PII_WORKERS", "1")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait for readiness.
    import urllib.request
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1):
                return proc
        except Exception:
            time.sleep(0.1)
    proc.terminate()
    raise RuntimeError("server did not become ready")


def _wait_for_server(base_url: str) -> None:
    import urllib.request
    for _ in range(100):
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=1):
                return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"server not reachable at {base_url}")


def run_profile(gen: LoadGenerator, profile: str, args) -> ProfileResult:
    if profile == "smoke":
        # Sequential smoke: mask -> unmask and retries.
        res = ProfileResult(name="smoke")
        with gen._client() as client:
            start = time.monotonic()
            # mask
            st, masked, ctx = gen._mask(client, SAMPLE)
            res.attempts += 1
            if st == 200:
                res.successes += 1
            # unmask
            st2, original = gen._unmask(client, masked, ctx)
            res.attempts += 1
            if st2 == 200 and original == SAMPLE:
                res.successes += 1
            else:
                res.errors += 1
            # retry mask (same text, new context)
            st3, _, _ = gen._mask(client, SAMPLE)
            res.attempts += 1
            if st3 == 200:
                res.successes += 1
            res.duration = time.monotonic() - start
        gen._finalize(res)
        res.notes.append("sequential smoke: mask, unmask, retry")
        return res

    if profile == "process":
        # Official /process contract: {payload, payload_id} -> {result}.
        # Each element is a mask->demask pair with a distinct payload_id.
        # Counting is done ONLY by _finalize from recorded observations; the
        # profile does not increment attempts/successes manually (fixes the
        # double-counting bug). Correctness (demask restoration) is tracked
        # separately.
        res = ProfileResult(name="process", load_model="legacy_sequential")
        with gen._client() as client:
            start = time.monotonic()
            for i in range(args.count or 100):
                pid = f"load-{gen.seed}-{i}"
                st, masked = gen._process(client, SAMPLE, pid)
                if st == 200 and masked:
                    # Demask with the same payload_id and returned mask.
                    st2, original = gen._process(client, masked, pid)
                    if st2 == 200 and original != SAMPLE:
                        res.correctness_failures += 1
                else:
                    res.correctness_failures += 1
            res.duration = time.monotonic() - start
        gen._finalize(res)
        res.notes.append("Legacy sequential /process contract diagnostic; one request in flight; --workers is ignored. "
                         "Use scripts/benchmark_process.py for concurrent/open-loop throughput.")
        return res

    if profile == "process_sustained":
        # Legacy sequential diagnostic, NOT independent offered HTTP load.
        # The limiter caps mask-loop starts; sampled restores are extra calls.
        # Reuses dataset texts with distinct IDs and ignores --workers.
        # Counting is done ONLY by _finalize from recorded observations.
        res = ProfileResult(name="process_sustained", load_model="legacy_sequential",
                            configured_mask_start_rate=args.rps)
        texts = [SAMPLE, SAMPLE2, SAMPLE3]
        limiter = RateLimiter(args.rps)
        start = time.monotonic()
        stop = start + args.duration
        counter = itertools.count(gen.seed * 1000)
        with gen._client() as client:
            while time.monotonic() < stop:
                limiter.wait()
                text = texts[gen.rng.randrange(len(texts))]
                pid = f"ps-{next(counter)}"
                st, masked = gen._process(client, text, pid)
                if st == 200 and masked:
                    # Verify exact restoration for a sample of pairs.
                    if gen.rng.random() < 0.5:
                        st2, original = gen._process(client, masked, pid)
                        if st2 == 200 and original != text:
                            res.correctness_failures += 1
                else:
                    res.correctness_failures += 1
            res.duration = time.monotonic() - start
        gen._finalize(res)
        res.notes.append(
            f"Legacy sequential /process diagnostic for {args.duration}s; "
            f"configured mask-loop ceiling {args.rps}/s, not offered HTTP RPS. "
            "One request in flight; --workers is ignored; restores add calls. "
            "Use scripts/benchmark_process.py for concurrent/open-loop throughput. "
            "This result does not establish service capacity or 1000 RPS."
        )
        return res

    if profile == "uniform":
        gen._prepare_contexts(args.workers * 2)
        return gen.run_fixed("uniform", gen._op_mixed, args.rps, args.duration, warm=True)

    if profile == "ramp":
        # Ramp up to 1000 RPS with ~200 connections (organizers' described profile).
        gen._prepare_contexts(args.workers * 2)
        res = ProfileResult(name="ramp")
        steps = [(100, 3), (330, 3), (600, 3), (1000, 3)]
        for rps, dur in steps:
            # Use enough workers so the generator is not the bottleneck at the
            # peak step (~200 connections for 1000 RPS).
            gen.workers = max(args.workers, 200 if rps >= 1000 else 64)
            step = gen.run_fixed("ramp-step", gen._op_mixed, rps, dur, warm=True)
            res.attempts += step.attempts
            res.successes += step.successes
            res.errors += step.errors
            res.status_429 += step.status_429
            res.status_5xx += step.status_5xx
            res.timeouts += step.timeouts
            res.mask_obs.extend(step.mask_obs)
            res.unmask_obs.extend(step.unmask_obs)
            res.duration += step.duration
            res.notes.append(f"step {rps} rps: completed={step.completed_rps():.1f}")
        res.offered_rps_value = 1000
        res.length_mix = _bucket_lengths(obs)
        return res

    if profile == "sustained":
        gen._prepare_contexts(args.workers * 2)
        return gen.run_fixed("sustained", gen._op_mixed, args.rps, args.duration, warm=True)

    if profile == "mask_heavy":
        return gen.run_fixed("mask_heavy", gen._op_mask, args.rps, args.duration, warm=True)

    if profile == "unmask_heavy":
        gen._prepare_contexts(args.workers * 4)
        return gen.run_fixed("unmask_heavy", gen._op_unmask, args.rps, args.duration, warm=True)

    if profile == "mix":
        gen._prepare_contexts(args.workers * 2)
        return gen.run_fixed("mix", gen._op_mixed, args.rps, args.duration, warm=True)

    if profile == "large":
        # Large texts separately.
        return gen.run_fixed("large", gen._op_large, args.rps, args.duration, warm=True)

    if profile == "mixed_length":
        # Mix long with short to see starvation.
        gen._prepare_contexts(args.workers * 2)
        return gen.run_fixed("mixed_length", gen._op_mixed_length, args.rps, args.duration, warm=True)

    if profile == "retries":
        # Retries after lost response / 429 / restart / Redis unavailability.
        res = ProfileResult(name="retries")
        with gen._client() as client:
            start = time.monotonic()
            # mask then retry same text with SAME context_id (replay) -> same mask
            st, masked, ctx = gen._mask(client, SAMPLE)
            res.attempts += 1
            if st == 200:
                res.successes += 1
            st2, masked2, _ = gen._mask(client, SAMPLE, context_id=ctx)
            res.attempts += 1
            if st2 == 200 and masked2 == masked:
                res.successes += 1
            else:
                res.errors += 1
            # unmask then retry same masked -> same original
            st3, orig = gen._unmask(client, masked, ctx)
            res.attempts += 1
            if st3 == 200:
                res.successes += 1
            st4, orig2 = gen._unmask(client, masked, ctx)
            res.attempts += 1
            if st4 == 200 and orig2 == orig:
                res.successes += 1
            else:
                res.errors += 1
            res.duration = time.monotonic() - start
        gen._finalize(res)
        res.notes.append("retries: mask replay, unmask replay")
        return res

    raise ValueError(f"unknown profile: {profile}")


def main() -> int:
    parser = argparse.ArgumentParser(description="AlfaGen load test")
    parser.add_argument("--profile", default="smoke")
    parser.add_argument(
        "--profiles",
        default=None,
        help="Comma-separated list of profiles to run in one invocation "
        "(overrides --profile). Results are aggregated into one report.",
    )
    parser.add_argument("--base-url", default=os.environ.get("PII_LOAD_BASE", DEFAULT_BASE))
    parser.add_argument("--api-key", default=DEFAULT_KEY)
    parser.add_argument("--seed", type=int, default=20260922)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--rps", type=float, default=330.0)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--count", type=int, default=100, help="pairs for the 'process' profile")
    parser.add_argument("--start-server", action="store_true", help="start a local uvicorn server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--report", default=str(ARTIFACTS / "load_report.json"))
    args = parser.parse_args()

    server_proc = None
    if args.start_server:
        server_proc = _start_server(args.port)
        args.base_url = f"http://127.0.0.1:{args.port}"
    else:
        _wait_for_server(args.base_url)

    gen = LoadGenerator(args.base_url, args.api_key, args.seed, args.workers)

    profiles = args.profiles.split(",") if args.profiles else [args.profile]
    profiles = [p.strip() for p in profiles if p.strip()]

    try:
        results = []
        for prof in profiles:
            print(f"\n=== Running profile: {prof} ===")
            res = run_profile(gen, prof, args)
            results.append(res.summary())
    finally:
        if server_proc is not None:
            server_proc.terminate()
            server_proc.wait(timeout=10)

    report = {
        "tool": "scripts/loadtest.py",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "seed": args.seed,
        "profiles": profiles,
        "config": {
            "base_url": args.base_url,
            "workers": args.workers,
            "rps": args.rps,
            "duration": args.duration,
            "tokenizer": "approximate chars/4 (estimated, not organizers' tokenizer)",
        },
        "environment": _env_info(),
        "policy": "baseline-001",
        "vault_backend": os.environ.get("PII_VAULT_BACKEND", "memory"),
        "results": results,
        "note": "LOCAL DIAGNOSTIC load test, not the organizers' official formula.",
    }

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\nReport saved to {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
