"""Span resolver tests: merge, overlap, invalid coords."""
import pytest

from app.core.models import Detection, Span
from app.core.span_resolver import SpanResolutionError, resolve_spans


def _det(category, spans, detector_id="d"):
    return Detection(
        entity_id="e",
        category=category,
        sensitive_spans=tuple(Span(s, e) for s, e in spans),
        detector_id=detector_id,
    )


class TestSpanResolver:
    def test_no_overlap(self):
        dets = [_det("EMAIL", [(0, 5)]), _det("PHONE", [(10, 15)])]
        resolved = resolve_spans(dets, 20)
        assert len(resolved) == 2

    def test_merge_overlap(self):
        dets = [_det("EMAIL", [(0, 10)]), _det("PHONE", [(5, 15)])]
        resolved = resolve_spans(dets, 20)
        assert len(resolved) == 1
        assert resolved[0].span.start == 0
        assert resolved[0].span.end == 15

    def test_merge_duplicate(self):
        dets = [_det("EMAIL", [(0, 5)]), _det("EMAIL", [(0, 5)])]
        resolved = resolve_spans(dets, 10)
        assert len(resolved) == 1
        assert len(resolved[0].detector_ids) == 1

    def test_invalid_out_of_bounds(self):
        dets = [_det("EMAIL", [(0, 100)])]
        with pytest.raises(SpanResolutionError):
            resolve_spans(dets, 10)

    def test_invalid_start_ge_end(self):
        dets = [_det("EMAIL", [(5, 5)])]
        with pytest.raises(SpanResolutionError):
            resolve_spans(dets, 10)

    def test_invalid_negative_span_construction(self):
        # Negative coordinates are rejected at Span construction time.
        with pytest.raises(ValueError):
            Span(-1, 5)

    def test_empty(self):
        assert resolve_spans([], 10) == []
