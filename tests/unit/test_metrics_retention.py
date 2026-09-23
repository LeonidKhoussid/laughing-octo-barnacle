"""Sustained requests must not retain an unbounded latency list."""
import pytest

from app.observability.metrics import Metrics, _HISTOGRAM_WINDOW


def test_bounded_window_preserves_lifetime_accounting_and_recent_percentiles():
    metrics = Metrics()
    for _ in range(_HISTOGRAM_WINDOW):
        metrics.observe("latency", 1.0, operation="process")
    for _ in range(_HISTOGRAM_WINDOW):
        metrics.observe("latency", 3.0, operation="process")
    result = metrics.snapshot()["histograms"]["latency/operation=process"]
    assert result["count"] == _HISTOGRAM_WINDOW * 2
    assert result["mean"] == pytest.approx(2.0)
    assert result["window_count"] == _HISTOGRAM_WINDOW
    assert result["percentile_scope"] == "most_recent_observations"
    assert result["p50"] == result["p95"] == result["p99"] == 3.0
    assert len(next(iter(metrics._histograms.values())).recent) == _HISTOGRAM_WINDOW
