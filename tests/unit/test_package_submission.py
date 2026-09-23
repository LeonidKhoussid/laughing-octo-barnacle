"""Submission ZIP must remain source-only."""
from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "package_submission.py"
_SPEC = importlib.util.spec_from_file_location("package_submission", _SCRIPT)
assert _SPEC and _SPEC.loader
package_submission = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(package_submission)


def test_excludes_model_archives_and_secrets_by_suffix() -> None:
    for path in (
        "models/semantic/model.onnx",
        "models/semantic/tokenizer.bin",
        "artifacts/submission.zip",
        "nested/.env.production",
        "keys/deployment.pem",
    ):
        assert package_submission._excluded(path)


def test_collect_keeps_source_but_not_generated_or_sensitive_files(tmp_path, monkeypatch) -> None:
    (tmp_path / "frontend" / "src").mkdir(parents=True)
    (tmp_path / "models").mkdir()
    (tmp_path / "frontend" / "src" / "main.tsx").write_text("export {}\n")
    (tmp_path / "frontend" / "dist").mkdir()
    (tmp_path / "frontend" / "dist" / "app.js").write_text("built")
    (tmp_path / "models" / "model.onnx").write_text("weight")
    (tmp_path / "frontend" / ".env.production").write_text("secret")
    monkeypatch.setattr(package_submission, "ROOT", tmp_path)
    monkeypatch.setattr(package_submission, "ALLOWLIST", ["frontend", "models"])

    assert [path.relative_to(tmp_path).as_posix() for path in package_submission._collect()] == [
        "frontend/src/main.tsx"
    ]


def test_placeholder_environment_is_in_source_archive():
    assert not package_submission._excluded(".env.example")
