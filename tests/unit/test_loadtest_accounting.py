"""Deterministic tests for loadtest accounting (review issue 1).

Verifies that attempts/successes/errors are counted exactly once per HTTP
request, that transport success and semantic correctness are distinguishable,
and that latency samples reconcile with counters.
"""
import os

os.environ["PII_VAULT_BACKEND"] = "memory"
os.environ["PII_WORKERS"] = "1"

from types import SimpleNamespace

from scripts.loadtest import LoadGenerator, ProfileResult, run_profile


class _FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json


class _FakeClient:
    """A controlled transport with scripted responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.count = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, *args, **kwargs):
        self.count += 1
        if self.count > len(self._responses):
            return _FakeResponse(500, {"detail": {"message": "unexpected"}})
        return self._responses[self.count - 1]


def _gen():
    return LoadGenerator("http://testserver", "review-key", 999, 8)


class TestCounting:
    def test_four_requests_four_attempts(self):
        """4 actual HTTP requests -> exactly 4 attempts/successes (no double-count)."""
        # mask(200) -> demask(200) for 2 pairs = 4 requests.
        responses = [
            _FakeResponse(200, {"result": "mask1"}),
            _FakeResponse(200, {"result": "orig1"}),
            _FakeResponse(200, {"result": "mask2"}),
            _FakeResponse(200, {"result": "orig2"}),
        ]
        client = _FakeClient(responses)
        gen = _gen()
        gen._client = lambda: client
        res = run_profile(gen, "process", SimpleNamespace(count=2))
        assert client.count == 4
        assert res.attempts == 4
        assert res.successes == 4
        assert res.errors == 0
        assert len(res.process_obs) == 4

    def test_http_error_counted_once(self):
        """An HTTP error is counted once, not as success. A failed mask means
        no demask for that pair."""
        responses = [
            _FakeResponse(500, {"detail": {"message": "err"}}),
        ]
        client = _FakeClient(responses)
        gen = _gen()
        gen._client = lambda: client
        res = run_profile(gen, "process", SimpleNamespace(count=1))
        assert client.count == 1  # mask failed -> no demask
        assert res.attempts == 1
        assert res.successes == 0
        assert res.errors == 1
        assert res.status_5xx == 1

    def test_429_counted_once(self):
        """A 429 is counted once and tracked separately; no demask follows."""
        responses = [
            _FakeResponse(429, {"detail": {"message": "overloaded"}}),
        ]
        client = _FakeClient(responses)
        gen = _gen()
        gen._client = lambda: client
        res = run_profile(gen, "process", SimpleNamespace(count=1))
        assert res.attempts == 1
        assert res.status_429 == 1
        assert res.successes == 0

    def test_timeout_counted_once(self):
        """A timeout (status 0) is counted once and tracked separately."""
        responses = [
            _FakeResponse(200, {"result": "mask"}),
        ]
        client = _FakeClient(responses)
        gen = _gen()
        gen._client = lambda: client
        # Force the demask (second call) to be a timeout.
        orig = gen._process
        calls = {"n": 0}

        def _timeout_process(c, payload, pid):
            calls["n"] += 1
            if calls["n"] == 2:
                gen._record("process", 0.5, 0, False, 10)
                return 0, ""
            return orig(c, payload, pid)

        gen._process = _timeout_process
        res = run_profile(gen, "process", SimpleNamespace(count=1))
        assert res.attempts == 2
        assert res.timeouts == 1
        assert res.successes == 1

    def test_http_200_malformed_not_success(self):
        """An HTTP 200 with malformed output (no 'result') is not a valid
        success; no demask follows."""
        responses = [
            _FakeResponse(200, {}),  # malformed: no result key
        ]
        client = _FakeClient(responses)
        gen = _gen()
        gen._client = lambda: client
        res = run_profile(gen, "process", SimpleNamespace(count=1))
        assert res.attempts == 1
        assert res.successes == 0
        assert res.errors == 1

    def test_incorrect_restoration_is_correctness_failure(self):
        """HTTP 200 with incorrect restoration is a correctness failure, not a
        transport success."""
        responses = [
            _FakeResponse(200, {"result": "mask"}),
            _FakeResponse(200, {"result": "WRONG-ORIGINAL"}),  # wrong restoration
        ]
        client = _FakeClient(responses)
        gen = _gen()
        gen._client = lambda: client
        res = run_profile(gen, "process", SimpleNamespace(count=1))
        # Both are transport successes (HTTP 200), but restoration is wrong.
        assert res.successes == 2
        assert res.correctness_failures == 1

    def test_latency_samples_reconcile_with_counters(self):
        """Latency samples count equals attempts (each request records one sample)."""
        responses = [
            _FakeResponse(200, {"result": "mask1"}),
            _FakeResponse(200, {"result": "orig1"}),
            _FakeResponse(200, {"result": "mask2"}),
            _FakeResponse(200, {"result": "orig2"}),
        ]
        client = _FakeClient(responses)
        gen = _gen()
        gen._client = lambda: client
        res = run_profile(gen, "process", SimpleNamespace(count=2))
        assert len(res.process_obs) == res.attempts == 4

class TestLegacyLabels:
    def test_sequential_result_does_not_claim_offered_load(self):
        result = ProfileResult(name="process_sustained", load_model="legacy_sequential",
                               configured_mask_start_rate=1000)
        result.attempts = result.successes = 100
        result.duration = 1
        report = result.summary()
        assert report["offered_rps"] is None
        assert report["configured_mask_start_rate"] == 1000
        assert report["attempted_rps"] == report["completed_rps"] == 100

    def test_process_profile_identifies_sequential_diagnostic(self):
        client = _FakeClient([_FakeResponse(500)])
        gen = _gen()
        gen._client = lambda: client
        result = run_profile(gen, "process", SimpleNamespace(count=1))
        assert result.summary()["load_model"] == "legacy_sequential"
        assert result.summary()["offered_rps"] is None
        assert "benchmark_process.py" in " ".join(result.notes)
