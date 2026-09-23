"""C4: config-driven extensibility — add a new data type without rewriting core."""
import os
import tempfile
from pathlib import Path

import pytest

from app.core.engine import Engine
from app.detectors.registry import DetectorRegistry


def _write_config(tmp_path: Path, extra: str) -> str:
    cfg = tmp_path / "detectors.yaml"
    cfg.write_text(
        "schema_version: 1\n"
        "detector_manifest_version: test\n"
        "detectors:\n"
        "  - id: email\n"
        "    type: EMAIL\n"
        "    enabled: true\n"
        + extra,
        encoding="utf-8",
    )
    return str(cfg)


class TestConfigDrivenDetector:
    def test_new_data_type_via_config(self, tmp_path):
        """A new sensitive data type can be added purely via detectors.yaml."""
        cfg = _write_config(
            tmp_path,
            "  - id: account_number\n"
            "    type: regex\n"
            "    category: ACCOUNT_NUMBER\n"
            "    pattern: \"\\\\b\\\\d{20}\\\\b\"\n"
            "    context: '(?i)(счёт|счет|account)'\n"
            "    enabled: true\n",
        )
        registry = DetectorRegistry.from_config(cfg)
        engine = Engine(registry.detectors)
        text = "Счёт: 40817810099910004312"
        result = engine.mask(text, "ns", "ctx")
        assert "40817810099910004312" not in result.masked_text
        assert "ACCOUNT_NUMBER" in [rs.category for rs in result.resolved_spans]
        # Round-trip is exact.
        assert engine.unmask(result.masked_text, result.token_mapping) == text

    def test_disabled_config_detector_not_loaded(self, tmp_path):
        cfg = _write_config(
            tmp_path,
            "  - id: account_number\n"
            "    type: regex\n"
            "    category: ACCOUNT_NUMBER\n"
            "    pattern: \"\\\\b\\\\d{20}\\\\b\"\n"
            "    context: '(?i)(счёт|счет|account)'\n"
            "    enabled: false\n",
        )
        registry = DetectorRegistry.from_config(cfg)
        ids = {d.detector_id for d in registry.detectors}
        assert "account_number" not in ids

    def test_regex_detector_requires_category_and_pattern(self, tmp_path):
        cfg = _write_config(
            tmp_path,
            "  - id: bad\n"
            "    type: regex\n"
            "    enabled: true\n",
        )
        with pytest.raises(ValueError):
            DetectorRegistry.from_config(cfg)

    def test_unknown_detector_id_rejected(self, tmp_path):
        cfg = _write_config(
            tmp_path,
            "  - id: not_a_real_detector\n"
            "    type: SOMETHING\n"
            "    enabled: true\n",
        )
        with pytest.raises(ValueError):
            DetectorRegistry.from_config(cfg)